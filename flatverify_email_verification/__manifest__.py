{
    "name": "Flatverify Email Verification",
    "summary": "Verify contact emails in Odoo: valid, invalid, risky or unknown. "
    "Every plan is unlimited, plans differ only in speed.",
    "description": "Verify the email addresses of your Odoo contacts with Flatverify. "
    "A Verify email button on every contact, one bulk job for any selection, "
    "optional automatic checks on new and changed emails, and status filters.",
    "version": "20.0.1.0.0",
    "category": "Sales",
    "author": "Argorant",
    "maintainer": "Argorant",
    "website": "https://flatverify.com",
    "support": "support@argorant.com",
    "license": "LGPL-3",
    "depends": ["base_setup"],
    "data": [
        "security/ir.access.csv",
        "data/ir_cron.xml",
        "data/ir_actions_server.xml",
        "views/flatverify_job_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
    ],
    "images": [
        "static/description/banner.png",
        "static/description/main_screenshot.png",
    ],
    "installable": True,
    "application": False,
}
