# Chatwoot vs DotMac Omni — Feature Gap Analysis

Generated: 2026-02-24

## What We Already Have (Parity or Better)

| Feature | Status |
|---------|--------|
| Multi-channel inbox (Email, WhatsApp, FB, Instagram, Widget) | **Parity** |
| Conversation lifecycle (open/pending/snoozed/resolved) | **Parity** |
| Message attachments & file uploads | **Parity** |
| Private notes (internal messages) | **Parity** |
| Contact management (CRUD, merge, dedup, channel linking) | **Parity** |
| Teams & agent management | **Parity** |
| Conversation assignment (agent + team) | **Parity** |
| Agent presence (online/away/offline) | **Better** (GPS location tracking) |
| Email integration (IMAP polling + SMTP) | **Parity** |
| WhatsApp Cloud API + templates | **Parity** |
| Meta webhook processing (FB/IG/WA) | **Parity** |
| CSAT surveys (per-target, email-based) | **Parity** |
| Sales pipeline / Leads / Quotes | **Better** (Chatwoot has none) |
| Email + WhatsApp campaigns (one-time + nurture) | **Better** (Chatwoot only has basic campaigns) |
| AI reply suggestions (Claude-powered) | **Better** (native, not a bolt-on integration) |
| Outbound message queue with retry/backoff | **Parity** |
| Audit logging | **Parity** |
| RBAC permissions per conversation | **Parity** |
| Message templates / canned responses | **Parity** |
| Conversation search & filtering | **Parity** |
| Circuit breaker for external APIs | **Parity** |

---

## Feature Gaps (What Chatwoot Has, We Don't)

### Priority 1 — High Impact, Core Inbox

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 1 | **Conversation Priority** (Urgent/High/Medium/Low) | Built-in field + sort + filter | **Missing** | Small |
| 2 | **Snooze Options** (1hr, tomorrow, next week, next reply, custom) | Full snooze scheduler | **Partial** — status exists, no scheduler | Medium |
| 3 | **Conversation Labels** (color-coded, multi-label) | Account-level labels, color-coded, filter/report | **Basic** — ConversationTag is string-only, no color | Medium |
| 4 | **Bulk Actions** (select multiple → assign/label/resolve) | Checkbox select + batch operations | **Missing** | Medium |
| 5 | **Typing Indicators** (agent ↔ customer, agent ↔ agent) | WebSocket-based real-time | **Missing** | Medium |
| 6 | **Collision Detection** (see who else is viewing/replying) | Visual indicator per conversation | **Missing** | Medium |
| 7 | **Conversation Mute** (stop notifications for a thread) | Per-conversation toggle | **Missing** | Small |
| 8 | **Email CC/BCC** support | Full CC/BCC in email replies | **Missing** | Medium |
| 9 | **Send Email Transcript** | One-click full conversation export to email | **Missing** | Small |
| 10 | **Rich Messages** (cards, carousels, quick-reply buttons) | Interactive message types in widget | **Missing** | Large |

### Priority 2 — Agent Productivity

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 11 | **Macros** (saved multi-step action sequences) | Define → one-click execute: label + assign + resolve | **Missing** | Medium |
| 12 | **Canned Response Shortcodes** (type `/greeting` to insert) | `/` trigger in reply editor | **Partial** — templates exist but no shortcode trigger | Small |
| 13 | **Keyboard Shortcuts** (Cmd+/ to see all, Alt+P for note) | Comprehensive shortcut set | **Missing** | Small |
| 14 | **Command Bar** (Cmd+K quick navigation) | Global search + action palette | **Partial** — global search exists, no action palette | Medium |
| 15 | **Saved Filters / Folders** (save filtered views as sidebar items) | Persistent named segments in sidebar | **Missing** | Medium |
| 16 | **Dynamic Variables in Canned Responses** (`{{contact.name}}`) | Auto-substitution in templates | **Partial** — campaigns have variables, templates don't | Small |

### Priority 3 — Reporting & Analytics

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 17 | **Conversation Reports** (count, FRT, resolution time, handle time) | Full dashboard with charts + date range | **Basic** | Large |
| 18 | **Agent Reports** (per-agent conversations, response time, CSAT) | Per-agent performance dashboard | **Partial** | Large |
| 19 | **Inbox/Channel Reports** (per-channel volume, response time) | Per-inbox metrics | **Missing** | Medium |
| 20 | **Team Reports** (team-level KPIs) | Team dashboard | **Missing** | Medium |
| 21 | **Label Reports** (conversation metrics per label) | Label-based analytics | **Missing** | Medium |
| 22 | **SLA Reports** (hit rate, miss log, breach details) | Full SLA reporting dashboard | **Missing** | Medium |
| 23 | **Live View / Real-Time Dashboard** (active convos, agent states) | Real-time ops dashboard | **Basic** | Medium |
| 24 | **Business Hours Toggle in Reports** | Filter metrics by business hours only | **Missing** | Medium |
| 25 | **Report Export** (CSV/PDF download) | Downloadable reports | **Missing** | Small |

### Priority 4 — Automation & SLA

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 26 | **SLA Policies** (FRT, NRT, Resolution Time targets) | Named policies with metric targets | **Missing** | Large |
| 27 | **SLA Assignment via Automation** | Auto-assign SLA based on conditions | **Missing** | Medium |
| 28 | **Automation Rules** (event → condition → action) | Full rule engine | **Partial** — automation_actions.py exists but limited | Large |
| 29 | **Auto-Resolve** (close conversations after N days idle) | Configurable account-wide setting | **Missing** | Small |

### Priority 5 — Self-Service & Knowledge Base

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 30 | **Help Center / Knowledge Base** (articles, categories, portal) | Full KB with custom domain, SEO, multilingual | **Missing** | Very Large |
| 31 | **KB Search in Widget** (customers search articles before chatting) | Articles surfaced in chat widget | **Missing** | Large |
| 32 | **AI Content Gap Detection** (spot unanswered questions) | Captain identifies missing KB articles | **Missing** | Medium |
| 33 | **Agent Article Insert** (paste KB article into conversation) | One-click insert from sidebar | **Missing** | Small |

### Priority 6 — Widget & Channel Enhancements

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 34 | **Pre-Chat Form** (name, email, phone, custom fields before chat) | Configurable per inbox with custom attributes | **Missing** | Medium |
| 35 | **Widget Visual Customization** (colors, position, bubble style) | Visual builder in settings | **Basic** — ChatWidgetConfig exists | Small |
| 36 | **Widget Business Hours** (off-hours message when team unavailable) | Per-inbox business hours + away message | **Missing** | Medium |
| 37 | **Widget SDK Methods** (setUser, setLocale, setCustomAttributes) | Full JavaScript SDK | **Unknown** | Medium |
| 38 | **HMAC Identity Validation** (secure logged-in user verification) | Built-in for widget | **Missing** | Small |
| 39 | **SMS Channel** (Twilio/Bandwidth SMS) | Native SMS support | **Missing** | Large |
| 40 | **Telegram Channel** | Bot-based integration | **Missing** | Medium |
| 41 | **LINE Channel** | LINE API integration | **Missing** | Medium |

### Priority 7 — Integration & Platform

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 42 | **Slack Integration** (reply from Slack, notifications) | Native two-way Slack sync | **Missing** | Large |
| 43 | **Dashboard Apps** (embed external apps in conversation sidebar) | Custom iframe/tab per conversation | **Missing** | Medium |
| 44 | **Contact Custom Attributes** (text, number, date, list, checkbox) | Typed custom fields on contacts | **Missing** | Medium |
| 45 | **Conversation Custom Attributes** | Typed custom fields on conversations | **Missing** | Medium |
| 46 | **Contact Import/Export CSV** | Built-in import + export | **Missing** | Small |
| 47 | **Webhooks (Account-Level)** | CRUD webhooks with event subscription | **Partial** | Medium |
| 48 | **Client/Public API** (end-user facing API for widget) | Separate public API namespace | **Partial** — widget_public.py exists | Small |

### Priority 8 — Nice to Have

| # | Feature | Chatwoot | Our Status | Effort |
|---|---------|----------|------------|--------|
| 49 | **Google Translate Integration** (real-time translation) | 100+ languages | **Missing** | Small |
| 50 | **Video Calling** (Dyte integration) | In-conversation video call | **Missing** | Large |
| 51 | **Contact Segments** (saved filtered contact views) | Named segments in sidebar | **Missing** | Medium |
| 52 | **Conversation Participants** (add watchers who get notifications) | Add agents as watchers | **Missing** | Small |
| 53 | **Multi-Language Dashboard** (30+ languages) | Full i18n | **Missing** | Very Large |
| 54 | **Mobile App** (iOS/Android) | Native apps with push notifications | **Missing** | Very Large |
| 55 | **Agent Notification Preferences** (per-type toggle) | Audio/email/push per agent | **Missing** | Medium |
| 56 | **Audio Alerts** (in-browser new message sound) | Configurable audio alerts | **Missing** | Small |

---

## Implementation Roadmap

### Wave 1 — Quick Wins (1-2 days each)
Items: #1 (Priority), #7 (Mute), #9 (Email Transcript), #12 (Shortcodes), #13 (Keyboard Shortcuts), #29 (Auto-Resolve), #56 (Audio Alerts)

### Wave 2 — Core Inbox Gaps (3-5 days each)
Items: #2 (Snooze scheduler), #3 (Labels upgrade), #4 (Bulk Actions), #8 (Email CC/BCC), #11 (Macros), #15 (Saved Filters), #52 (Participants)

### Wave 3 — Analytics Foundation (1-2 weeks)
Items: #17-22 (Full reporting suite), #25 (Report Export), #23 (Live View)

### Wave 4 — Automation & SLA (2-3 weeks)
Items: #26 (SLA Policies), #27 (SLA Automation), #28 (Rule Engine), #34 (Pre-Chat Form)

### Wave 5 — Platform Extensions
Items: #39 (SMS), #40 (Telegram), #42 (Slack), #44-45 (Custom Attributes), #47 (Webhooks)

### Defer / Skip
Items: #30-33 (Knowledge Base), #50 (Video), #53 (i18n), #54 (Mobile App)
