# Email Ingestion Hardening

CRM email ingestion is not the sender-facing source of truth for mail delivery. The CRM poller reads accepted mailbox messages and records malformed sender skips, but it does not delete source mail and it cannot reject a message after the mail server has already accepted it.

## Owner Boundaries

- Mail server / MTA: accepts or rejects SMTP delivery and sends sender-visible bounce responses.
- CRM email poller: imports accepted mailbox messages into CRM conversations, skips malformed accepted messages, records skip evidence, and advances the mailbox cursor.
- CRM admin settings: surfaces the last poll run and recent malformed sender skips for operators.

## Sales Mail Rejection Policy

Configure the Sales Mail MTA or anti-spam policy to reject messages before mailbox delivery when the sender identity is blank or syntactically invalid. This is the only layer that can reliably tell the sender their message was not accepted.

Minimum checks:

- Reject missing or invalid SMTP envelope sender when the server is not handling a valid bounce.
- Reject malformed `From` headers on inbound customer mail.
- Preserve normal null-envelope delivery for legitimate DSNs if the mail server routes bounces for this mailbox.
- Log rejected message ID, remote IP, envelope sender, header `From`, and reason.

Operational checks after changing the mail server:

- Send a normal customer message and verify CRM ingestion still creates the conversation.
- Send a blank or malformed `From` test message and verify the MTA rejects it before it reaches the mailbox.
- Confirm the CRM poller logs no `EmailWebhookPayload.contact_address` validation error and no timeout while processing the mailbox.

CRM-side fallback remains necessary because accepted historical mail and upstream forwarding systems can still place malformed messages in the mailbox.
