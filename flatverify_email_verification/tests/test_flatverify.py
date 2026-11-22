from datetime import timedelta

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models.compat import param_get, param_set
from ..models.flatverify_api import MSG_BAD_KEY, MSG_NO_KEY, MSG_PLAN, MSG_UNREACHABLE, parse_retry_after
from .common import FakeResponse, FlatverifyCase, error, job_body, result


@tagged("post_install", "-at_install", "flatverify")
class TestFlatverifyClient(FlatverifyCase):
    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after("12"), 12)
        self.assertEqual(parse_retry_after("2.5"), 2)
        self.assertEqual(parse_retry_after(None), 60)
        self.assertEqual(parse_retry_after("soon"), 60)
        self.assertEqual(parse_retry_after("0"), 1)
        self.assertEqual(parse_retry_after("999999"), 3600)

    def test_connection_shows_plan_and_speed(self):
        self.fake.add(
            "GET",
            "/account",
            FakeResponse(200, {"plan": "flat-pro", "limits": {"per_minute": 50}, "usage": {}, "free_checks_left": 0}),
        )
        settings = self.env["res.config.settings"].create({})
        action = settings.action_flatverify_test_connection()
        message = action["params"]["message"]
        self.assertIn("Flat Pro", message)
        self.assertIn("50 checks a minute", message)
        self.assertEqual(action["params"]["type"], "success")
        call = self.fake.calls[0]
        self.assertEqual(call["headers"]["Authorization"], "Bearer fv_test_key")
        self.assertNotIn("usage", message.lower())

    def test_connection_uses_key_typed_in_form(self):
        self.fake.add("GET", "/account", FakeResponse(200, {"plan": "unlimited", "limits": {"per_minute": 150}}))
        settings = self.env["res.config.settings"].create({"flatverify_api_key": "fv_typed_in_form"})
        settings.action_flatverify_test_connection()
        self.assertEqual(self.fake.calls[0]["headers"]["Authorization"], "Bearer fv_typed_in_form")

    def test_connection_without_key(self):
        param_set(self.env, "flatverify.api_key", False)
        settings = self.env["res.config.settings"].create({})
        with self.assertRaises(UserError) as caught:
            settings.action_flatverify_test_connection()
        self.assertEqual(caught.exception.args[0], MSG_NO_KEY)
        self.assertFalse(self.fake.calls)

    def test_bad_key_plain_message(self):
        self.fake.add("GET", "/account", error(401, "unauthorized", "Bad key"))
        settings = self.env["res.config.settings"].create({})
        with self.assertRaises(UserError) as caught:
            settings.action_flatverify_test_connection()
        self.assertEqual(caught.exception.args[0], MSG_BAD_KEY)

    def test_unreachable_plain_message(self):
        self.fake.add("GET", "/account", requests.ConnectionError("down"))
        settings = self.env["res.config.settings"].create({})
        with self.assertRaises(UserError) as caught:
            settings.action_flatverify_test_connection()
        self.assertEqual(caught.exception.args[0], MSG_UNREACHABLE)


@tagged("post_install", "-at_install", "flatverify")
class TestFlatverifyPartner(FlatverifyCase):
    def setUp(self):
        super().setUp()
        Partner = self.env["res.partner"]
        self.maya = Partner.create({"name": "Maya Chen", "email": "maya.chen@amazon.com"})
        self.ortiz = Partner.create({"name": "J Ortiz", "email": "j.ortiz@salesforce.com"})
        self.priya = Partner.create({"name": "Priya N", "email": "priya.n@hubspot.com"})
        self.priya_dup = Partner.create({"name": "Priya N (HubSpot)", "email": " Priya.N@hubspot.com "})
        self.shopify = Partner.create({"name": "Shopify", "is_company": True, "email": "info@shopify.com"})
        self.no_email = Partner.create({"name": "No Email"})

    # Single record

    def test_verify_button_writes_fields(self):
        self.fake.add("POST", "/verify", FakeResponse(200, result("maya.chen@amazon.com", "valid", "accepted")))
        action = self.maya.action_flatverify_verify()
        self.assertEqual(self.fake.calls[0]["json"], {"email": "maya.chen@amazon.com"})
        self.assertEqual(self.maya.flatverify_status, "valid")
        self.assertEqual(self.maya.flatverify_reason, "accepted")
        self.assertTrue(self.maya.flatverify_checked_at)
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("maya.chen@amazon.com is valid (accepted)", action["params"]["message"])

    def test_verify_role_account_is_risky(self):
        self.fake.add("POST", "/verify", FakeResponse(200, result("info@shopify.com", "risky", "role_account")))
        self.shopify.action_flatverify_verify()
        self.assertEqual(self.shopify.flatverify_status, "risky")
        self.assertEqual(self.shopify.flatverify_reason, "role_account")

    def test_unknown_status_value_maps_to_unknown(self):
        self.fake.add("POST", "/verify", FakeResponse(200, {"email": "maya.chen@amazon.com", "status": "something_new"}))
        self.maya.action_flatverify_verify()
        self.assertEqual(self.maya.flatverify_status, "unknown")

    def test_verify_without_email(self):
        with self.assertRaises(UserError):
            self.no_email.action_flatverify_verify()
        self.assertFalse(self.fake.calls)

    def test_402_plain_message(self):
        self.fake.add("POST", "/verify", error(402, "payment_required", "Out of checks"))
        with self.assertRaises(UserError) as caught:
            self.maya.action_flatverify_verify()
        self.assertEqual(caught.exception.args[0], MSG_PLAN)
        self.assertEqual(MSG_PLAN, "Your Flatverify plan needs attention. Open app.flatverify.com/billing.")

    def test_invalid_email_400(self):
        self.maya.email = "maya.chen@@amazon.com"
        self.fake.add("POST", "/verify", error(400, "invalid_email", "Not an email address."))
        self.maya.action_flatverify_verify()
        self.assertEqual(self.maya.flatverify_status, "invalid")
        self.assertEqual(self.maya.flatverify_reason, "bad_syntax")

    def test_429_on_verify_queues_and_respects_retry_after(self):
        self.fake.add("POST", "/verify", error(429, "rate_limited", "Slow down", {"Retry-After": "7"}))
        before = fields.Datetime.now()
        action = self.maya.action_flatverify_verify()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("7 seconds", action["params"]["message"])
        self.assertTrue(self.maya.flatverify_queued)
        self.assertTrue(self.maya.flatverify_checking)
        self.assertFalse(self.maya.flatverify_status)
        self.assertTrue(self.Job._flatverify_queue_paused())
        until = fields.Datetime.to_datetime(param_get(self.env, "flatverify.queue_not_before"))
        self.assertGreaterEqual(until, before + timedelta(seconds=6))
        self.assertLessEqual(until, before + timedelta(seconds=9))
        # While paused, the queue sends nothing.
        self.Job._flatverify_run_once()
        self.assertEqual(len(self.fake.calls), 1)

    def test_503_on_verify_queues(self):
        self.fake.add("POST", "/verify", error(503, "busy", "Busy", {"Retry-After": "20"}))
        action = self.maya.action_flatverify_verify()
        self.assertIn("20 seconds", action["params"]["message"])
        self.assertTrue(self.maya.flatverify_queued)

    # Bulk

    def _all(self):
        return self.maya | self.ortiz | self.priya | self.priya_dup | self.shopify | self.no_email

    def test_bulk_sends_one_job(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=4)))
        action = self._all().action_flatverify_bulk()
        calls = self.fake.calls_to("POST", "/verify/bulk")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(
            calls[0]["json"]["emails"],
            ["info@shopify.com", "j.ortiz@salesforce.com", "maya.chen@amazon.com", "priya.n@hubspot.com"],
        )
        self.assertTrue(calls[0]["json"]["name"].startswith("Odoo "))
        job = self.Job.search([])
        self.assertEqual(len(job), 1)
        self.assertEqual(job.remote_id, "job_1")
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.record_count, 5)
        self.assertEqual(self.priya_dup.flatverify_job_id, job)
        self.assertFalse(self.no_email.flatverify_job_id)
        self.assertTrue(self.maya.flatverify_checking)
        self.assertEqual(action["params"]["type"], "success")

    def test_bulk_without_any_email(self):
        with self.assertRaises(UserError):
            self.no_email.action_flatverify_bulk()

    def test_bulk_402_rolls_back_cleanly(self):
        self.fake.add("POST", "/verify/bulk", error(402, "payment_required", "Out of checks"))
        with self.assertRaises(UserError) as caught:
            self._all().action_flatverify_bulk()
        self.assertEqual(caught.exception.args[0], MSG_PLAN)

    def test_button_on_several_records_uses_bulk(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=2)))
        (self.maya | self.ortiz).action_flatverify_verify()
        self.assertEqual(len(self.fake.calls_to("POST", "/verify/bulk")), 1)
        self.assertFalse(self.fake.calls_to("POST", "/verify"))

    def test_cron_polls_and_pages_results(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=4)))
        self._all().action_flatverify_bulk()
        self.fake.add("GET", "/jobs/job_1", FakeResponse(200, job_body("job_1", "running", total=4, processed=2)))
        self.Job._flatverify_run_once()
        job = self.Job.search([])
        self.assertEqual(job.state, "running")
        self.assertEqual(job.processed, 2)
        self.assertFalse(self.fake.calls_to("GET", "/jobs/job_1/results"))

        self.fake.routes[("GET", "/jobs/job_1")] = [FakeResponse(200, job_body("job_1", "done", total=4, processed=4))]

        def results(_json, params):
            if "after" not in params:
                return FakeResponse(
                    200,
                    {
                        "results": [
                            result("info@shopify.com", "risky", "role_account"),
                            result("j.ortiz@salesforce.com", "invalid", "mailbox_not_found"),
                        ],
                        "next_after": 1,
                    },
                )
            self.assertEqual(params["after"], 1)
            return FakeResponse(
                200,
                {
                    "results": [
                        result("maya.chen@amazon.com", "valid", "accepted"),
                        result("priya.n@hubspot.com", "risky", "catch_all"),
                    ],
                    "next_after": None,
                },
            )

        self.fake.add("GET", "/jobs/job_1/results", results)
        self.Job._flatverify_run_once()
        pages = self.fake.calls_to("GET", "/jobs/job_1/results")
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["params"], {"format": "json"})
        self.assertEqual(pages[1]["params"], {"format": "json", "after": 1})
        self.assertEqual(job.state, "done")
        self.assertTrue(job.finished_at)
        self.assertEqual((self.maya.flatverify_status, self.maya.flatverify_reason), ("valid", "accepted"))
        self.assertEqual(self.ortiz.flatverify_status, "invalid")
        self.assertEqual(self.ortiz.flatverify_reason, "mailbox_not_found")
        self.assertEqual(self.shopify.flatverify_reason, "role_account")
        self.assertEqual(self.priya.flatverify_reason, "catch_all")
        self.assertEqual(self.priya_dup.flatverify_status, "risky")
        for partner in self.maya | self.ortiz | self.priya | self.priya_dup | self.shopify:
            self.assertTrue(partner.flatverify_checked_at)
            self.assertFalse(partner.flatverify_job_id)
            self.assertFalse(partner.flatverify_checking)
        # A done job is not polled again.
        count = len(self.fake.calls)
        self.Job._flatverify_run_once()
        self.assertEqual(len(self.fake.calls), count)

    def test_poll_respects_retry_after(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=1)))
        self.maya.action_flatverify_bulk()
        self.fake.add("GET", "/jobs/job_1", error(503, "busy", "Busy", {"Retry-After": "30"}))
        before = fields.Datetime.now()
        self.Job._flatverify_run_once()
        job = self.Job.search([])
        self.assertEqual(job.state, "queued")
        self.assertGreaterEqual(job.next_try_at, before + timedelta(seconds=29))
        self.assertIn("busy", job.message.lower())
        self.Job._flatverify_run_once()
        self.assertEqual(len(self.fake.calls_to("GET", "/jobs/job_1")), 1)
        # Once the time has passed, polling goes on.
        job.next_try_at = fields.Datetime.now() - timedelta(seconds=1)
        self.fake.routes[("GET", "/jobs/job_1")] = [FakeResponse(200, job_body("job_1", "running", total=1))]
        self.Job._flatverify_run_once()
        self.assertEqual(len(self.fake.calls_to("GET", "/jobs/job_1")), 2)
        self.assertFalse(job.next_try_at)

    def test_too_many_jobs_waits_then_submits(self):
        self.fake.add(
            "POST",
            "/verify/bulk",
            error(429, "too_many_jobs", "Wait for one to finish"),
            FakeResponse(202, job_body("job_2", "queued", total=1)),
        )
        before = fields.Datetime.now()
        action = self.maya.action_flatverify_bulk()
        job = self.Job.search([])
        self.assertEqual(job.state, "pending")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertGreaterEqual(job.next_try_at, before + timedelta(seconds=59))
        self.Job._flatverify_run_once()
        self.assertEqual(len(self.fake.calls_to("POST", "/verify/bulk")), 1)
        job.next_try_at = fields.Datetime.now() - timedelta(seconds=1)
        self.fake.add("GET", "/jobs/job_2", FakeResponse(200, job_body("job_2", "queued", total=1)))
        self.Job._flatverify_run_once()
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.remote_id, "job_2")

    def test_job_gone_is_stopped(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=1)))
        self.maya.action_flatverify_bulk()
        self.fake.add("GET", "/jobs/job_1", error(404, "not_found", "No job with this id."))
        self.Job._flatverify_run_once()
        self.assertEqual(self.Job.search([]).state, "failed")
        self.assertFalse(self.maya.flatverify_checking)

    def test_bad_syntax_without_result(self):
        broken = self.env["res.partner"].create({"name": "Typo", "email": "maya.chen.amazon.com"})
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=1)))
        (broken | self.maya).action_flatverify_bulk()
        self.fake.add("GET", "/jobs/job_1", FakeResponse(200, job_body("job_1", "done", total=1, processed=1)))
        self.fake.add(
            "GET",
            "/jobs/job_1/results",
            FakeResponse(200, {"results": [result("maya.chen@amazon.com", "valid", "accepted")], "next_after": None}),
        )
        self.Job._flatverify_run_once()
        self.assertEqual((broken.flatverify_status, broken.flatverify_reason), ("invalid", "bad_syntax"))
        self.assertEqual(self.maya.flatverify_status, "valid")

    def test_email_changed_during_job_is_not_overwritten(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=1)))
        self.maya.action_flatverify_bulk()
        self.maya.email = "maya@amazon.com"
        self.assertFalse(self.maya.flatverify_job_id)
        self.fake.add("GET", "/jobs/job_1", FakeResponse(200, job_body("job_1", "done", total=1, processed=1)))
        self.fake.add(
            "GET",
            "/jobs/job_1/results",
            FakeResponse(200, {"results": [result("maya.chen@amazon.com", "invalid", "mailbox_not_found")], "next_after": None}),
        )
        self.Job._flatverify_run_once()
        self.assertFalse(self.maya.flatverify_status)

    def test_server_action_runs_one_job(self):
        action = self.env.ref("flatverify_email_verification.action_flatverify_verify_partners")
        self.assertEqual(action.binding_model_id.model, "res.partner")
        self.assertIn("list", action.binding_view_types)
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_1", "queued", total=3)))
        records = self.maya | self.ortiz | self.priya
        result_action = action.with_context(
            active_model="res.partner", active_ids=records.ids, active_id=records.ids[0]
        ).run()
        self.assertEqual(len(self.fake.calls_to("POST", "/verify/bulk")), 1)
        self.assertEqual(result_action["tag"], "display_notification")
        self.assertEqual(len(self.Job.search([])), 1)

    # Automatic checks

    def test_auto_verify_queues_on_create_without_http(self):
        self.set_auto(True)
        partner = self.env["res.partner"].create({"name": "New Lead", "email": "new.lead@microsoft.com"})
        self.assertTrue(partner.flatverify_queued)
        self.assertFalse(self.fake.calls, "saving must never wait for a check")
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_9", "queued", total=1)))
        self.fake.add("GET", "/jobs/job_9", FakeResponse(200, job_body("job_9", "queued", total=1)))
        self.Job._flatverify_run_once()
        calls = self.fake.calls_to("POST", "/verify/bulk")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["emails"], ["new.lead@microsoft.com"])
        self.assertFalse(partner.flatverify_queued)
        self.assertEqual(partner.flatverify_job_id.remote_id, "job_9")

    def test_auto_verify_batches_many_creates_into_one_job(self):
        self.set_auto(True)
        self.env["res.partner"].create(
            [{"name": "A", "email": "a.one@google.com"}, {"name": "B", "email": "b.two@apple.com"}]
        )
        self.env["res.partner"].create({"name": "C", "email": "c.three@stripe.com"})
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_9", "queued", total=3)))
        self.fake.add("GET", "/jobs/job_9", FakeResponse(200, job_body("job_9", "queued", total=3)))
        self.Job._flatverify_run_once()
        calls = self.fake.calls_to("POST", "/verify/bulk")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["emails"], ["a.one@google.com", "b.two@apple.com", "c.three@stripe.com"])

    def test_auto_off_does_not_queue(self):
        partner = self.env["res.partner"].create({"name": "New Lead", "email": "new.lead@microsoft.com"})
        self.assertFalse(partner.flatverify_queued)

    def test_email_change_resets_and_queues(self):
        self.maya._flatverify_apply_result(result("maya.chen@amazon.com", "valid", "accepted"))
        self.assertEqual(self.maya.flatverify_status, "valid")
        self.assertFalse(self.maya.flatverify_queued, "writing a result never queues")
        self.set_auto(True)
        self.maya.write({"name": "Maya Chen (Amazon)"})
        self.assertEqual(self.maya.flatverify_status, "valid", "other fields keep the result")
        self.maya.write({"email": "MAYA.CHEN@amazon.com"})
        self.assertEqual(self.maya.flatverify_status, "valid", "same address in other case keeps the result")
        self.maya.write({"email": "m.chen@amazon.com"})
        self.assertFalse(self.maya.flatverify_status)
        self.assertFalse(self.maya.flatverify_reason)
        self.assertFalse(self.maya.flatverify_checked_at)
        self.assertTrue(self.maya.flatverify_queued)
        self.assertFalse(self.fake.calls)

    def test_email_removed_clears_without_queue(self):
        self.set_auto(True)
        self.maya._flatverify_apply_result(result("maya.chen@amazon.com", "valid", "accepted"))
        self.maya.write({"email": False})
        self.assertFalse(self.maya.flatverify_status)
        self.assertFalse(self.maya.flatverify_queued)

    def test_copy_starts_unchecked(self):
        self.maya._flatverify_apply_result(result("maya.chen@amazon.com", "valid", "accepted"))
        copy = self.maya.copy()
        self.assertFalse(copy.flatverify_status)

    # Views

    def test_views_have_button_columns_and_filters(self):
        Partner = self.env["res.partner"]
        views = Partner.get_views([(False, "form"), (False, "search")])
        form = views["views"]["form"]["arch"]
        search = views["views"]["search"]["arch"]
        self.assertIn("action_flatverify_verify", form)
        self.assertIn("flatverify_status", form)
        for name in ("flatverify_valid", "flatverify_invalid", "flatverify_risky"):
            self.assertIn(name, search)
        self.assertEqual(Partner.search_count([("id", "=", self.maya.id), ("flatverify_status", "=", "valid")]), 0)
        self.maya._flatverify_apply_result(result("maya.chen@amazon.com", "valid", "accepted"))
        self.assertEqual(Partner.search([("flatverify_status", "=", "valid")]), self.maya)

    def test_models_with_fields(self):
        self.assertIn("res.partner", self.Job._flatverify_models())
