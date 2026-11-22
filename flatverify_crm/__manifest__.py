{
    "name": "Flatverify Email Verification for CRM",
    "summary": "Verify lead and opportunity emails with Flatverify. "
    "Every plan is unlimited, plans differ only in speed.",
    "description": "Adds Flatverify Status, Reason and Checked to CRM leads and opportunities, "
    "a Verify email button on the lead form and one bulk job for any selection of leads.",
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "author": "Argorant",
    "maintainer": "Argorant",
    "website": "https://flatverify.com",
    "support": "support@argorant.com",
    "license": "LGPL-3",
    "depends": ["crm", "flatverify_email_verification"],
    "data": [
        "data/ir_actions_server.xml",
        "views/crm_lead_views.xml",
    ],
    "images": [
        "static/description/banner.png",
        "static/description/main_screenshot.png",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
