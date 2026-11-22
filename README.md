# Flatverify for Odoo

Verify the email addresses of your Odoo contacts, leads and opportunities with
[Flatverify](https://flatverify.com). Every plan is unlimited, plans differ only in speed.

Publisher: Argorant. License: LGPL-3. Free module.

## Modules

| Module | What it does |
| --- | --- |
| `flatverify_email_verification` | Settings (API key, Test connection, automatic checks), Flatverify Status, Reason and Checked on contacts, Verify email button, bulk action for selected contacts, background job polling, search filters. Depends on `base_setup` only. |
| `flatverify_crm` | The same fields, button, bulk action and filters on CRM leads and opportunities. Installs itself when CRM and the base module are both installed. |

## Branches

One branch per Odoo series: `17.0`, `18.0`, `19.0`, `20.0`.

## Setup

1. Install Flatverify Email Verification from the Apps menu.
2. Open Settings, Flatverify, paste the API key from app.flatverify.com, Settings, API keys, and click Test connection.
   You see the plan and the speed per minute.
3. Optional: turn on "Verify new and changed emails automatically". New and changed addresses are queued and
   checked in the background, so saving a record never waits.

## Use

- Open a contact such as Maya Chen (maya.chen@amazon.com) and click Verify email. Status, Reason and Checked fill in.
- Select many contacts in the list view, open Actions, choose Verify emails with Flatverify. The whole selection
  goes out as one bulk job; a scheduled action polls it and writes the results back to every record.
- Filter by Valid email, Invalid email or Risky email, or group by Flatverify Status.

Status is valid, invalid, risky or unknown. Reason is one word such as accepted, catch_all, role_account,
disposable or mailbox_not_found.

## Errors

- Busy or rate limited answers (429, 503) are retried after the time the API asks for.
- 402: "Your Flatverify plan needs attention. Open app.flatverify.com/billing."

## Tests

Unit tests use mocked HTTP only (`--test-tags flatverify`). One live end to end test runs only with an internal
QA key in `FLATVERIFY_QA_KEY`; without it, it is skipped.

## Support

support@argorant.com. Docs: https://flatverify.com/docs. Privacy: https://flatverify.com/privacy.
Terms: https://flatverify.com/terms.
