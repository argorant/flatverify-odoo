from odoo import models


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "flatverify.mixin"]
    _flatverify_email_field = "email_from"
