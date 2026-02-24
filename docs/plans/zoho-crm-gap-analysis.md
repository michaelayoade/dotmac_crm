# Zoho CRM vs DotMac CRM — Gap Analysis

**Date:** 2026-02-24

## Legend

- **Have** = Feature exists in DotMac
- **Partial** = Some capability exists but incomplete vs Zoho
- **Gap** = Feature missing entirely

---

## 1. Sales Automation

| Feature | Status | Notes |
|---------|--------|-------|
| Lead management & scoring | **Partial** | Leads exist with pipeline/stages, but no **rule-based lead scoring** (points for opens, clicks, site visits) |
| Lead assignment rules | **Partial** | Manual assignment exists; no **round-robin**, **load-balanced**, or **criteria-based auto-assignment** |
| Contact/Account hierarchy | **Gap** | No parent-child account relationships or account hierarchy |
| Contact roles per deal | **Gap** | No way to define decision-maker/influencer roles on leads |
| Multiple pipelines | **Have** | Custom pipelines with stages |
| Deal aging alerts | **Gap** | No stagnant-deal alerting |
| Sales forecasting | **Gap** | No quota management, top-down/bottom-up forecasting, or forecast categories |
| Territory management | **Gap** | No hierarchical territories with auto-assignment rules |
| Expected revenue (probability-weighted) | **Have** | Stage probability exists |

## 2. Marketing Automation

| Feature | Status | Notes |
|---------|--------|-------|
| Campaign management | **Have** | Email + WhatsApp campaigns, nurture sequences |
| Campaign ROI tracking | **Gap** | No budget/cost vs revenue tracking per campaign |
| A/B testing | **Gap** | No subject line or content variant testing |
| Drip/nurture campaigns | **Have** | Multi-step with delays |
| Email analytics (open/click) | **Have** | Open/click count tracking |
| Social media marketing | **Gap** | No social posting, listening, or engagement |
| Web forms (drag & drop) | **Gap** | No embeddable form builder that auto-creates leads |
| Landing pages | **Gap** | Not in scope |
| RFM segmentation | **Gap** | No recency/frequency/monetary scoring |
| Dynamic segments | **Partial** | Segment filters exist for campaigns but no auto-updating dynamic segments |

## 3. Omnichannel Communication

| Feature | Status | Notes |
|---------|--------|-------|
| Email (IMAP/SMTP) | **Have** | Full inbound/outbound with connectors |
| WhatsApp Business | **Have** | Connector-based integration |
| Facebook Messenger | **Have** | Via Meta webhooks |
| Instagram DM | **Have** | Via Meta webhooks |
| Web chat widget | **Have** | Embedded widget with WebSocket |
| SMS | **Partial** | Notification-level SMS; no bidirectional SMS inbox channel |
| Telephony/VoIP | **Gap** | No click-to-call, call logging, or telephony integration |
| SalesSignals (real-time notification hub) | **Gap** | No unified cross-channel notification feed for reps |
| Email scheduling | **Gap** | No schedule-for-later on individual messages |
| Visitor tracking | **Gap** | No website visitor identification or page tracking |

## 4. Customer Support

| Feature | Status | Notes |
|---------|--------|-------|
| Ticket management | **Have** | Full CRUD, multi-assignment, priorities |
| SLA policies | **Have** | Priority-based SLA targets with breach tracking |
| SLA escalation | **Partial** | Breach detection exists; no **automatic escalation chains** (reassign to manager) |
| Knowledge base | **Gap** | No article management, help center, or self-service KB |
| Customer self-service portal | **Gap** | No customer-facing ticket submission/tracking portal |
| CSAT on tickets | **Gap** | CSAT exists for inbox conversations only, not tickets |
| Canned/template responses | **Partial** | Inbox has templates; tickets don't have canned responses |

## 5. Analytics & Reporting

| Feature | Status | Notes |
|---------|--------|-------|
| Agent performance dashboards | **Have** | Presence, response time, review metrics |
| CRM pipeline analytics | **Partial** | Basic metrics; no **funnel visualization**, **win/loss analysis by reason**, or **cohort analysis** |
| Custom report builder | **Gap** | No drag-and-drop report builder or cross-module reports |
| Scheduled report emails | **Gap** | No auto-email of reports to stakeholders |
| KPI widgets with targets | **Gap** | No target-vs-actual gauges or goal tracking |
| Anomaly detection | **Gap** | No statistical deviation flagging on trends |
| Export formats (PDF/Excel) | **Gap** | No formatted report exports |
| Dashboard sharing / TV mode | **Gap** | No public dashboard links or presentation mode |

## 6. Process Management

| Feature | Status | Notes |
|---------|--------|-------|
| Workflow rules (event-based) | **Have** | Automation rules with conditions and actions |
| Status transitions | **Have** | Configurable allowed transitions |
| Blueprint (guided process) | **Gap** | No visual process designer enforcing mandatory steps per transition |
| Approval processes | **Gap** | No multi-level approval chains (e.g., discount > 20% requires VP approval) |
| Cadences (multi-channel sequences) | **Gap** | Nurture campaigns exist but no sales-rep-level cadences with call/email/task mixing |
| Scoring rules (with decay) | **Gap** | No lead/contact scoring engine |
| Review process | **Gap** | No record review workflow before finalization |
| Scheduled actions (delayed) | **Gap** | No "fire action N days after event" delayed triggers |

## 7. AI Features

| Feature | Status | Notes |
|---------|--------|-------|
| AI insights/analysis | **Partial** | Persona-based analysis exists but narrow |
| Deal win/loss prediction | **Gap** | No ML-based deal probability predictions |
| Lead conversion prediction | **Gap** | No predictive lead scoring |
| Sentiment analysis | **Gap** | No email/message sentiment classification |
| Best time to contact | **Gap** | No optimal contact time suggestions |
| Data enrichment | **Gap** | No web-crawl enrichment of company/contact data |
| AI email drafting | **Gap** | No generative email composition from conversation context |
| AI recommendations (next best action) | **Gap** | No suggested next steps per deal |
| Conversational AI (chat with CRM) | **Gap** | No natural language query interface |

## 8. Customization

| Feature | Status | Notes |
|---------|--------|-------|
| Custom fields | **Partial** | JSON metadata columns provide flexibility but no **admin-managed custom field definitions** |
| Custom modules | **Gap** | No user-defined data entities |
| Multiple page layouts per module | **Gap** | Fixed layouts per entity |
| Validation rules (admin-configurable) | **Gap** | Validation is in code, not admin-configurable |
| Subforms (inline line items) | **Have** | Quote line items, material request items |
| Canvas/custom record views | **Gap** | No WYSIWYG record view designer |
| Wizards (step-by-step) | **Gap** | No guided multi-step record creation |

## 9. Data Management

| Feature | Status | Notes |
|---------|--------|-------|
| CSV import | **Partial** | Chatwoot importer exists; no **generic CSV import for any module** |
| Data export (CSV/Excel) | **Gap** | No bulk export across modules |
| Deduplication | **Partial** | Conversation dedup exists; no **contact/lead dedup with merge** |
| Data backup | **Gap** | No in-app data backup/download |
| Recycle bin | **Gap** | Soft deletes exist but no user-facing recycle bin to restore |
| Import from other CRMs | **Gap** | Only Chatwoot import; no Salesforce/HubSpot/Pipedrive importers |

## 10. Integrations

| Feature | Status | Notes |
|---------|--------|-------|
| REST API | **Have** | Full API with JWT auth |
| Outbound webhooks | **Have** | Event-triggered with retry |
| Meta (FB/IG) | **Have** | OAuth + webhooks |
| Splynx | **Have** | Customer sync |
| ERP integration | **Have** | DotMac ERP + ERPNext mappers |
| Google Workspace sync | **Gap** | No Gmail/Calendar/Drive integration |
| Microsoft 365 sync | **Gap** | No Outlook/Teams integration |
| Zapier/iPaaS | **Gap** | No connector to automation platforms |
| Marketplace/extensions | **Gap** | No plugin/extension architecture |
| OAuth2 for third-party apps | **Gap** | No OAuth2 provider (only consumer) |

## 11. Mobile

| Feature | Status | Notes |
|---------|--------|-------|
| Mobile app | **Gap** | No native mobile app (responsive web only) |
| Offline access | **Gap** | No offline mode |
| Business card scanner | **Gap** | No OCR lead capture |
| Field rep check-in/out | **Gap** | Agent presence exists but no GPS check-in at customer sites |
| Route planning | **Gap** | No field visit route optimization |

## 12. Team Collaboration

| Feature | Status | Notes |
|---------|--------|-------|
| @mentions | **Have** | Agent/ticket mentions |
| Notes on records | **Have** | Comments on tickets, conversations |
| Tags | **Have** | Tags on tickets, conversations, leads |
| Activity feed | **Gap** | No central team activity stream |
| Gamification (leaderboards) | **Gap** | No sales competitions or badges |
| Team chat integration | **Partial** | Nextcloud Talk notifications; no Slack/Teams bot |

## 13. Inventory / CPQ

| Feature | Status | Notes |
|---------|--------|-------|
| Product catalog | **Have** | Inventory items with SKU, pricing |
| Stock tracking | **Have** | Per-location quantity tracking |
| Quote generation | **Have** | Quotes with line items |
| Sales orders | **Have** | Quote-to-order conversion |
| Price books (tiered pricing) | **Gap** | No multiple pricing tiers per customer segment |
| Purchase orders | **Gap** | Material requests exist but no formal PO to vendors |
| Invoices | **Gap** | Removed (legacy billing) — by design |
| eSignature | **Gap** | No digital quote acceptance |
| Multi-currency | **Gap** | No multi-currency support with auto-conversion |

## 14. Security & Admin

| Feature | Status | Notes |
|---------|--------|-------|
| RBAC (roles + permissions) | **Have** | Fine-grained domain:resource:action model |
| Multi-portal auth | **Have** | Admin, customer, vendor, reseller |
| CSRF protection | **Have** | Double-submit cookie pattern |
| Rate limiting | **Have** | API rate limiting with Redis |
| Audit logs | **Partial** | AI action audit exists; no **comprehensive record-level audit trail** |
| Field-level security | **Gap** | No per-profile field hide/read-only |
| SSO (SAML/OIDC) | **Gap** | No single sign-on with identity providers |
| 2FA | **Partial** | Vendor portal has MFA; admin portal does not |
| IP restriction | **Gap** | No IP whitelisting per role |
| Data sharing rules | **Gap** | No configurable record visibility rules (single-tenant mitigates need) |
| GDPR tools | **Gap** | No consent tracking, right-to-forget, or data portability features |

---

## Priority Gap Summary (Highest Impact)

### Tier 1 — High Value, Moderate Effort

1. **Lead scoring** — Rule-based scoring on engagement + demographics
2. **Auto-assignment rules** — Round-robin and criteria-based lead/ticket routing
3. **Knowledge base** — Help articles for customer self-service
4. **Custom report builder** — Cross-module reports with export
5. **Approval processes** — Multi-level approvals for quotes/discounts
6. **Comprehensive audit trail** — Record-level change history with before/after

### Tier 2 — High Value, Higher Effort

7. **Sales forecasting** — Quota management + forecast vs actual
8. **Blueprint/guided processes** — Visual process designer with mandatory steps
9. **Customer self-service portal** — Ticket submission + status tracking
10. **SSO (SAML/OIDC)** — Enterprise identity provider integration
11. **Admin 2FA** — TOTP/WebAuthn for admin portal
12. **Scheduled report delivery** — Auto-email reports on schedule

### Tier 3 — Differentiators (Longer Horizon)

13. **AI predictions** — Deal win probability, lead conversion scoring
14. **Cadences** — Multi-channel sales outreach sequences
15. **Telephony integration** — Click-to-call with call logging
16. **Google/Microsoft calendar sync**
17. **CSV import/export for all modules**

---

## Out of Scope

Many Zoho features are ecosystem plays that don't map to DotMac's single-tenant field-service focus:

- **Mobile native app** — Responsive web is sufficient for current deployment model
- **Marketplace/extensions** — Single-tenant doesn't need a plugin ecosystem
- **Gamification** — Nice-to-have but not core for ISP/telco operations
- **Canvas/WYSIWYG designer** — Engineering cost outweighs benefit at current scale
- **Kiosk Studio** — No-code flows are a platform play, not a CRM feature
- **Multi-currency** — Single-tenant deployments operate in one currency
- **Invoicing** — Intentionally removed; handled by external ERP
