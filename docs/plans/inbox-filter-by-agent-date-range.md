# Plan: Filter Inbox Conversations by Assignee Agent + Date Range

## Context

Currently the CRM inbox sidebar has four assignment filter pills: "All", "Assigned to me", "My Team", and "Unassigned". There is no way to view conversations assigned to a **specific** agent, or to filter by the **date** an agent picked up a conversation. This makes it hard for supervisors to review workload distribution or audit when conversations were claimed.

The goal is to add a fifth "By Agent" filter mode with an agent dropdown and optional date range inputs (assigned from / assigned to), so users can answer: "Show me all conversations that Agent X picked up between Date A and Date B."

## Files to Modify

### 1. `app/services/crm/inbox/queries.py` — SQL filter logic
- Add three new params to `list_inbox_conversations()`: `filter_agent_id: str | None`, `assigned_from: datetime | None`, `assigned_to: datetime | None`
- Add a new `elif assignment_filter == "agent":` block (after the existing `my_team` block at line 132):
  ```python
  elif assignment_filter == "agent":
      if not filter_agent_id:
          return []
      agent_uuid = coerce_uuid(filter_agent_id)
      agent_subq = (
          db.query(ConversationAssignment.conversation_id)
          .filter(ConversationAssignment.is_active.is_(True))
          .filter(ConversationAssignment.agent_id == agent_uuid)
      )
      if assigned_from:
          agent_subq = agent_subq.filter(ConversationAssignment.assigned_at >= assigned_from)
      if assigned_to:
          agent_subq = agent_subq.filter(ConversationAssignment.assigned_at <= assigned_to)
      query = query.filter(Conversation.id.in_(agent_subq.distinct()))
  ```
  Note: the local variable is `assignment_filter` (derived from `assignment` param at line 77).
- Also pass the three new params through the `InboxQueries.list_conversations()` static method (line 562)

### 2. `app/services/crm/inbox/listing.py` — Pass-through params
- Add `filter_agent_id`, `assigned_from`, `assigned_to` params to `load_inbox_list()`
- Include them in `cache_params` dict (line 52) for proper cache keying
- Pass them through to `list_inbox_conversations()` (line 108)

### 3. `app/services/crm/inbox/page_context.py` — Context builder
- Add `filter_agent_id`, `assigned_from`, `assigned_to` params to both:
  - `build_inbox_page_context()` (line 95, async)
  - `build_inbox_conversations_partial_context()` (line 308, async)
- Pass through to `load_inbox_list()`
- Add `current_filter_agent_id`, `current_assigned_from`, `current_assigned_to` to the returned context dicts

### 4. `app/web/admin/crm_inbox_conversations.py` — HTMX route
- Add query params: `agent_id: str | None`, `assigned_from: str | None`, `assigned_to: str | None`
- Parse `assigned_from`/`assigned_to` as dates with timezone conversion:
  ```python
  from datetime import datetime, time, UTC
  def _parse_date_param(value: str | None, *, end_of_day: bool = False) -> datetime | None:
      if not value:
          return None
      try:
          d = datetime.strptime(value.strip(), "%Y-%m-%d")
          t = time.max if end_of_day else time.min
          return datetime.combine(d.date(), t, tzinfo=UTC)
      except ValueError:
          return None
  ```
- Pass to `build_inbox_conversations_partial_context()`

### 5. `app/web/admin/crm.py` — Full-page inbox route
- Add the same three query params: `agent_id`, `assigned_from`, `assigned_to`
- Parse dates using the same helper and pass to `build_inbox_page_context()`

### 6. `templates/admin/crm/inbox.html` — UI changes

**Alpine.js state** (in `x-data`, ~line 748):
- Add: `currentFilterAgentId: '{{ current_filter_agent_id | default("") }}'`
- Add: `currentAssignedFrom: '{{ current_assigned_from | default("") }}'`
- Add: `currentAssignedTo: '{{ current_assigned_to | default("") }}'`

**Assignment filter pills** (~line 283):
- Add `flex-wrap` to the pill container for mobile responsiveness
- Add a fifth "By Agent" pill button after "Unassigned":
  ```html
  <button @click="setAssignment('agent')" ...>By Agent</button>
  ```

**Collapsible agent filter panel** (below the pill row, ~line 305):
- Show when `currentAssignment === 'agent'`, using `x-show` + `x-transition`
- Contains:
  - Agent `<select>` dropdown populated from existing `agents` + `agent_labels` template vars
  - Two `<input type="date">` fields for "From" and "To"
  - All three trigger `filterConversations()` on change

**`filterConversations()` JS** (~line 1999):
- Add to URLSearchParams:
  ```js
  if (this.currentAssignment === 'agent' && this.currentFilterAgentId) {
      params.set('agent_id', this.currentFilterAgentId);
  }
  if (this.currentAssignedFrom) params.set('assigned_from', this.currentAssignedFrom);
  if (this.currentAssignedTo) params.set('assigned_to', this.currentAssignedTo);
  ```

**`setAssignment()` method** (~line 1847):
- Clear agent filter fields when switching away from 'agent' mode

### 7. `tests/test_inbox_agent_date_filter.py` — Tests (new file)

Test cases:
- `test_agent_filter_returns_assigned_conversations` — assignment="agent" with valid agent_id returns only that agent's conversations
- `test_agent_filter_with_date_from_only` — assigned_from filters out conversations assigned before that date
- `test_agent_filter_with_date_to_only` — assigned_to filters out conversations assigned after that date
- `test_agent_filter_with_date_range` — both from+to narrows results correctly
- `test_agent_filter_missing_agent_id_returns_empty` — assignment="agent" without agent_id returns []
- `test_existing_filters_unchanged` — "assigned", "unassigned", "my_team" still work as before

## Existing Code to Reuse
- `agents` list and `agent_labels` dict — already in template context from `crm_service.get_agent_team_options(db)` (page_context.py:230, 299)
- `ConversationAssignment.agent_id` and `ConversationAssignment.assigned_at` — existing model fields (confirmed in `app/models/crm/conversation.py`)
- `coerce_uuid()` from `app/services/common` — for agent_id validation
- Existing assignment filter pattern in queries.py (lines 78-132) — follow the same subquery style
- Cache key builder in `inbox_cache.build_inbox_list_key()` — automatically picks up new dict keys (JSON-serializes params dict)

## Verification

1. **Syntax check**: `python3 -c "import ast; ast.parse(open('app/services/crm/inbox/queries.py').read())"`  (repeat for all modified .py files)
2. **Lint**: `ruff check app/services/crm/inbox/ app/web/admin/crm.py app/web/admin/crm_inbox_conversations.py`
3. **App boot**: `docker compose restart app` then check logs for tracebacks
4. **Endpoint test**:
   - `GET /admin/crm/inbox` — page loads without errors
   - `GET /admin/crm/inbox/conversations?assignment=agent&agent_id=<uuid>` — returns filtered list
   - `GET /admin/crm/inbox/conversations?assignment=agent&agent_id=<uuid>&assigned_from=2026-02-01&assigned_to=2026-02-23` — returns date-filtered list
   - Existing filters (assigned, unassigned, my_team) still work unchanged
5. **UI check**: Open inbox in browser, click "By Agent" pill, verify dropdown shows agents, select one, verify list filters. Add date range, verify further filtering. Switch back to "All", verify agent panel hides and full list returns.
6. **Run tests**: `pytest tests/test_inbox_agent_date_filter.py -v` — new tests pass
7. **Regression**: `pytest tests/ -k inbox -x` — no regressions
