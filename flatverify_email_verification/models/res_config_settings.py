from odoo import _, fields, models

from .compat import param_get
from .flatverify_api import DEFAULT_BASE_URL, FlatverifyClient, plan_label


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    flatverify_api_key = fields.Char(
        string="Flatverify API key",
        config_parameter="flatverify.api_key",
        help="Create a key in app.flatverify.com, Settings, API keys.",
    )
    flatverify_auto_verify = fields.Boolean(
        string="Verify new and changed emails automatically",
        config_parameter="flatverify.auto_verify",
    )

    def action_flatverify_test_connection(self):
        """GET /account with the key in the form (or the saved one) and show plan and speed."""
        self.ensure_one()
        key = self.flatverify_api_key or param_get(self.env, "flatverify.api_key")
        client = FlatverifyClient(key, param_get(self.env, "flatverify.base_url") or DEFAULT_BASE_URL)
        data = client.account()
        plan = data.get("plan") or "none"
        per_minute = (data.get("limits") or {}).get("per_minute") or 0
        if plan == "none":
            message = _("Connected. This account has no plan yet. Open app.flatverify.com/billing to pick one.")
            kind = "warning"
        else:
            message = _("Connected. Plan %(plan)s, %(speed)s checks a minute.") % {
                "plan": plan_label(plan),
                "speed": per_minute,
            }
            kind = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Flatverify"), "message": message, "type": kind, "sticky": False},
        }
