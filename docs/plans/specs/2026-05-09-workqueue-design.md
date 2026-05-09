# Workqueue — Design Spec

**Date:** 2026-05-09
**Feature ref:** `docs/plans/feature-list.md` → T1.3
**Status:** Approved (design); awaiting implementation plan

---

## 1. Summary

A unified "what should I work on right now" surface at `/agent/workqueue`. Aggregates conversations, tickets, leads/quotes, and project tasks into a hybrid view: a **Right Now** hero band (top items by urgency across all kinds) over per-kind sections. Role-aware audience (self / team / org). Live updates via WebSocket with a 60-second polling fallback. Inline actions: open, snooze, claim, complete.

---

## 2. Goals & non-goals

### Goals
- Single surface that personalizes by role and shows the highest-urgency work first.
- Reuse existing service-layer reads — no business-logic duplication.
- Pluggable provider interface so new item kinds (and AI-augmented scoring) can be added without a schema migration.
- Live updates without a separate server: extend the existing presence WS hub.

### Non-goals (v1)
- AI-augmented scoring.
- Persistent dismissal of items.
- Bulk actions, filters, or search inside the queue.
- Mobile-specific gestures (responsive layout yes; native swipe no).
- Cross-user delegation ("snooze this on someone else's behalf").
- Per-user customization of section order or hero-band size.

---

## 3. Audience & permissions

Role-aware view, scope determined from RBAC:

| Audience | Items returned | Granted by |
|---|---|---|
| `self` | Items assigned to me | All authenticated CRM/inbox users |
| `team` | Mine + my team's items, including unassigned | Dispatchers, team leads, managers |
| `org` | Everything | Admins |

Highest-tier permission wins. Users may downscope via `?as=self` (or `?as=team` for `org` users) — never upscope. An unsupported `?as=` value falls back to the user's natural audience.

New permissions (seeded by migration):

| Permission | Granted to |
|---|---|
| `workqueue:view` | All authenticated CRM/inbox users |
| `workqueue:claim` | Agents, dispatchers, managers, admins |
| `workqueue:audience:team` | Dispatchers, team leads, managers |
| `workqueue:audience:org` | Admins |

---

## 4. Architecture

```
app/
├── services/
│   └── workqueue/
│       ├── __init__.py
│       ├── types.py              # WorkqueueItem, WorkqueueSection, ItemKind, WorkqueueAudience
│       ├── providers/
│       │   ├── base.py           # WorkqueueProvider Protocol
│       │   ├── conversations.py
│       │   ├── tickets.py
│       │   ├── leads_quotes.py
│       │   └── tasks.py
│       ├── aggregator.py         # merge + rank + section assembly
│       ├── snooze.py             # snooze CRUD + "until next reply" resolver
│       ├── actions.py            # claim / complete / snooze (delegates to domain managers)
│       ├── events.py             # emit_change + WS channel helpers
│       ├── permissions.py        # can_view_workqueue, resolve_audience
│       ├── scoring_config.py     # tunable thresholds + score bands
│       └── tasks.py              # Celery: sla_tick, prune_snoozes
├── models/
│   └── workqueue.py              # WorkqueueSnooze (only new table)
└── web/
    └── agent/
        └── workqueue.py          # GET page + HTMX partials + action POSTs

templates/agent/workqueue/
├── index.html
├── _right_now.html
├── _section.html
├── _item.html
└── _snooze_picker.html

templates/components/ui/
└── workqueue_macros.html         # workqueue_item(item) macro
```

**Boundary rules:**
- Providers are the only Workqueue code that touches other domains. They delegate to existing read functions (`inbox.queries`, ticket queries, lead queries, project queries) — no duplicated business logic.
- `actions.py` is a thin facade over domain managers (`Conversations`, `Tickets`, `Tasks`). The Workqueue layer contains no mutation logic.
- WS lives in the existing presence hub. We add a channel topic, not a new server.

---

## 5. Data model

### 5.1 `WorkqueueItem` (in-memory dataclass — never persisted)

```python
class ItemKind(StrEnum):
    conversation = "conversation"
    ticket = "ticket"
    lead = "lead"
    quote = "quote"
    task = "task"

class ActionKind(StrEnum):
    open = "open"
    snooze = "snooze"
    claim = "claim"
    complete = "complete"

@dataclass(frozen=True)
class WorkqueueItem:
    kind: ItemKind
    item_id: UUID
    title: str
    subtitle: str | None
    score: int                       # 0..100 from provider
    reason: str                      # short label, e.g. "sla_breach"
    urgency: Literal["critical", "high", "normal", "low"]  # derived from score band
    deep_link: str
    assignee_id: UUID | None
    is_unassigned: bool
    happened_at: datetime
    actions: frozenset[ActionKind]
    metadata: dict[str, Any]         # channel, priority, etc.
```

Items are recomputed each request — no item-level table.

### 5.2 `WorkqueueAudience` (computed, not stored)

```python
class WorkqueueAudience(StrEnum):
    self = "self"
    team = "team"
    org = "org"
```

Derived from user roles via `permissions.resolve_audience(user)`.

### 5.3 `WorkqueueSnooze` (the only new table)

```python
class WorkqueueSnooze(Base):
    __tablename__ = "workqueue_snoozes"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    item_kind: Mapped[ItemKind] = mapped_column(Enum(ItemKind, native_enum=False), nullable=False)
    item_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    until_next_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("user_id", "item_kind", "item_id", name="uq_workqueue_snooze_user_item"),
        Index("ix_workqueue_snooze_user_until", "user_id", "snooze_until"),
    )
```

**Snooze semantics:**
- Either `snooze_until` is set OR `until_next_reply=True`. The service layer raises `ValueError` if both or neither are provided.
- For conversations, "until next reply" hooks the existing inbound-message handler: a new inbound message clears the row and re-emits `added` to WS.
- For other kinds, "until next reply" is hidden in the UI.
- Daily Celery task prunes rows where `snooze_until < now() - 7d`.

**No dismissal table.** Items leave the queue only when their underlying state changes (resolved, replied, completed) or via snooze. This avoids "where did my work go?" UX issues and a sync-with-resolution problem.

---

## 6. Provider contract

```python
class WorkqueueProvider(Protocol):
    kind: ItemKind

    def fetch(
        self,
        db: Session,
        *,
        user: AuthUser,
        audience: WorkqueueAudience,
        snoozed_ids: set[UUID],
        limit: int = 50,
    ) -> list[WorkqueueItem]: ...
```

**Contract rules:**
- Providers filter, score, and build deep-links. The aggregator does not know per-type semantics.
- Providers respect `audience`: `self` → `assignee_id == user.id`; `team` → user's team members + unassigned; `org` → unfiltered.
- Providers skip items whose `(kind, id)` is in `snoozed_ids`.
- Providers return at most `limit` items already sorted by descending score.

---

## 7. Scoring

Scores are 0–100. Bands map to urgency:

| Score | Urgency |
|---|---|
| ≥ 90 | critical |
| 70–89 | high |
| 40–69 | normal |
| < 40 | low |

All thresholds and weights live in `app/services/workqueue/scoring_config.py` so they can be tuned in one file.

### 7.1 Conversations

| Reason | Score |
|---|---|
| `sla_breach` (first or next-response SLA missed) | 100 |
| `sla_imminent` (≤ 5 min) | 90 |
| `sla_soon` (≤ 30 min) | 75 |
| `mention` (you're @mentioned in private note) | 65 |
| `awaiting_reply_long` (> 4h, no SLA) | 55 |
| `assigned_unread` | 45 |

### 7.2 Tickets

| Reason | Score |
|---|---|
| `sla_breach` | 100 |
| `sla_imminent` (≤ 15 min) | 90 |
| `priority_urgent` + open | 80 |
| `sla_soon` (≤ 2 h) | 75 |
| `overdue` (past `due_at`) | 70 |
| `customer_replied` (was waiting_on_customer) | 65 |

### 7.3 Leads & Quotes

| Reason | Score |
|---|---|
| `quote_expires_today` | 85 |
| `lead_overdue_followup` (`next_action_at < now`) | 70 |
| `quote_expires_3d` | 65 |
| `lead_high_value_idle_3d` (weighted value above org-configurable threshold) | 60 |
| `quote_sent_no_response_7d` | 50 |

### 7.4 Tasks

| Reason | Score |
|---|---|
| `overdue` | 80 |
| `due_today` | 70 |
| `blocked_dependency_resolved` | 60 |
| `assigned_recently_unread` | 40 |

### 7.5 Tie-break

Equal scores order by `happened_at` descending, then by `kind` in fixed order: conversation → ticket → lead → quote → task. Ensures stable ordering across reloads.

---

## 8. Aggregator

```python
def build_workqueue(db: Session, user: AuthUser) -> WorkqueueView:
    audience = resolve_audience(user)
    snoozed = snooze_service.active_snoozed_ids(db, user.id)

    items_by_kind = {}
    for provider in PROVIDERS:
        items_by_kind[provider.kind] = provider.fetch(
            db, user=user, audience=audience, snoozed_ids=snoozed, limit=50
        )

    all_items = list(chain.from_iterable(items_by_kind.values()))
    all_items.sort(key=lambda i: (-i.score, -i.happened_at.timestamp(), KIND_ORDER[i.kind]))

    right_now = all_items[:HERO_BAND_SIZE]   # default 6, settings-tunable

    sections = [
        WorkqueueSection(kind=k, items=items_by_kind[k], total=len(items_by_kind[k]))
        for k in SECTION_ORDER  # conversation, ticket, leads_quotes, task
    ]

    return WorkqueueView(audience=audience, right_now=right_now, sections=sections)
```

Lead and quote items render in a single "Leads & Quotes" UI section but remain separate `ItemKind` values (independent scoring, separate provider methods if needed).

**Performance budget:** P95 < 250 ms full-page render, < 100 ms section partial. Existing indices (assignee+status, sla_due_at, due_at) cover provider queries. With `limit=50` per provider, worst-case examined rows ≈ 200.

---

## 9. Live updates

### 9.1 Channels

- `workqueue:user:{user_id}` — every authenticated user subscribes.
- `workqueue:audience:team:{team_id}` — `team` audience adds this.
- `workqueue:audience:org` — `org` audience adds this.

### 9.2 Event payload

```json
{
  "type": "workqueue.changed",
  "kind": "ticket",
  "item_id": "…",
  "change": "added | removed | updated",
  "score": 85,
  "reason": "sla_imminent",
  "happened_at": "2026-05-09T14:32:11Z"
}
```

Payload is identifiers + minimal metadata only — no rendered markup. Client decides whether to drop, insert, or refresh; server doesn't render markup for events.

### 9.3 Emit points

Five well-defined places, each calls `workqueue.events.emit_change(...)` after the relevant DB commit:

1. **Assignment changes** — ticket/conversation/lead/task `assignee_id` writes. Emits `removed` for old assignee, `added` for new.
2. **Status transitions** — ticket resolved, conversation closed/snoozed, lead won/lost, task completed. Emits `removed`.
3. **SLA tick** — Celery beat task `workqueue.tasks.sla_tick` runs every 60 s, scans items whose SLA band has changed, emits `updated`. Only periodic emitter.
4. **New inbound** — inbox handler emits `added`/`updated` on new inbound. Also clears any `until_next_reply` snooze and emits `added`.
5. **Snooze CRUD** — emits `removed` on create, `added` on clear.

`emit_change` resolves the target user-set per event: the current assignee, the previous assignee (on reassignment), plus any active subscribers on the relevant `workqueue:audience:team:{team_id}` or `workqueue:audience:org` channels. The call is fire-and-forget: failures are logged, never raised. The Workqueue is a derived view; dropped pushes are reconciled by the next polling tick or page load.

### 9.4 Polling fallback

Each section also has `hx-trigger="every 60s"`. Server returns 304 when nothing changed (ETag built from `(top_item_id, top_score, count)`). When WS is healthy, polling is a no-op; when WS is down, the user gets eventually-consistent data within a minute.

### 9.5 Client behavior

On `workqueue.changed`:
- Re-fetch `_right_now` (any change can shuffle global ranking).
- Re-fetch `_section/{kind}` for the affected kind.
- Debounce 250 ms so a burst coalesces into one refresh.

WS reconnect: existing presence-channel backoff (200 ms → 5 s → 30 s). On reconnect, trigger one full Workqueue refresh.

---

## 10. Routes

All under `/agent/workqueue`. All require `Depends(require_web_auth)` and `Depends(require_permission("workqueue:view"))`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/agent/workqueue` | Full page render |
| `GET` | `/agent/workqueue/_right_now` | Hero-band partial |
| `GET` | `/agent/workqueue/_section/{kind}` | One section's partial |
| `POST` | `/agent/workqueue/snooze` | Create snooze. Body: `kind`, `item_id`, `until` or `until_next_reply` |
| `POST` | `/agent/workqueue/snooze/clear` | Clear snooze. Body: `kind`, `item_id` |
| `POST` | `/agent/workqueue/claim` | Assign-to-me. Body: `kind`, `item_id` |
| `POST` | `/agent/workqueue/complete` | Quick-resolve. Body: `kind`, `item_id` |

All `POST`s return `204` with `HX-Trigger` carrying `workqueue:refresh` plus a toast payload. The page-level Alpine listener turns `workqueue:refresh` into the same two `hx-get` calls a WS event triggers, so action and live paths share one refresh code path.

---

## 11. Inline actions

| Action | Behavior |
|---|---|
| **Open** | Plain `<a href="{deep_link}">`. No JS. |
| **Snooze** | Alpine `x-show` popover (`_snooze_picker.html`). Choices: 1h / Tomorrow 9am / Next week / Until next reply (conversations only) / Custom datetime. POST to `/snooze`. |
| **Claim** | Rendered only when `is_unassigned and "workqueue:claim" in user.permissions`. POST to `/claim`. Single-click, no confirmation in v1 (only self-claim). |
| **Resolve / Complete** | Confirmation modal. POST to `/complete`. Endpoint dispatches per `kind`: conversation → `Conversations.set_status(closed)`; ticket → `Tickets.resolve()`; task → `Tasks.complete()`. Hidden for lead/quote — these need a stage choice; user clicks Open. |

### `actions.py`

```python
class WorkqueueActions:
    @staticmethod
    def claim(db, user, kind, item_id) -> None: ...
    @staticmethod
    def complete(db, user, kind, item_id) -> None: ...
    @staticmethod
    def snooze(db, user, kind, item_id, *, until=None, until_next_reply=False) -> None: ...
    @staticmethod
    def clear_snooze(db, user, kind, item_id) -> None: ...

workqueue_actions = WorkqueueActions()
```

Each method validates the user can act on the item (assignee or has team/org audience), delegates to the existing domain manager, then emits the WS event.

---

## 12. UI

### 12.1 Layout

```
┌─ Workqueue ──────────────────── [audience: self ▾] [↻] ─┐
│                                                          │
│  Right now                                               │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ [⚠ critical] T-1042 · OLT down · SLA breach 2m ago │ │
│  │ [Open] [Claim] [Snooze] [Resolve]                  │ │
│  └─────────────────────────────────────────────────────┘ │
│  … up to 6 hero items …                                  │
│                                                          │
│  ── Conversations (12) ─────────────────────  [Show all] │
│  ── Tickets (4) ─────────────────────────────  [Show all] │
│  ── Leads & Quotes (3) ──────────────────────  [Show all] │
│  ── Tasks (5) ───────────────────────────────  [Show all] │
└──────────────────────────────────────────────────────────┘
```

Each section shows top 5 by default. "Show all" links into the relevant list view (e.g., `/admin/tickets?assignee=me&status=open&sort=sla`).

### 12.2 Styling

Reuses macros from `components/ui/macros.html` (`page_header`, `card`, `status_badge`, `action_button`, `empty_state`). New helper macro `workqueue_item(item)` lives in a new `components/ui/workqueue_macros.html` file — no edits to the central `macros.html`.

Color coding follows the design system. Kind chip uses domain colors:

| Kind | Domain colors |
|---|---|
| conversation | violet (channel-specific via existing CRM channel CSS variables) |
| ticket | rose + pink |
| lead / quote | amber + orange |
| task | emerald + teal |

Urgency band colors: `red-500` critical, `amber-500` high, `slate-500` normal/low (with required `dark:` variants per the design system).

### 12.3 Sidebar entry

Added to the agent sidebar above "Inbox". Count badge shows `len(right_now)`, fed by a new `workqueue_attention` field on `get_sidebar_stats(db)`.

---

## 13. Testing

### 13.1 Unit / service tests (`tests/services/test_workqueue_*.py`)

| File | Covers |
|---|---|
| `test_workqueue_providers.py` | Each provider in isolation: scoring per `reason`, audience filtering, snooze exclusion, deep-link generation |
| `test_workqueue_aggregator.py` | Merge ordering, tie-break determinism, hero-band cap, section assembly, empty-state |
| `test_workqueue_snooze.py` | CRUD, mutual exclusivity, auto-clear on inbound, prune task |
| `test_workqueue_actions.py` | Permission gating per (action × kind), delegation to domain managers, WS emission, complete-disallowed for lead/quote |
| `test_workqueue_events.py` | `emit_change` user-set resolution per audience, fire-and-forget on failure, reassignment emits `removed`+`added` |
| `test_workqueue_sla_tick.py` | Beat task: band transition emits exactly one `updated` per affected user |

### 13.2 Route tests (`tests/web/test_workqueue_routes.py`)

- `GET /agent/workqueue` returns 200 with hero band + 4 sections.
- `_right_now` and `_section/{kind}` partials render correctly.
- `?as=self` downscopes a manager.
- All `POST`s return 204 with correct `HX-Trigger`.
- Permission denials return 403.

### 13.3 E2E (`tests/playwright/e2e/test_workqueue.py`)

- Snooze a conversation → it disappears → time advances (mock) → it reappears.
- Claim an unassigned ticket → row reflows out of "unassigned" filter.
- Live update: agent B assigns a ticket to agent A → A's page reflects it without manual refresh.

---

## 14. Migration & rollout

### 14.1 Migration

One Alembic migration: `workqueue_snoozes` table + indices, plus seeded permissions (`workqueue:view`, `workqueue:claim`, `workqueue:audience:team`, `workqueue:audience:org`). No data backfill.

### 14.2 Feature flag

`workqueue.enabled` in `settings_spec.py` (default off in production, on in staging). Sidebar link, page route, and beat task all check the flag at request time. Per-user opt-in via `settings_state` (already supported).

### 14.3 Rollout stages

1. Internal admin (single user).
2. All admins + managers.
3. All agents.

### 14.4 Observability

Slot into existing `app/metrics.py`:

| Metric | Type | Purpose |
|---|---|---|
| `workqueue.render_ms{audience}` | histogram | Full-page render latency (P95 budget signal) |
| `workqueue.action_total{kind, action}` | counter | Action usage |
| `workqueue.ws_event_total{kind, change}` | counter | Live-update volume |
| `observe_job("workqueue.sla_tick", …)` | existing pattern | Beat-task duration & status |

---

## 15. Open questions

None blocking. Items deferred to future iterations (see §2 non-goals): AI-augmented scoring, persistent dismissal, bulk actions, in-queue filters/search, mobile gestures, cross-user delegation, per-user customization.

---

## 16. Implementation order (preview)

The implementation plan (next document) will sequence these slices, each independently shippable behind the feature flag:

1. Models + migration + permissions seed.
2. Provider interface + types + aggregator (no UI yet — covered by service tests).
3. Conversations provider + Tickets provider (the two highest-volume kinds).
4. Routes + templates + macros — full-page render with polling only.
5. Snooze CRUD + UI.
6. Inline claim + complete actions.
7. Leads/Quotes provider + Tasks provider.
8. WS channel + emit points + client live-update wiring.
9. SLA-tick Celery task + prune task + sidebar badge.
10. Observability + metrics + E2E tests.
