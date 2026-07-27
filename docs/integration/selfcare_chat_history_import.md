# Selfcare native-chat history import

This bounded importer supports the temporary CRM-authority decision documented
in Dotmac Sub ADR 0006. It is an operator migration, not a live synchronization
path.

The input schema is `dotmac_sub.native_chat_history.v1`. The importer:

- verifies the export digest and declared counts;
- resolves each Sub subscriber UUID to exactly one active CRM Person;
- keys conversations by the Sub source conversation UUID;
- keys messages with `dotmac_sub:<source-message-uuid>`;
- preserves original message and conversation timestamps;
- marks rows as historical imports with source provenance;
- does not emit `message.inbound` events, run live auto-assignment, or send
  customer notifications;
- commits the batch atomically under a PostgreSQL advisory transaction lock;
- fails closed on ambiguous identity, changed content, or cross-thread reuse.

Run a dry-run first against a private mode-0600 preflight export:

```bash
poetry run python scripts/import_selfcare_chat_history.py \
  --input /run/operator/selfcare-chat-history.json
```

Apply only after the dry-run maps the full cohort:

```bash
poetry run python scripts/import_selfcare_chat_history.py \
  --input /run/operator/selfcare-chat-history.json \
  --apply
```

Run `--apply` a second time. The acceptance result has zero creates and reports
every conversation and message as reused. Remove the private input after count
and provenance verification.

The final applied file must be a fresh export created after Selfcare's
`comms.chat_session_authority=crm` write barrier is active. The earlier
preflight proves identity mapping only; applying a post-barrier snapshot closes
the race with a native message arriving during preflight.

Imported conversations do not have a live portal visitor session. They are
backlog/audit evidence; agents should follow up through an active CRM channel.
