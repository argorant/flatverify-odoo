"""Shared test helpers. Every HTTP call is answered by FakeFlatverify; nothing leaves the machine."""
from unittest.mock import patch

from odoo.tests.common import TransactionCase

from ..models.compat import param_set

API = "https://app.flatverify.com/api/v1"
TEST_KEY = "fv_test_key"


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def error(status, code, message="", headers=None):
    return FakeResponse(status, {"error": {"code": code, "message": message}}, headers)


class FakeFlatverify:
    """Stands in for requests.request. Routes by (method, path) and records every call."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def add(self, method, path, *responses):
        self.routes.setdefault((method, path), []).extend(responses)

    def calls_to(self, method, path):
        return [c for c in self.calls if c["method"] == method and c["path"] == path]

    def __call__(self, method, url, json=None, params=None, headers=None, timeout=None, **kwargs):
        assert url.startswith(API), "unexpected host %s" % url
        path = url[len(API):]
        self.calls.append({"method": method, "path": path, "json": json, "params": params, "headers": headers})
        queue = self.routes.get((method, path))
        if not queue:
            raise AssertionError("unexpected call %s %s" % (method, path))
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(json, params)
        return response


def job_body(job_id="job_1", status="queued", total=0, processed=0):
    return {
        "id": job_id,
        "name": "Odoo",
        "source": "api",
        "status": status,
        "waiting_reason": None,
        "total": total,
        "processed": processed,
        "counts": {"valid": 0, "invalid": 0, "risky": 0, "unknown": 0},
        "created_at": "2026-11-22T10:00:00Z",
        "finished_at": None,
        "skipped": 0,
    }


def result(email, status, sub_status):
    return {
        "email": email,
        "status": status,
        "sub_status": sub_status,
        "reason": None,
        "disposable": False,
        "role": sub_status == "role_account",
        "free": False,
        "catch_all": sub_status == "catch_all",
    }


class FlatverifyCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        param_set(cls.env, "flatverify.api_key", TEST_KEY)
        param_set(cls.env, "flatverify.base_url", API)
        param_set(cls.env, "flatverify.auto_verify", False)
        param_set(cls.env, "flatverify.queue_not_before", False)

    def setUp(self):
        super().setUp()
        self.fake = FakeFlatverify()
        patcher = patch(
            "odoo.addons.flatverify_email_verification.models.flatverify_api.requests.request",
            side_effect=self.fake,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.Job = self.env["flatverify.job"]

    def set_auto(self, on=True):
        param_set(self.env, "flatverify.auto_verify", "True" if on else False)
