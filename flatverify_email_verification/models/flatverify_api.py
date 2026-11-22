"""Small client for the Flatverify REST API (https://flatverify.com/docs).

Every call goes out from the Odoo server with the API key from the settings.
Errors become plain messages that can be shown to the user as they are.
"""
import logging

import requests

from odoo.exceptions import UserError

from .compat import param_get

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://app.flatverify.com/api/v1"
TIMEOUT = 30
USER_AGENT = "Flatverify-Odoo/1.0.0"
MAX_JOB_EMAILS = 200000
DEFAULT_RETRY_AFTER = 60

MSG_NO_KEY = (
    "Add your Flatverify API key first: Settings, Flatverify. "
    "You find the key in app.flatverify.com, Settings, API keys."
)
MSG_BAD_KEY = (
    "Flatverify did not accept this API key. "
    "Create a new one in app.flatverify.com, Settings, API keys, and paste it in Settings, Flatverify."
)
MSG_PLAN = "Your Flatverify plan needs attention. Open app.flatverify.com/billing."
MSG_UNREACHABLE = "Flatverify could not be reached right now. Try again in a minute."
MSG_BUSY = "Flatverify is busy right now."

PLAN_LABELS = {
    "flat": "Flat",
    "flat-pro": "Flat Pro",
    "unlimited": "Unlimited",
    "creator": "Creator",
    "trial": "Free trial",
    "none": "No plan",
}


class FlatverifyError(UserError):
    """A Flatverify call failed. The message is plain and safe to show."""

    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


class FlatverifyBusy(FlatverifyError):
    """429 rate_limited, 429 too_many_jobs or 503 busy. Try again after retry_after seconds."""

    def __init__(self, message, code=None, status=None, retry_after=DEFAULT_RETRY_AFTER):
        super().__init__(message, code=code, status=status)
        self.retry_after = retry_after


def parse_retry_after(value, default=DEFAULT_RETRY_AFTER):
    """Seconds from a Retry-After header. Bad or missing values fall back to the default."""
    try:
        seconds = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(1, min(seconds, 3600))


def plan_label(plan):
    return PLAN_LABELS.get(plan or "none", (plan or "").replace("-", " ").title() or "No plan")


class FlatverifyClient:
    def __init__(self, api_key, base_url=None):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def from_env(cls, env):
        return cls(
            param_get(env, "flatverify.api_key"),
            param_get(env, "flatverify.base_url") or DEFAULT_BASE_URL,
        )

    # Low level

    def _request(self, method, path, payload=None, params=None):
        if not self.api_key:
            raise FlatverifyError(MSG_NO_KEY, code="no_key")
        headers = {
            "Authorization": "Bearer %s" % self.api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            resp = requests.request(
                method,
                self.base_url + path,
                json=payload,
                params=params,
                headers=headers,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            _logger.warning("Flatverify request %s %s failed: %s", method, path, exc.__class__.__name__)
            raise FlatverifyError(MSG_UNREACHABLE, code="unreachable") from None

        status = resp.status_code
        if status < 400:
            try:
                return resp.json()
            except ValueError:
                raise FlatverifyError(MSG_UNREACHABLE, code="bad_response", status=status) from None

        code, message = self._error_body(resp)
        if status in (429, 503):
            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            text = MSG_BUSY
            if code == "too_many_jobs":
                text = "Flatverify is still working on earlier lists for this account."
            raise FlatverifyBusy(
                "%s The check starts again in about %s seconds." % (text, retry_after),
                code=code or ("busy" if status == 503 else "rate_limited"),
                status=status,
                retry_after=retry_after,
            )
        if status == 402:
            raise FlatverifyError(MSG_PLAN, code="payment_required", status=status)
        if status in (401, 403):
            raise FlatverifyError(MSG_BAD_KEY, code=code or "unauthorized", status=status)
        if status == 404:
            raise FlatverifyError(message or "Flatverify does not know this job.", code=code or "not_found", status=status)
        if status in (400, 413, 422):
            raise FlatverifyError(message or "Flatverify could not read this request.", code=code, status=status)
        raise FlatverifyError(MSG_UNREACHABLE, code=code or "server_error", status=status)

    @staticmethod
    def _error_body(resp):
        try:
            err = (resp.json() or {}).get("error") or {}
        except (ValueError, AttributeError):
            return None, None
        if not isinstance(err, dict):
            return None, None
        return err.get("code"), err.get("message")

    # Endpoints

    def account(self):
        return self._request("GET", "/account")

    def verify(self, email):
        return self._request("POST", "/verify", payload={"email": email})

    def bulk(self, emails, name=None):
        payload = {"emails": list(emails)}
        if name:
            payload["name"] = name
        return self._request("POST", "/verify/bulk", payload=payload)

    def job(self, job_id):
        return self._request("GET", "/jobs/%s" % job_id)

    def results(self, job_id, after=None):
        params = {"format": "json"}
        if after is not None and after >= 0:
            params["after"] = after
        return self._request("GET", "/jobs/%s/results" % job_id, params=params)
