"""One live end to end check against the real Flatverify API.

Runs only with an internal QA key in FLATVERIFY_QA_KEY (or /root/.secrets/flatverify-qa-key).
Without a key the test is skipped. Addresses are only on a domain we control.
"""
import os
import time

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, _super_send

from ..models.compat import param_set

QA_KEY_FILE = "/root/.secrets/flatverify-qa-key"


def _qa_key():
    key = os.environ.get("FLATVERIFY_QA_KEY", "").strip()
    if not key and os.path.isfile(QA_KEY_FILE):
        with open(QA_KEY_FILE, encoding="utf-8") as handle:
            key = handle.read().strip()
    return key


@tagged("post_install", "-at_install", "flatverify", "flatverify_live")
class TestFlatverifyLive(TransactionCase):
    @classmethod
    def _request_handler(cls, s, r, /, **kw):
        # Odoo blocks outgoing HTTP in tests. This one test may reach the Flatverify API host only.
        if r.url.startswith("https://app.flatverify.com/"):
            return _super_send(s, r, **kw)
        return super()._request_handler(s, r, **kw)

    def setUp(self):
        super().setUp()
        key = _qa_key()
        if not key:
            self.skipTest("no Flatverify QA key, live E2E skipped")
        param_set(self.env, "flatverify.api_key", key)
        param_set(self.env, "flatverify.base_url", "https://app.flatverify.com/api/v1")

    def test_live_single_and_bulk(self):
        settings = self.env["res.config.settings"].create({})
        action = settings.action_flatverify_test_connection()
        self.assertIn("Connected", action["params"]["message"])

        Partner = self.env["res.partner"]
        support = Partner.create({"name": "Argorant Support", "email": "support@argorant.com"})
        support.action_flatverify_verify()
        self.assertEqual(support.flatverify_status, "valid")

        nobody = Partner.create({"name": "Nobody", "email": "nobody-e2e-7731@argorant.com"})
        nobody.action_flatverify_bulk()
        job = nobody.flatverify_job_id
        self.assertTrue(job.remote_id)
        deadline = time.time() + 180
        while job.state != "done" and time.time() < deadline:
            time.sleep(5)
            job.next_try_at = False
            job._flatverify_poll()
        self.assertEqual(job.state, "done")
        self.assertIn(nobody.flatverify_status, ("invalid", "risky"))
