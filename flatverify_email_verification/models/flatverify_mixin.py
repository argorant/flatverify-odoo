from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .compat import param_bool, param_get
from .flatverify_api import FlatverifyBusy, FlatverifyClient, FlatverifyError, MSG_NO_KEY

STATUS_SELECTION = [
    ("valid", "Valid"),
    ("invalid", "Invalid"),
    ("risky", "Risky"),
    ("unknown", "Unknown"),
]
STATUS_KEYS = {key for key, _label in STATUS_SELECTION}


def normalize_email(value):
    return (value or "").strip().lower()


def looks_like_email(value):
    value = normalize_email(value)
    if value.count("@") != 1 or " " in value:
        return False
    local, domain = value.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


class FlatverifyMixin(models.AbstractModel):
    """Adds Flatverify fields and actions to any model with an email field.

    Inheriting models set _flatverify_email_field when the email field is not called "email".
    """

    _name = "flatverify.mixin"
    _description = "Flatverify email verification"
    _flatverify_email_field = "email"

    flatverify_status = fields.Selection(
        STATUS_SELECTION,
        string="Flatverify Status",
        copy=False,
        index=True,
        readonly=True,
        help="Result of the last Flatverify check of this email address.",
    )
    flatverify_reason = fields.Char(
        string="Flatverify Reason",
        copy=False,
        readonly=True,
        help="One word reason from Flatverify, for example accepted, catch_all, role_account or mailbox_not_found.",
    )
    flatverify_checked_at = fields.Datetime(string="Flatverify Checked", copy=False, readonly=True)
    flatverify_queued = fields.Boolean(string="Flatverify Queued", copy=False, index=True, readonly=True)
    flatverify_job_id = fields.Many2one(
        "flatverify.job", string="Flatverify Job", copy=False, index=True, ondelete="set null", readonly=True
    )
    flatverify_checking = fields.Boolean(string="Flatverify Checking", compute="_compute_flatverify_checking")

    @api.depends("flatverify_queued", "flatverify_job_id.state")
    def _compute_flatverify_checking(self):
        open_states = self.env["flatverify.job"]._flatverify_open_states()
        for record in self:
            record.flatverify_checking = bool(
                record.flatverify_queued or (record.flatverify_job_id and record.flatverify_job_id.state in open_states)
            )

    # Helpers

    def _flatverify_email(self):
        self.ensure_one()
        return (self[self._flatverify_email_field] or "").strip()

    @api.model
    def _flatverify_auto_enabled(self):
        return param_bool(self.env, "flatverify.auto_verify") and bool(param_get(self.env, "flatverify.api_key"))

    @api.model
    def _flatverify_trigger_cron(self):
        cron = self.env.ref("flatverify_email_verification.ir_cron_flatverify", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _flatverify_notify(self, message, kind="info", reload=True):
        params = {"title": _("Flatverify"), "message": message, "type": kind, "sticky": False}
        if reload:
            params["next"] = {"type": "ir.actions.client", "tag": "soft_reload"}
        return {"type": "ir.actions.client", "tag": "display_notification", "params": params}

    @api.model
    def _flatverify_result_vals(self, result):
        status = (result or {}).get("status")
        if status not in STATUS_KEYS:
            status = "unknown"
        return {
            "flatverify_status": status,
            "flatverify_reason": (result or {}).get("sub_status") or False,
            "flatverify_checked_at": fields.Datetime.now(),
            "flatverify_queued": False,
            "flatverify_job_id": False,
        }

    def _flatverify_write(self, vals):
        """Write Flatverify fields only. Never resets or queues anything."""
        return self.sudo().with_context(flatverify_writeback=True).write(vals)

    def _flatverify_apply_result(self, result):
        return self._flatverify_write(self._flatverify_result_vals(result))

    # ORM: changed emails reset the result and, when the setting is on, queue a background check.

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("flatverify_writeback") and self._flatverify_auto_enabled():
            todo = records.filtered(lambda r: r._flatverify_email())
            if todo:
                todo._flatverify_write({"flatverify_queued": True})
                self._flatverify_trigger_cron()
        return records

    def write(self, vals):
        field = self._flatverify_email_field
        if field not in vals or self.env.context.get("flatverify_writeback"):
            return super().write(vals)
        new_email = normalize_email(vals.get(field))
        changed = self.filtered(lambda r: normalize_email(r[field]) != new_email)
        result = super().write(vals)
        if changed:
            queue = bool(new_email) and self._flatverify_auto_enabled()
            changed._flatverify_write(
                {
                    "flatverify_status": False,
                    "flatverify_reason": False,
                    "flatverify_checked_at": False,
                    "flatverify_job_id": False,
                    "flatverify_queued": queue,
                }
            )
            if queue:
                self._flatverify_trigger_cron()
        return result

    # Actions

    def action_flatverify_verify(self):
        """Verify email button. One record is checked right away, several go out as one bulk job."""
        if len(self) != 1:
            return self.action_flatverify_bulk()
        record = self
        email = record._flatverify_email()
        if not email:
            raise UserError(_("This record has no email address to verify."))
        client = FlatverifyClient.from_env(self.env)
        try:
            result = client.verify(email)
        except FlatverifyBusy as exc:
            record._flatverify_write({"flatverify_queued": True})
            self.env["flatverify.job"]._flatverify_pause_queue(exc.retry_after)
            self._flatverify_trigger_cron()
            return self._flatverify_notify(
                _("Flatverify is busy right now. The check is queued and runs in about %s seconds.") % exc.retry_after,
                "warning",
            )
        except FlatverifyError as exc:
            if exc.code == "invalid_email":
                record._flatverify_apply_result({"status": "invalid", "sub_status": "bad_syntax"})
                return self._flatverify_notify(_("%s is not a valid email address.") % email, "warning")
            raise
        record._flatverify_apply_result(result)
        status = record.flatverify_status
        reason = record.flatverify_reason
        message = _("%(email)s is %(status)s.") % {"email": email, "status": status}
        if reason:
            message = _("%(email)s is %(status)s (%(reason)s).") % {"email": email, "status": status, "reason": reason}
        kind = {"valid": "success", "invalid": "danger", "risky": "warning"}.get(status, "info")
        return self._flatverify_notify(message, kind)

    def action_flatverify_bulk(self):
        """Send the selected records to Flatverify as one bulk job and poll it in the background."""
        records = self.filtered(lambda r: r._flatverify_email())
        if not records:
            raise UserError(_("None of the selected records has an email address."))
        if not FlatverifyClient.from_env(self.env).api_key:
            raise UserError(MSG_NO_KEY)
        jobs = self.env["flatverify.job"].sudo()._flatverify_create_for(records)
        jobs._flatverify_submit(raise_errors=True)
        self._flatverify_trigger_cron()
        pending = jobs.filtered(lambda j: j.state == "pending")
        if pending:
            return self._flatverify_notify(pending[0].message or _("Flatverify is busy right now."), "warning")
        return self._flatverify_notify(
            _("Flatverify is checking %s addresses. Results appear on each record when the list is done.")
            % sum(jobs.mapped("total")),
            "success",
        )
