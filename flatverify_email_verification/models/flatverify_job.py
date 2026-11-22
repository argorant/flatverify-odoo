import json
import logging
import threading
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import config

from .compat import param_get, param_set
from .flatverify_api import MAX_JOB_EMAILS, FlatverifyBusy, FlatverifyClient, FlatverifyError
from .flatverify_mixin import STATUS_KEYS, looks_like_email, normalize_email

_logger = logging.getLogger(__name__)

OPEN_STATES = ("queued", "running", "waiting")
QUEUE_PAUSE_PARAM = "flatverify.queue_not_before"


class FlatverifyJob(models.Model):
    """One Flatverify bulk job: a set of Odoo records whose addresses went out as one list."""

    _name = "flatverify.job"
    _description = "Flatverify bulk job"
    _order = "id desc"

    name = fields.Char(required=True, readonly=True)
    remote_id = fields.Char(string="Flatverify job id", readonly=True, copy=False, index=True)
    state = fields.Selection(
        [
            ("pending", "Starting soon"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("waiting", "Waiting"),
            ("done", "Done"),
            ("failed", "Stopped"),
        ],
        default="pending",
        required=True,
        index=True,
        readonly=True,
    )
    res_model = fields.Char(string="Records of", required=True, readonly=True)
    res_model_label = fields.Char(string="Record type", compute="_compute_res_model_label")
    res_ids = fields.Text(string="Record ids", readonly=True, default="[]")
    record_count = fields.Integer(string="Records", readonly=True)
    total = fields.Integer(string="Addresses", readonly=True)
    processed = fields.Integer(string="Checked", readonly=True)
    progress = fields.Float(string="Progress", compute="_compute_progress")
    next_after = fields.Integer(string="Results read up to", default=-1, readonly=True)
    next_try_at = fields.Datetime(string="Next try", readonly=True)
    message = fields.Char(readonly=True)
    finished_at = fields.Datetime(string="Finished", readonly=True)

    @api.depends("res_model")
    def _compute_res_model_label(self):
        for job in self:
            model = self.env["ir.model"]._get(job.res_model) if job.res_model else None
            job.res_model_label = model.name if model else job.res_model

    @api.depends("processed", "total", "state")
    def _compute_progress(self):
        for job in self:
            if job.state == "done":
                job.progress = 100.0
            elif job.total:
                job.progress = min(100.0, 100.0 * job.processed / job.total)
            else:
                job.progress = 0.0

    # Helpers

    @api.model
    def _flatverify_open_states(self):
        return OPEN_STATES

    @api.model
    def _flatverify_models(self):
        """Every concrete model that carries the Flatverify fields (contacts, and leads with flatverify_crm)."""
        names = []
        for name in list(self.env.registry):
            model = self.env[name]
            if model._abstract or model._transient:
                continue
            if "flatverify_queued" in model._fields and hasattr(model, "_flatverify_email_field"):
                names.append(name)
        return sorted(names)

    def _flatverify_record_ids(self):
        self.ensure_one()
        try:
            return [int(i) for i in json.loads(self.res_ids or "[]")]
        except (ValueError, TypeError):
            return []

    def _flatverify_records(self):
        self.ensure_one()
        return self.env[self.res_model].with_context(active_test=False).browse(self._flatverify_record_ids()).exists()

    def _flatverify_can_commit(self):
        if not self.env.context.get("flatverify_commit"):
            return False
        if getattr(threading.current_thread(), "testing", False) or config.get("test_enable"):
            return False
        return True

    def _flatverify_commit(self):
        if self._flatverify_can_commit():
            self.env.cr.commit()

    @api.model
    def _flatverify_pause_queue(self, seconds):
        until = fields.Datetime.now() + timedelta(seconds=int(seconds or 60))
        param_set(self.env, QUEUE_PAUSE_PARAM, fields.Datetime.to_string(until))

    @api.model
    def _flatverify_queue_paused(self):
        value = param_get(self.env, QUEUE_PAUSE_PARAM)
        if not value:
            return False
        try:
            return fields.Datetime.to_datetime(value) > fields.Datetime.now()
        except (TypeError, ValueError):
            return False

    # Create and submit

    @api.model
    def _flatverify_create_for(self, records):
        """Create jobs for the records. Records with the same address share a job; up to 200,000 addresses each."""
        if not records:
            return self.browse()
        field = records._flatverify_email_field
        by_email = defaultdict(list)
        for rec_id, email in zip(records.ids, records.mapped(field)):
            key = normalize_email(email)
            if key:
                by_email[key].append(rec_id)
        emails = list(by_email)
        chunks = [emails[i:i + MAX_JOB_EMAILS] for i in range(0, len(emails), MAX_JOB_EMAILS)]
        label = records._description or records._name
        stamp = fields.Datetime.context_timestamp(self, fields.Datetime.now()).strftime("%Y-%m-%d %H:%M")
        jobs = self.browse()
        for index, chunk in enumerate(chunks, start=1):
            ids = [rec_id for email in chunk for rec_id in by_email[email]]
            name = "Odoo %s %s" % (label, stamp)
            if len(chunks) > 1:
                name = "%s part %s" % (name, index)
            job = self.create(
                {
                    "name": name,
                    "res_model": records._name,
                    "res_ids": json.dumps(ids),
                    "record_count": len(ids),
                    "total": len(chunk),
                }
            )
            records.browse(ids)._flatverify_write({"flatverify_job_id": job.id, "flatverify_queued": False})
            jobs |= job
        return jobs

    def _flatverify_submit(self, raise_errors=False):
        """POST /verify/bulk once per pending job. Busy answers keep the job pending until Retry-After."""
        client = FlatverifyClient.from_env(self.env)
        for job in self.filtered(lambda j: j.state == "pending"):
            records = job._flatverify_records().filtered(lambda r, j=job: r.flatverify_job_id == j)
            field = records._flatverify_email_field
            emails = sorted({normalize_email(e) for e in records.mapped(field) if normalize_email(e)})
            if not emails:
                job.write({"state": "done", "finished_at": fields.Datetime.now(), "message": False})
                continue
            try:
                data = client.bulk(emails, name=job.name)
            except FlatverifyBusy as exc:
                job.write(
                    {
                        "next_try_at": fields.Datetime.now() + timedelta(seconds=exc.retry_after),
                        "message": exc.args[0],
                    }
                )
                continue
            except FlatverifyError as exc:
                if raise_errors:
                    raise
                job.write({"state": "failed", "message": exc.args[0]})
                continue
            job.write(
                {
                    "remote_id": data.get("id"),
                    "state": self._flatverify_map_state(data.get("status")),
                    "total": data.get("total") or len(emails),
                    "processed": data.get("processed") or 0,
                    "next_after": -1,
                    "next_try_at": False,
                    "message": False,
                }
            )

    @api.model
    def _flatverify_map_state(self, remote_status):
        if remote_status in OPEN_STATES:
            return remote_status
        if remote_status == "done":
            return "running"  # stays open until every result page is written back
        return "running"

    # Poll and write back

    def _flatverify_poll(self):
        client = FlatverifyClient.from_env(self.env)
        now = fields.Datetime.now()
        for job in self.filtered(lambda j: j.state in OPEN_STATES and j.remote_id):
            if job.next_try_at and job.next_try_at > now:
                continue
            try:
                data = client.job(job.remote_id)
                job.write(
                    {
                        "state": self._flatverify_map_state(data.get("status")),
                        "total": data.get("total") or job.total,
                        "processed": data.get("processed") or 0,
                        "next_try_at": False,
                        "message": False,
                    }
                )
                if data.get("status") == "done":
                    job._flatverify_fetch_results(client)
            except FlatverifyBusy as exc:
                job.write(
                    {
                        "next_try_at": fields.Datetime.now() + timedelta(seconds=exc.retry_after),
                        "message": exc.args[0],
                    }
                )
            except FlatverifyError as exc:
                if exc.status == 404 or exc.code in ("payment_required", "unauthorized", "no_key"):
                    job.write({"state": "failed", "message": exc.args[0]})
                else:
                    job.write({"message": exc.args[0]})
            job._flatverify_commit()

    def _flatverify_fetch_results(self, client):
        """Page /jobs/:id/results?format=json&after= and write status, reason and time per record."""
        self.ensure_one()
        model = self.env[self.res_model].with_context(active_test=False)
        field = model._flatverify_email_field
        by_email = defaultdict(list)
        for row in model.search_read([("flatverify_job_id", "=", self.id)], [field]):
            by_email[normalize_email(row[field])].append(row["id"])
        after = self.next_after
        while True:
            page = client.results(self.remote_id, after=after)
            groups = defaultdict(list)
            for row in page.get("results") or []:
                status = row.get("status")
                if status not in STATUS_KEYS:
                    continue
                ids = by_email.pop(normalize_email(row.get("email")), [])
                if ids:
                    groups[(status, row.get("sub_status") or False)].extend(ids)
            checked_at = fields.Datetime.now()
            for (status, reason), ids in groups.items():
                model.browse(ids)._flatverify_write(
                    {
                        "flatverify_status": status,
                        "flatverify_reason": reason,
                        "flatverify_checked_at": checked_at,
                        "flatverify_queued": False,
                        "flatverify_job_id": False,
                    }
                )
            next_after = page.get("next_after")
            if next_after is None or next_after == after:
                break
            after = next_after
            self.write({"next_after": after})
            self._flatverify_commit()

        leftover = model.browse([i for ids in by_email.values() for i in ids]).exists()
        if leftover:
            checked_at = fields.Datetime.now()
            bad = leftover.filtered(lambda r: not looks_like_email(r[field]))
            if bad:
                bad._flatverify_write(
                    {
                        "flatverify_status": "invalid",
                        "flatverify_reason": "bad_syntax",
                        "flatverify_checked_at": checked_at,
                        "flatverify_job_id": False,
                    }
                )
            rest = leftover - bad
            if rest:
                rest._flatverify_write(
                    {
                        "flatverify_status": "unknown",
                        "flatverify_reason": False,
                        "flatverify_checked_at": checked_at,
                        "flatverify_job_id": False,
                    }
                )
        self.write({"state": "done", "finished_at": fields.Datetime.now(), "message": False})

    # Queue (automatic checks) and cron

    @api.model
    def _flatverify_process_queue(self):
        """Records queued on create or email change go out as one bulk job per model."""
        if self._flatverify_queue_paused():
            return self.browse()
        jobs = self.browse()
        for model_name in self._flatverify_models():
            model = self.env[model_name].with_context(active_test=False)
            queued = model.search([("flatverify_queued", "=", True)], limit=MAX_JOB_EMAILS)
            with_email = queued.filtered(lambda r: r._flatverify_email())
            if queued - with_email:
                (queued - with_email)._flatverify_write({"flatverify_queued": False})
            if with_email:
                jobs |= self._flatverify_create_for(with_email)
        return jobs

    @api.model
    def _cron_flatverify_run(self):
        if not FlatverifyClient.from_env(self.env).api_key:
            return
        jobs = self.with_context(flatverify_commit=True)
        jobs._flatverify_run_once()

    @api.model
    def _flatverify_run_once(self):
        self._flatverify_process_queue()
        self._flatverify_commit()
        now = fields.Datetime.now()
        pending = self.search([("state", "=", "pending"), "|", ("next_try_at", "=", False), ("next_try_at", "<=", now)])
        for job in pending:
            job._flatverify_submit()
            job._flatverify_commit()
        self.search([("state", "in", list(OPEN_STATES))])._flatverify_poll()

    # Buttons on the job form

    def action_flatverify_retry(self):
        for job in self.filtered(lambda j: j.state in ("failed", "pending")):
            job.write({"state": "pending", "next_try_at": False, "message": False, "remote_id": False, "next_after": -1})
        self._flatverify_submit(raise_errors=True)
        return True

    def action_flatverify_check_now(self):
        self.write({"next_try_at": False})
        self._flatverify_poll()
        return True

    def action_flatverify_open_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": self.res_model,
            "view_mode": "tree,form",
            "domain": [("id", "in", self._flatverify_record_ids())],
            "context": {"active_test": False},
        }
