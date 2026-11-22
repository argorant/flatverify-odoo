from odoo.tests import tagged

from odoo.addons.flatverify_email_verification.tests.common import FakeResponse, FlatverifyCase, job_body, result


@tagged("post_install", "-at_install", "flatverify")
class TestFlatverifyCrm(FlatverifyCase):
    def setUp(self):
        super().setUp()
        Lead = self.env["crm.lead"]
        self.lead = Lead.create({"name": "Amazon rollout", "type": "lead", "email_from": "maya.chen@amazon.com"})
        self.opp = Lead.create({"name": "Salesforce renewal", "type": "opportunity", "email_from": "j.ortiz@salesforce.com"})

    def test_lead_uses_email_from(self):
        self.assertEqual(self.lead._flatverify_email_field, "email_from")
        self.assertIn("crm.lead", self.Job._flatverify_models())

    def test_verify_button_on_lead(self):
        self.fake.add("POST", "/verify", FakeResponse(200, result("maya.chen@amazon.com", "valid", "accepted")))
        self.lead.action_flatverify_verify()
        self.assertEqual(self.fake.calls[0]["json"], {"email": "maya.chen@amazon.com"})
        self.assertEqual(self.lead.flatverify_status, "valid")
        self.assertEqual(self.lead.flatverify_reason, "accepted")
        self.assertTrue(self.lead.flatverify_checked_at)

    def test_bulk_leads_one_job_and_write_back(self):
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_l", "queued", total=2)))
        (self.lead | self.opp).action_flatverify_bulk()
        calls = self.fake.calls_to("POST", "/verify/bulk")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["emails"], ["j.ortiz@salesforce.com", "maya.chen@amazon.com"])
        self.fake.add("GET", "/jobs/job_l", FakeResponse(200, job_body("job_l", "done", total=2, processed=2)))
        self.fake.add(
            "GET",
            "/jobs/job_l/results",
            FakeResponse(
                200,
                {
                    "results": [
                        result("j.ortiz@salesforce.com", "invalid", "mailbox_not_found"),
                        result("maya.chen@amazon.com", "risky", "catch_all"),
                    ],
                    "next_after": None,
                },
            ),
        )
        self.Job._flatverify_run_once()
        self.assertEqual((self.opp.flatverify_status, self.opp.flatverify_reason), ("invalid", "mailbox_not_found"))
        self.assertEqual((self.lead.flatverify_status, self.lead.flatverify_reason), ("risky", "catch_all"))

    def test_auto_verify_on_lead_create_and_change(self):
        self.set_auto(True)
        lead = self.env["crm.lead"].create({"name": "HubSpot pilot", "email_from": "priya.n@hubspot.com"})
        self.assertTrue(lead.flatverify_queued)
        self.assertFalse(self.fake.calls)
        lead._flatverify_apply_result(result("priya.n@hubspot.com", "valid", "accepted"))
        lead.write({"email_from": "p.n@hubspot.com"})
        self.assertFalse(lead.flatverify_status)
        self.assertTrue(lead.flatverify_queued)

    def test_server_action_on_leads(self):
        action = self.env.ref("flatverify_crm.action_flatverify_verify_leads")
        self.fake.add("POST", "/verify/bulk", FakeResponse(202, job_body("job_l", "queued", total=2)))
        records = self.lead | self.opp
        action.with_context(active_model="crm.lead", active_ids=records.ids, active_id=records.ids[0]).run()
        self.assertEqual(len(self.fake.calls_to("POST", "/verify/bulk")), 1)

    def test_lead_views(self):
        views = self.env["crm.lead"].get_views([(False, "form"), (False, "search")])
        self.assertIn("action_flatverify_verify", views["views"]["form"]["arch"])
        self.assertIn("flatverify_risky", views["views"]["search"]["arch"])
