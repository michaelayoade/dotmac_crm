# DotMac Omni CRM -- Complete Database Schema Reference

*Auto-generated on 2026-02-19 03:52 UTC from live database.*
*Run `python scripts/generate_schema_skill.py` to regenerate after migrations.*

## Summary

- **1 schemas**, **184 tables**

## Tables by Domain

### CRM (27 tables)
`crm_agent_location_pings`, `crm_agent_presence`, `crm_agent_presence_events`, `crm_agent_teams`, `crm_agents`, `crm_campaign_recipients`, `crm_campaign_senders`, `crm_campaign_smtp_configs`, `crm_campaign_steps`, `crm_campaigns`, `crm_conversation_assignments`, `crm_conversation_tags`, `crm_conversations`, `crm_leads`, `crm_message_attachments`, `crm_message_templates`, `crm_messages`, `crm_outbox`, `crm_pipeline_stages`, `crm_pipelines`, `crm_quote_line_items`, `crm_quotes`, `crm_routing_rules`, `crm_social_comment_replies`, `crm_social_comments`, `crm_team_channels`, `crm_teams`

### CRM/Sales (1 tables)
`quote_line_items`

### Dispatch (1 tables)
`dispatch_rules`

### Fiber/Network (16 tables)
`fiber_access_points`, `fiber_asset_merge_logs`, `fiber_change_requests`, `fiber_qa_remediation_logs`, `fiber_segments`, `fiber_splice_closures`, `fiber_splice_trays`, `fiber_splices`, `fiber_strands`, `fiber_termination_points`, `olt_card_ports`, `olt_cards`, `olt_devices`, `olt_power_units`, `olt_sfp_modules`, `olt_shelves`

### Inventory (6 tables)
`inventory_items`, `inventory_locations`, `inventory_reservations`, `inventory_stock`, `material_request_items`, `material_requests`

### Notifications (3 tables)
`notification_deliveries`, `notification_templates`, `notifications`

### Operations (2 tables)
`service_team_members`, `service_teams`

### Other (90 tables)
`agent_performance_goals`, `agent_performance_reviews`, `agent_performance_scores`, `agent_performance_snapshots`, `ai_insights`, `alert_notification_logs`, `alert_notification_policies`, `alert_notification_policy_steps`, `api_keys`, `as_built_routes`, `audit_events`, `automation_rule_logs`, `automation_rules`, `availability_blocks`, `bandwidth_samples`, `billing_rates`, `buildout_milestones`, `buildout_projects`, `buildout_requests`, `buildout_updates`, `chat_widget_configs`, `connector_configs`, `contract_signatures`, `cost_rates`, `coverage_areas`, `customer_notification_events`, `document_sequences`, `eta_updates`, `event_store`, `expense_lines`, `external_references`, `fdh_cabinets`, `geo_areas`, `geo_layers`, `geo_locations`, `installation_project_notes`, `installation_projects`, `integration_jobs`, `integration_runs`, `integration_targets`, `kpi_aggregates`, `kpi_configs`, `legal_documents`, `mfa_methods`, `nextcloud_talk_accounts`, `nextcloud_talk_notification_rooms`, `oauth_tokens`, `on_call_rotation_members`, `on_call_rotations`, `ont_assignments`, `ont_units`, `organization_memberships`, `organizations`, `people`, `pon_port_splitter_links`, `pon_ports`, `proposed_route_revisions`, `queue_mappings`, `sales_order_lines`, `sales_orders`, `scheduled_tasks`, `service_buildings`, `service_qualifications`, `sessions`, `shifts`, `skills`, `sla_breaches`, `sla_clocks`, `sla_policies`, `sla_targets`, `splitter_ports`, `splitters`, `survey_invitations`, `survey_los_paths`, `survey_points`, `survey_responses`, `surveys`, `technician_profiles`, `technician_skills`, `webhook_dead_letters`, `webhook_deliveries`, `webhook_endpoints`, `webhook_subscriptions`, `widget_visitor_sessions`, `wireguard_connection_logs`, `wireguard_peers`, `wireguard_servers`, `wireless_masts`, `wireless_site_surveys`, `work_logs`

### People/Auth (10 tables)
`permissions`, `person_channels`, `person_merge_logs`, `person_permissions`, `person_roles`, `person_status_logs`, `role_permissions`, `roles`, `user_credentials`, `user_filter_preferences`

### PostGIS (1 tables)
`spatial_ref_sys`

### Projects (11 tables)
`project_comments`, `project_quotes`, `project_task_assignees`, `project_task_comments`, `project_task_dependencies`, `project_task_status_transitions`, `project_tasks`, `project_template_task_dependency`, `project_template_tasks`, `project_templates`, `projects`

### Settings (1 tables)
`domain_settings`

### Subscribers (1 tables)
`subscribers`

### System (1 tables)
`alembic_version`

### Tickets (5 tables)
`ticket_assignees`, `ticket_comments`, `ticket_sla_events`, `ticket_status_transitions`, `tickets`

### Vendors (2 tables)
`vendor_users`, `vendors`

### Workforce (6 tables)
`work_order_assignment_queue`, `work_order_assignments`, `work_order_materials`, `work_order_notes`, `work_order_status_transitions`, `work_orders`

## PostGIS Geometry Columns

| Table | Column | Geometry Type | SRID |
|-------|--------|--------------|------|
| `public.as_built_routes` | `route_geom` | LINESTRING | 4326 |
| `public.coverage_areas` | `geom` | GEOMETRY | 4326 |
| `public.fdh_cabinets` | `geom` | POINT | 4326 |
| `public.fiber_access_points` | `geom` | POINT | 4326 |
| `public.fiber_segments` | `route_geom` | LINESTRING | 4326 |
| `public.fiber_splice_closures` | `geom` | POINT | 4326 |
| `public.geo_areas` | `geom` | GEOMETRY | 4326 |
| `public.geo_locations` | `geom` | POINT | 4326 |
| `public.proposed_route_revisions` | `route_geom` | LINESTRING | 4326 |
| `public.service_buildings` | `geom` | POINT | 4326 |
| `public.service_buildings` | `boundary_geom` | POLYGON | 4326 |
| `public.service_qualifications` | `geom` | POINT | 4326 |
| `public.survey_points` | `geom` | POINT | 4326 |
| `public.wireless_masts` | `geom` | POINT | 4326 |

## Enum Types (111 total)

- **`accountstatus`**: active, inactive, churned, suspended, archived
- **`accounttype`**: prospect, customer, partner, reseller, vendor, competitor, other
- **`agentpresencestatus`**: online, away, offline, on_break
- **`aiinsightstatus`**: pending, completed, failed, skipped, acknowledged, actioned, expired
- **`alertseverity`**: info, warning, critical, emergency
- **`alertstatus`**: open, acknowledged, resolved
- **`appointmentstatus`**: proposed, confirmed, completed, no_show, canceled
- **`asbuiltroutestatus`**: submitted, under_review, accepted, rejected
- **`auditactortype`**: system, user, api_key, service
- **`authprovider`**: local, sso, radius
- **`automationlogoutcome`**: success, partial_failure, failure, skipped
- **`automationrulestatus`**: active, paused, archived
- **`buildoutmilestonestatus`**: pending, in_progress, completed, blocked, canceled
- **`buildoutprojectstatus`**: planned, in_progress, blocked, ready, completed, canceled
- **`buildoutrequeststatus`**: submitted, approved, rejected, canceled
- **`buildoutstatus`**: planned, in_progress, ready, not_planned
- **`campaignchannel`**: email, whatsapp
- **`campaignrecipientstatus`**: pending, sent, delivered, failed, bounced, unsubscribed
- **`campaignstatus`**: draft, scheduled, sending, sent, completed, cancelled
- **`campaigntype`**: one_time, nurture
- **`channeltype`**: email, phone, sms, whatsapp, facebook_messenger, instagram_dm, note, chat_widget
- **`connectorauthtype`**: none, basic, bearer, hmac, api_key, oauth2
- **`connectortype`**: webhook, http, email, whatsapp, smtp, stripe, twilio, facebook, instagram, custom
- **`contactmethod`**: email, phone, sms, push
- **`conversationstatus`**: open, pending, snoozed, resolved
- **`customernotificationstatus`**: pending, sent, failed
- **`customersurveystatusenum`**: draft, active, paused, closed
- **`deliverystatus`**: accepted, delivered, failed, bounced, rejected
- **`dispatchqueuestatus`**: queued, assigned, skipped
- **`eventstatus`**: pending, processing, completed, failed
- **`externalentitytype`**: ticket, ticket_comment, project, project_task, work_order, work_order_note, person, subscriber, lead, quote, project_...
- **`fibercabletype`**: single_mode, multi_mode, armored, aerial, underground, direct_buried
- **`fiberchangerequestoperation`**: create, update, delete
- **`fiberchangerequeststatus`**: pending, applied, rejected
- **`fiberendpointtype`**: olt_port, splitter_port, fdh, ont, splice_closure, other
- **`fibersegmenttype`**: feeder, distribution, drop
- **`fiberstrandstatus`**: available, in_use, reserved, damaged, retired
- **`gender`**: unknown, female, male, non_binary, other
- **`geoareatype`**: coverage, service_area, region, custom
- **`geolayersource`**: locations, areas
- **`geolayertype`**: points, lines, polygons, heatmap, cluster
- **`geolocationtype`**: address, pop, site, customer, asset, custom
- **`goalstatus`**: active, achieved, missed, canceled
- **`insightdomain`**: tickets, inbox, projects, performance, vendors, dispatch, campaigns, customer_success
- **`insightseverity`**: info, suggestion, warning, critical
- **`installationprojectstatus`**: draft, open_for_bidding, quoted, approved, in_progress, completed, verified, assigned
- **`integrationjobtype`**: sync, export, import_
- **`integrationrunstatus`**: running, success, failed
- **`integrationscheduletype`**: manual, interval
- **`integrationtargettype`**: radius, crm, billing, n8n, custom
- **`leadstatus`**: new, contacted, qualified, proposal, negotiation, won, lost
- **`legaldocumenttype`**: terms_of_service, privacy_policy, acceptable_use, service_level_agreement, data_processing, cookie_policy, refund_pol...
- **`materialrequestpriority`**: low, medium, high, urgent
- **`materialrequeststatus`**: draft, submitted, approved, rejected, fulfilled, canceled, issued
- **`materialstatus`**: required, reserved, used
- **`messagedirection`**: inbound, outbound, internal
- **`messagestatus`**: received, queued, sent, failed
- **`mfamethodtype`**: totp, sms, email
- **`notificationchannel`**: email, sms, push, whatsapp, webhook
- **`notificationstatus`**: queued, sending, delivered, failed, canceled
- **`odnendpointtype`**: fdh, splitter, splitter_port, pon_port, olt_port, ont, terminal, splice_closure, other
- **`oltporttype`**: pon, uplink, ethernet, mgmt
- **`organizationmembershiprole`**: owner, admin, member
- **`partystatus`**: lead, contact, customer, subscriber
- **`performancedomain`**: support, operations, field_service, communication, sales, data_quality
- **`personstatus`**: active, inactive, archived
- **`project_taskstatus`**: backlog, todo, in_progress, blocked, done, canceled
- **`projectpriority`**: low, normal, high, urgent, lower, medium
- **`projectquotestatus`**: draft, submitted, under_review, approved, rejected, revision_requested
- **`projectstatus`**: open, planned, active, on_hold, completed, canceled
- **`projecttype`**: cable_rerun, fiber_optics_relocation, air_fiber_relocation, fiber_optics_installation, air_fiber_installation, cross_...
- **`proposedrouterevisionstatus`**: draft, submitted, accepted, rejected
- **`provisioning_taskstatus`**: pending, in_progress, blocked, completed, failed
- **`provisioningrunstatus`**: pending, running, success, failed
- **`provisioningsteptype`**: assign_ont, push_config, confirm_up
- **`provisioningvendor`**: mikrotik, huawei, zte, nokia, genieacs, other
- **`qualificationstatus`**: eligible, ineligible, needs_buildout
- **`quotestatus`**: draft, sent, accepted, rejected, expired
- **`reservationstatus`**: active, released, consumed
- **`salesorderpaymentstatus`**: pending, partial, paid, waived
- **`salesorderstatus`**: draft, confirmed, paid, fulfilled, cancelled
- **`scheduletype`**: interval
- **`serviceorderstatus`**: draft, submitted, scheduled, provisioning, active, canceled, failed
- **`servicestate`**: pending, installing, provisioning, active, suspended, canceled, disconnected
- **`serviceteammemberrole`**: member, lead, manager
- **`serviceteamtype`**: operations, support, field_service
- **`sessionstatus`**: active, revoked, expired
- **`settingdomain`**: auth, audit, billing, catalog, subscriber, imports, notification, network, network_monitoring, provisioning, geocodin...
- **`settingvaluetype`**: string, integer, boolean, decimal, json
- **`slabreachstatus`**: open, acknowledged, resolved
- **`slaclockstatus`**: running, paused, completed, breached
- **`socialcommentplatform`**: facebook, instagram
- **`splitterporttype`**: input, output
- **`subscriberstatus`**: active, suspended, terminated, pending
- **`surveyinvitationstatusenum`**: pending, sent, opened, completed, expired
- **`surveypointtype`**: tower, access_point, cpe, repeater, custom
- **`surveystatus`**: draft, in_progress, completed, archived
- **`surveytriggertypeenum`**: manual, ticket_closed, work_order_completed
- **`taskdependencytype`**: finish_to_start, start_to_start, finish_to_finish, start_to_finish
- **`taskpriority`**: low, normal, high, urgent, lower, medium
- **`ticketchannel`**: web, email, phone, chat, api
- **`ticketpriority`**: low, normal, high, urgent, lower, medium
- **`ticketstatus`**: new, open, pending, on_hold, resolved, closed, canceled, waiting_on_customer, lastmile_rerun, site_under_construction
- **`vendorassignmenttype`**: bidding, direct
- **`webhookdeliverystatus`**: pending, delivered, failed
- **`webhookeventtype`**: subscriber_created, subscriber_updated, subscriber_suspended, subscriber_reactivated, subscription_created, subscript...
- **`wireguardpeerstatus`**: active, disabled
- **`workflowentitytype`**: ticket, work_order, project_task
- **`workorderpriority`**: low, normal, high, urgent, lower, medium
- **`workorderstatus`**: draft, scheduled, dispatched, in_progress, completed, canceled
- **`workordertype`**: install, repair, survey, maintenance, disconnect, other

---

## `public` schema (184 tables)

### `public.agent_performance_goals`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| person_id | uuid |  |  |
| domain | performancedomain |  |  |
| metric_key | varchar(80) |  |  |
| label | varchar(200) |  |  |
| target_value | numeric(12,2) |  |  |
| current_value | numeric(12,2) | YES |  |
| comparison | varchar(10) |  |  |
| deadline | date |  |  |
| status | goalstatus |  | 'active'::goalstatus |
| created_by_person_id | uuid | YES |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `created_by_person_id` -> `public.people.id`
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_perf_goal_deadline`: (deadline)
- `ix_perf_goal_person_status`: (person_id, status)

### `public.agent_performance_reviews`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| person_id | uuid |  |  |
| review_period_start | timestamp with time zone |  |  |
| review_period_end | timestamp with time zone |  |  |
| composite_score | numeric(5,2) |  |  |
| domain_scores_json | json |  |  |
| summary_text | text |  |  |
| strengths_json | json |  |  |
| improvements_json | json |  |  |
| recommendations_json | json |  |  |
| callouts_json | json |  |  |
| llm_model | varchar(100) |  |  |
| llm_provider | varchar(40) |  |  |
| llm_tokens_in | integer | YES |  |
| llm_tokens_out | integer | YES |  |
| is_acknowledged | boolean |  | false |
| acknowledged_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_perf_review_ack`: (is_acknowledged)
- `ix_perf_review_person_period`: (person_id, review_period_start)
- `uq_perf_review_person_period`: (person_id, review_period_start, review_period_end) (unique)

### `public.agent_performance_scores`
PK: `id` | ~660 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| person_id | uuid |  |  |
| score_period_start | timestamp with time zone |  |  |
| score_period_end | timestamp with time zone |  |  |
| domain | performancedomain |  |  |
| raw_score | numeric(5,2) |  |  |
| weighted_score | numeric(5,2) |  |  |
| metrics_json | json | YES |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_perf_score_domain`: (domain)
- `ix_perf_score_period`: (score_period_start)
- `ix_perf_score_person_period`: (person_id, score_period_start)
- `uq_perf_score_person_period_domain`: (person_id, score_period_start, domain) (unique)

### `public.agent_performance_snapshots`
PK: `id` | ~110 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| person_id | uuid |  |  |
| team_id | uuid | YES |  |
| score_period_start | timestamp with time zone |  |  |
| score_period_end | timestamp with time zone |  |  |
| composite_score | numeric(5,2) |  |  |
| domain_scores_json | json |  |  |
| weights_json | json |  |  |
| team_type | varchar(40) | YES |  |
| sales_activity_ratio | numeric(8,4) | YES |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `team_id` -> `public.service_teams.id`

**Indexes:**
- `ix_perf_snapshot_composite`: (composite_score)
- `ix_perf_snapshot_period`: (score_period_start)
- `ix_perf_snapshot_team_period`: (team_id, score_period_start)
- `uq_perf_snapshot_person_period`: (person_id, score_period_start, score_period_end) (unique)

### `public.ai_insights`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| persona_key | varchar(80) |  |  |
| domain | insightdomain |  |  |
| severity | insightseverity |  | 'info'::insightseverity |
| status | aiinsightstatus |  | 'pending'::aiinsightstatus |
| entity_type | varchar(80) |  |  |
| entity_id | varchar(120) | YES |  |
| title | varchar(300) |  |  |
| summary | text |  |  |
| structured_output | json | YES |  |
| confidence_score | numeric(3,2) | YES |  |
| recommendations | json | YES |  |
| llm_provider | varchar(40) |  | 'vllm'::character varying |
| llm_model | varchar(100) |  |  |
| llm_tokens_in | integer | YES |  |
| llm_tokens_out | integer | YES |  |
| llm_endpoint | varchar(20) | YES |  |
| generation_time_ms | integer | YES |  |
| trigger | varchar(40) |  |  |
| triggered_by_person_id | uuid | YES |  |
| acknowledged_at | timestamp with time zone | YES |  |
| acknowledged_by_person_id | uuid | YES |  |
| expires_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone | YES |  |
| updated_at | timestamp with time zone | YES |  |
| context_quality_score | numeric(3,2) | YES |  |

**Foreign keys:**
- `acknowledged_by_person_id` -> `public.people.id`
- `triggered_by_person_id` -> `public.people.id`

**Indexes:**
- `ix_ai_insights_context_quality`: (context_quality_score)
- `ix_ai_insights_created`: (created_at)
- `ix_ai_insights_domain_status`: (domain, status)
- `ix_ai_insights_entity`: (entity_type, entity_id)
- `ix_ai_insights_persona`: (persona_key)
- `ix_ai_insights_severity`: (severity)

### `public.alembic_version`
PK: `version_num` | ~1 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **version_num** (PK) | varchar(32) |  |  |

### `public.alert_notification_logs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| policy_id | uuid |  |  |
| notification_id | uuid | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `notification_id` -> `public.notifications.id`
- `policy_id` -> `public.alert_notification_policies.id`

### `public.alert_notification_policies`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| channel | notificationchannel |  |  |
| recipient | varchar(255) |  |  |
| template_id | uuid | YES |  |
| severity_min | alertseverity |  |  |
| status | alertstatus |  |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `template_id` -> `public.notification_templates.id`

**Indexes:**
- `uq_alert_notification_policies_name`: (name) (unique)

### `public.alert_notification_policy_steps`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| policy_id | uuid |  |  |
| step_index | integer |  |  |
| delay_minutes | integer |  |  |
| channel | notificationchannel |  |  |
| recipient | varchar(255) | YES |  |
| template_id | uuid | YES |  |
| connector_config_id | uuid | YES |  |
| rotation_id | uuid | YES |  |
| severity_min | alertseverity |  |  |
| status | alertstatus |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`
- `policy_id` -> `public.alert_notification_policies.id`
- `rotation_id` -> `public.on_call_rotations.id`
- `template_id` -> `public.notification_templates.id`

### `public.api_keys`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid | YES |  |
| label | varchar(120) | YES |  |
| key_hash | varchar(255) |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| last_used_at | timestamp with time zone | YES |  |
| expires_at | timestamp with time zone | YES |  |
| revoked_at | timestamp with time zone | YES |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `api_keys_key_hash_key`: (key_hash) (unique)

### `public.as_built_routes`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| proposed_revision_id | uuid | YES |  |
| status | asbuiltroutestatus |  |  |
| route_geom (PostGIS: LINESTRING, SRID:4326) | geometry | YES |  |
| actual_length_meters | double precision | YES |  |
| submitted_at | timestamp with time zone | YES |  |
| submitted_by_person_id | uuid | YES |  |
| reviewed_at | timestamp with time zone | YES |  |
| reviewed_by_person_id | uuid | YES |  |
| review_notes | text | YES |  |
| fiber_segment_id | uuid | YES |  |
| report_file_path | varchar(500) | YES |  |
| report_file_name | varchar(255) | YES |  |
| report_generated_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `fiber_segment_id` -> `public.fiber_segments.id`
- `project_id` -> `public.installation_projects.id`
- `proposed_revision_id` -> `public.proposed_route_revisions.id`
- `reviewed_by_person_id` -> `public.people.id`
- `submitted_by_person_id` -> `public.people.id`

**Indexes:**
- `idx_as_built_routes_route_geom`: (route_geom)

### `public.audit_events`
PK: `id` | ~117,567 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| occurred_at | timestamp with time zone |  |  |
| actor_type | auditactortype |  |  |
| actor_id | varchar(120) | YES |  |
| action | varchar(80) |  |  |
| entity_type | varchar(160) |  |  |
| entity_id | varchar(120) | YES |  |
| status_code | integer |  |  |
| is_success | boolean |  |  |
| is_active | boolean |  |  |
| ip_address | varchar(64) | YES |  |
| user_agent | varchar(255) | YES |  |
| request_id | varchar(120) | YES |  |
| metadata | json | YES |  |

### `public.automation_rule_logs`
PK: `id` | ~2,940 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| rule_id | uuid |  |  |
| event_id | uuid |  |  |
| event_type | varchar(100) |  |  |
| outcome | automationlogoutcome |  |  |
| actions_executed | jsonb | YES |  |
| duration_ms | integer | YES |  |
| error | text | YES |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `rule_id` -> `public.automation_rules.id`

**Indexes:**
- `ix_automation_rule_logs_created_at`: (created_at)
- `ix_automation_rule_logs_rule_id`: (rule_id)

### `public.automation_rules`
PK: `id` | ~6 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(200) |  |  |
| description | text | YES |  |
| event_type | varchar(100) |  |  |
| conditions | jsonb | YES |  |
| actions | jsonb | YES |  |
| status | automationrulestatus |  | 'active'::automationrulestatus |
| priority | integer |  | 0 |
| stop_after_match | boolean |  | false |
| cooldown_seconds | integer |  | 0 |
| execution_count | integer |  | 0 |
| last_triggered_at | timestamp with time zone | YES |  |
| created_by_id | uuid | YES |  |
| is_active | boolean |  | true |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `created_by_id` -> `public.people.id`

**Indexes:**
- `ix_automation_rules_active_event`: (event_type, status, is_active, priority)

### `public.availability_blocks`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| technician_id | uuid |  |  |
| start_at | timestamp with time zone |  |  |
| end_at | timestamp with time zone |  |  |
| reason | varchar(160) | YES |  |
| is_available | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| block_type | varchar(60) | YES |  |
| erp_id | varchar(100) | YES |  |
| updated_at | timestamp with time zone | YES | now() |

**Foreign keys:**
- `technician_id` -> `public.technician_profiles.id`

**Indexes:**
- `ix_availability_blocks_erp_id`: (erp_id) (unique)

### `public.bandwidth_samples`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| subscription_id | uuid |  |  |
| device_id | uuid | YES |  |
| interface_id | uuid | YES |  |
| rx_bps | integer |  |  |
| tx_bps | integer |  |  |
| sample_at | timestamp with time zone |  |  |
| created_at | timestamp with time zone |  |  |

### `public.billing_rates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| hourly_rate | numeric(12,2) |  |  |
| currency | varchar(3) |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.buildout_milestones`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| name | varchar(160) |  |  |
| status | buildoutmilestonestatus |  |  |
| order_index | integer |  |  |
| due_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `project_id` -> `public.buildout_projects.id`

### `public.buildout_projects`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| request_id | uuid | YES |  |
| coverage_area_id | uuid | YES |  |
| address_id | uuid | YES |  |
| status | buildoutprojectstatus |  |  |
| progress_percent | integer |  |  |
| target_ready_date | timestamp with time zone | YES |  |
| started_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `coverage_area_id` -> `public.coverage_areas.id`
- `request_id` -> `public.buildout_requests.id`

### `public.buildout_requests`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| qualification_id | uuid | YES |  |
| coverage_area_id | uuid | YES |  |
| address_id | uuid | YES |  |
| requested_by | varchar(120) | YES |  |
| status | buildoutrequeststatus |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `coverage_area_id` -> `public.coverage_areas.id`
- `qualification_id` -> `public.service_qualifications.id`

### `public.buildout_updates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| status | buildoutprojectstatus |  |  |
| message | text | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `project_id` -> `public.buildout_projects.id`

### `public.chat_widget_configs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| connector_config_id | uuid | YES |  |
| allowed_domains | json | YES |  |
| primary_color | varchar(20) |  | '#3B82F6'::character varying |
| bubble_position | varchar(20) |  | 'bottom-right'::character varying |
| welcome_message | text | YES |  |
| placeholder_text | varchar(120) |  | 'Type a message...'::character varying |
| widget_title | varchar(80) |  | 'Chat with us'::character varying |
| offline_message | text | YES |  |
| prechat_form_enabled | boolean |  | false |
| prechat_fields | json | YES |  |
| business_hours | json | YES |  |
| rate_limit_messages_per_minute | integer |  | 10 |
| rate_limit_sessions_per_ip | integer |  | 5 |
| is_active | boolean |  | true |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`

### `public.connector_configs`
PK: `id` | ~7 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| connector_type | connectortype |  |  |
| base_url | varchar(500) | YES |  |
| auth_type | connectorauthtype |  |  |
| auth_config | json | YES |  |
| headers | json | YES |  |
| retry_policy | json | YES |  |
| timeout_sec | integer | YES |  |
| metadata | json | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_connector_configs_name`: (name) (unique)

### `public.contract_signatures`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| account_id | uuid | YES |  |
| document_id | uuid | YES |  |
| signer_name | varchar(200) |  |  |
| signer_email | varchar(255) |  |  |
| signed_at | timestamp with time zone |  |  |
| ip_address | varchar(45) |  |  |
| user_agent | varchar(500) | YES |  |
| agreement_text | text |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `document_id` -> `public.legal_documents.id`

### `public.cost_rates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid | YES |  |
| hourly_rate | numeric(12,2) |  |  |
| effective_from | timestamp with time zone | YES |  |
| effective_to | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

### `public.coverage_areas`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| code | varchar(80) | YES |  |
| zone_key | varchar(80) | YES |  |
| buildout_status | buildoutstatus |  |  |
| buildout_window | varchar(120) | YES |  |
| serviceable | boolean |  |  |
| priority | integer |  |  |
| geometry_geojson | json |  |  |
| geom (PostGIS: GEOMETRY, SRID:4326) | geometry | YES |  |
| min_latitude | double precision | YES |  |
| max_latitude | double precision | YES |  |
| min_longitude | double precision | YES |  |
| max_longitude | double precision | YES |  |
| constraints | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `idx_coverage_areas_geom`: (geom)

### `public.crm_agent_location_pings`
PK: `id` | ~1,684 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| agent_id | uuid |  |  |
| latitude | double precision |  |  |
| longitude | double precision |  |  |
| accuracy_m | double precision | YES |  |
| captured_at | timestamp with time zone |  |  |
| received_at | timestamp with time zone |  | now() |
| source | varchar(32) |  | 'browser'::character varying |

**Foreign keys:**
- `agent_id` -> `public.crm_agents.id`

**Indexes:**
- `ix_crm_agent_location_pings_agent_received`: (agent_id, received_at)
- `ix_crm_agent_location_pings_received_at`: (received_at)

### `public.crm_agent_presence`
PK: `id` | ~81 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| agent_id | uuid |  |  |
| status | agentpresencestatus |  |  |
| last_seen_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| manual_override_status | agentpresencestatus | YES |  |
| manual_override_set_at | timestamp with time zone | YES |  |
| location_sharing_enabled | boolean |  | false |
| last_latitude | double precision | YES |  |
| last_longitude | double precision | YES |  |
| last_location_accuracy_m | double precision | YES |  |
| last_location_at | timestamp with time zone | YES |  |

**Foreign keys:**
- `agent_id` -> `public.crm_agents.id`

**Indexes:**
- `ix_crm_agent_presence_last_seen_at`: (last_seen_at)
- `ix_crm_agent_presence_manual_override_status`: (manual_override_status)
- `ix_crm_agent_presence_status`: (status)
- `uq_crm_agent_presence_agent_id`: (agent_id) (unique)

### `public.crm_agent_presence_events`
PK: `id` | ~4,639 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| agent_id | uuid |  |  |
| status | agentpresencestatus |  |  |
| started_at | timestamp with time zone |  |  |
| ended_at | timestamp with time zone | YES |  |
| source | varchar(32) |  | 'auto'::character varying |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `agent_id` -> `public.crm_agents.id`

**Indexes:**
- `ix_crm_agent_presence_events_agent_id`: (agent_id)
- `ix_crm_agent_presence_events_ended_at`: (ended_at)
- `ix_crm_agent_presence_events_started_at`: (started_at)
- `ix_crm_agent_presence_events_status`: (status)

### `public.crm_agent_teams`
PK: `id` | ~21 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| agent_id | uuid |  |  |
| team_id | uuid |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `agent_id` -> `public.crm_agents.id`
- `team_id` -> `public.crm_teams.id`

**Indexes:**
- `uq_crm_agent_team`: (agent_id, team_id) (unique)

### `public.crm_agents`
PK: `id` | ~81 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| is_active | boolean |  |  |
| title | varchar(120) | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

### `public.crm_campaign_recipients`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| campaign_id | uuid |  |  |
| person_id | uuid |  |  |
| step_id | uuid | YES |  |
| email | varchar(255) | YES |  |
| status | campaignrecipientstatus | YES | 'pending'::campaignrecipientstatus |
| notification_id | uuid | YES |  |
| sent_at | timestamp with time zone | YES |  |
| delivered_at | timestamp with time zone | YES |  |
| failed_reason | text | YES |  |
| created_at | timestamp with time zone | YES | now() |
| address | varchar(255) |  |  |

**Foreign keys:**
- `campaign_id` -> `public.crm_campaigns.id`
- `notification_id` -> `public.notifications.id`
- `person_id` -> `public.people.id`
- `step_id` -> `public.crm_campaign_steps.id`

**Indexes:**
- `ix_crm_campaign_recipients_campaign_id`: (campaign_id)
- `ix_crm_campaign_recipients_person_id`: (person_id)
- `ix_crm_campaign_recipients_status`: (status)
- `uq_campaign_person_step`: (campaign_id, person_id, step_id) (unique)

### `public.crm_campaign_senders`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| from_name | varchar(160) | YES |  |
| from_email | varchar(255) |  |  |
| reply_to | varchar(255) | YES |  |
| is_active | boolean | YES | true |
| created_at | timestamp with time zone | YES | now() |
| updated_at | timestamp with time zone | YES | now() |

**Indexes:**
- `uq_crm_campaign_senders_from_email`: (from_email) (unique)

### `public.crm_campaign_smtp_configs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| host | varchar(255) |  |  |
| port | integer | YES | 587 |
| username | varchar(255) | YES |  |
| password | varchar(255) | YES |  |
| use_tls | boolean | YES | true |
| use_ssl | boolean | YES | false |
| is_active | boolean | YES | true |
| created_at | timestamp with time zone | YES | now() |
| updated_at | timestamp with time zone | YES | now() |

**Indexes:**
- `uq_crm_campaign_smtp_configs_name`: (name) (unique)

### `public.crm_campaign_steps`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| campaign_id | uuid |  |  |
| step_index | integer | YES | 0 |
| name | varchar(200) | YES |  |
| subject | varchar(200) | YES |  |
| body_html | text | YES |  |
| body_text | text | YES |  |
| delay_days | integer | YES | 0 |
| created_at | timestamp with time zone | YES | now() |
| updated_at | timestamp with time zone | YES | now() |

**Foreign keys:**
- `campaign_id` -> `public.crm_campaigns.id`

**Indexes:**
- `ix_crm_campaign_steps_campaign_id`: (campaign_id)

### `public.crm_campaigns`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(200) |  |  |
| campaign_type | campaigntype | YES | 'one_time'::campaigntype |
| status | campaignstatus | YES | 'draft'::campaignstatus |
| subject | varchar(200) | YES |  |
| body_html | text | YES |  |
| body_text | text | YES |  |
| from_name | varchar(160) | YES |  |
| from_email | varchar(255) | YES |  |
| reply_to | varchar(255) | YES |  |
| segment_filter | json | YES |  |
| scheduled_at | timestamp with time zone | YES |  |
| sending_started_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| total_recipients | integer | YES | 0 |
| sent_count | integer | YES | 0 |
| delivered_count | integer | YES | 0 |
| failed_count | integer | YES | 0 |
| opened_count | integer | YES | 0 |
| clicked_count | integer | YES | 0 |
| created_by_id | uuid | YES |  |
| is_active | boolean | YES | true |
| metadata | json | YES |  |
| created_at | timestamp with time zone | YES | now() |
| updated_at | timestamp with time zone | YES | now() |
| campaign_sender_id | uuid | YES |  |
| campaign_smtp_config_id | uuid | YES |  |
| channel | campaignchannel |  | 'email'::campaignchannel |
| connector_config_id | uuid | YES |  |
| whatsapp_template_name | varchar(200) | YES |  |
| whatsapp_template_language | varchar(10) | YES |  |
| whatsapp_template_components | json | YES |  |

**Foreign keys:**
- `campaign_sender_id` -> `public.crm_campaign_senders.id`
- `campaign_smtp_config_id` -> `public.crm_campaign_smtp_configs.id`
- `connector_config_id` -> `public.connector_configs.id`
- `created_by_id` -> `public.people.id`

**Indexes:**
- `ix_crm_campaigns_is_active`: (is_active)
- `ix_crm_campaigns_scheduled_at`: (scheduled_at)
- `ix_crm_campaigns_status`: (status)

### `public.crm_conversation_assignments`
PK: `id` | ~1,932 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| conversation_id | uuid |  |  |
| team_id | uuid | YES |  |
| agent_id | uuid | YES |  |
| assigned_by_id | uuid | YES |  |
| assigned_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `agent_id` -> `public.crm_agents.id`
- `assigned_by_id` -> `public.people.id`
- `conversation_id` -> `public.crm_conversations.id`
- `team_id` -> `public.crm_teams.id`

**Indexes:**
- `idx_crm_assignments_active`: (conversation_id)
- `idx_crm_assignments_agent`: (agent_id, conversation_id)
- `uq_crm_conversation_assignments`: (conversation_id, team_id, agent_id) (unique)

### `public.crm_conversation_tags`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| conversation_id | uuid |  |  |
| tag | varchar(80) |  |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `conversation_id` -> `public.crm_conversations.id`

**Indexes:**
- `uq_crm_conversation_tags_conversation_tag`: (conversation_id, tag) (unique)

### `public.crm_conversations`
PK: `id` | ~13,454 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| ticket_id | uuid | YES |  |
| status | conversationstatus |  |  |
| subject | varchar(200) | YES |  |
| last_message_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `ticket_id` -> `public.tickets.id`

**Indexes:**
- `idx_crm_conversations_last_msg`: (last_message_at, updated_at)
- `idx_crm_conversations_person_status`: (person_id, status, is_active, updated_at)

### `public.crm_leads`
PK: `id` | ~2,517 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| pipeline_id | uuid | YES |  |
| stage_id | uuid | YES |  |
| owner_agent_id | uuid | YES |  |
| title | varchar(200) | YES |  |
| status | leadstatus |  |  |
| estimated_value | numeric(12,2) | YES |  |
| currency | varchar(3) | YES |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| probability | integer | YES |  |
| expected_close_date | date | YES |  |
| lost_reason | varchar(200) | YES |  |
| region | varchar(80) | YES |  |
| address | text | YES |  |
| closed_at | timestamp with time zone | YES |  |

**Foreign keys:**
- `owner_agent_id` -> `public.crm_agents.id`
- `person_id` -> `public.people.id`
- `pipeline_id` -> `public.crm_pipelines.id`
- `stage_id` -> `public.crm_pipeline_stages.id`

**Indexes:**
- `ix_crm_leads_closed_at`: (closed_at)

### `public.crm_message_attachments`
PK: `id` | ~8,878 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| message_id | uuid |  |  |
| file_name | varchar(255) | YES |  |
| mime_type | varchar(120) | YES |  |
| file_size | integer | YES |  |
| external_url | varchar(500) | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `message_id` -> `public.crm_messages.id`

### `public.crm_message_templates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| channel_type | channeltype |  |  |
| subject | varchar(200) | YES |  |
| body | text |  |  |
| is_active | boolean |  | true |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `ix_crm_message_templates_channel_active`: (channel_type, is_active)

### `public.crm_messages`
PK: `id` | ~127,631 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| conversation_id | uuid |  |  |
| person_channel_id | uuid | YES |  |
| channel_target_id | uuid | YES |  |
| channel_type | channeltype |  |  |
| direction | messagedirection |  |  |
| status | messagestatus |  |  |
| subject | varchar(200) | YES |  |
| body | text | YES |  |
| external_id | varchar(120) | YES |  |
| external_ref | varchar(255) | YES |  |
| author_id | uuid | YES |  |
| sent_at | timestamp with time zone | YES |  |
| received_at | timestamp with time zone | YES |  |
| read_at | timestamp with time zone | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| reply_to_message_id | uuid | YES |  |

**Foreign keys:**
- `author_id` -> `public.people.id`
- `channel_target_id` -> `public.integration_targets.id`
- `conversation_id` -> `public.crm_conversations.id`
- `person_channel_id` -> `public.person_channels.id`
- `reply_to_message_id` -> `public.crm_messages.id`

**Indexes:**
- `idx_crm_messages_channel_target`: (channel_type, channel_target_id, conversation_id)
- `idx_crm_messages_conv_last_ts`: (conversation_id)
- `idx_crm_messages_unread`: (conversation_id)
- `ix_crm_messages_reply_to_message_id`: (reply_to_message_id)
- `uq_crm_messages_external`: (channel_type, external_id) (unique)
- `uq_crm_messages_inbound_external`: (channel_type, external_id) (unique)

### `public.crm_outbox`
PK: `id` | ~3,188 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| conversation_id | uuid |  |  |
| message_id | uuid | YES |  |
| channel_type | channeltype |  |  |
| status | varchar(32) |  | 'queued'::character varying |
| attempts | integer |  | 0 |
| next_attempt_at | timestamp with time zone | YES |  |
| last_attempt_at | timestamp with time zone | YES |  |
| last_error | text | YES |  |
| payload | json | YES |  |
| author_id | uuid | YES |  |
| idempotency_key | varchar(128) | YES |  |
| priority | integer |  | 0 |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `conversation_id` -> `public.crm_conversations.id`
- `message_id` -> `public.crm_messages.id`

**Indexes:**
- `crm_outbox_idempotency_key_key`: (idempotency_key) (unique)
- `ix_crm_outbox_status_next_attempt`: (status, next_attempt_at)

### `public.crm_pipeline_stages`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| pipeline_id | uuid |  |  |
| name | varchar(160) |  |  |
| order_index | integer |  |  |
| is_active | boolean |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| default_probability | integer |  | 50 |

**Foreign keys:**
- `pipeline_id` -> `public.crm_pipelines.id`

### `public.crm_pipelines`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| is_active | boolean |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.crm_quote_line_items`
PK: `id` | ~2,064 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| quote_id | uuid |  |  |
| inventory_item_id | uuid | YES |  |
| description | varchar(255) |  |  |
| quantity | numeric(12,3) |  |  |
| unit_price | numeric(12,2) |  |  |
| amount | numeric(12,2) |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `inventory_item_id` -> `public.inventory_items.id`
- `quote_id` -> `public.crm_quotes.id`

### `public.crm_quotes`
PK: `id` | ~1,166 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| lead_id | uuid | YES |  |
| status | quotestatus |  |  |
| currency | varchar(3) |  |  |
| subtotal | numeric(12,2) |  |  |
| tax_total | numeric(12,2) |  |  |
| total | numeric(12,2) |  |  |
| expires_at | timestamp with time zone | YES |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `lead_id` -> `public.crm_leads.id`
- `person_id` -> `public.people.id`

### `public.crm_routing_rules`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| team_id | uuid |  |  |
| channel_type | channeltype |  |  |
| rule_config | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `team_id` -> `public.crm_teams.id`

### `public.crm_social_comment_replies`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| comment_id | uuid |  |  |
| platform | socialcommentplatform |  |  |
| external_id | varchar(200) | YES |  |
| message | text |  |  |
| created_time | timestamp with time zone | YES |  |
| raw_payload | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_crm_social_comment_replies_platform_external`: (platform, external_id) (unique)

### `public.crm_social_comments`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| platform | socialcommentplatform |  |  |
| external_id | varchar(200) |  |  |
| external_post_id | varchar(200) | YES |  |
| source_account_id | varchar(200) | YES |  |
| author_id | varchar(200) | YES |  |
| author_name | varchar(200) | YES |  |
| message | text | YES |  |
| created_time | timestamp with time zone | YES |  |
| permalink_url | varchar(500) | YES |  |
| raw_payload | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_crm_social_comments_platform_external`: (platform, external_id) (unique)

### `public.crm_team_channels`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| team_id | uuid |  |  |
| channel_type | channeltype |  |  |
| channel_target_id | uuid | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `channel_target_id` -> `public.integration_targets.id`
- `team_id` -> `public.crm_teams.id`

**Indexes:**
- `uq_crm_team_channels_default`: (team_id, channel_type) (unique)
- `uq_crm_team_channels_team_type_target`: (team_id, channel_type, channel_target_id) (unique)

### `public.crm_teams`
PK: `id` | ~5 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| is_active | boolean |  |  |
| notes | varchar(255) | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| service_team_id | uuid | YES |  |

**Foreign keys:**
- `service_team_id` -> `public.service_teams.id`

**Indexes:**
- `ix_crm_teams_service_team_id`: (service_team_id)

### `public.customer_notification_events`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| entity_type | varchar(40) |  |  |
| entity_id | uuid |  |  |
| channel | varchar(40) |  |  |
| recipient | varchar(255) |  |  |
| message | text |  |  |
| status | customernotificationstatus |  |  |
| sent_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |

### `public.dispatch_rules`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| priority | integer |  |  |
| work_type | varchar(40) | YES |  |
| work_priority | varchar(40) | YES |  |
| region | varchar(120) | YES |  |
| skill_ids | json | YES |  |
| auto_assign | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.document_sequences`
PK: `id` | ~4 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| key | varchar(80) |  |  |
| next_value | integer |  | 1 |
| created_at | timestamp with time zone |  | timezone('utc'::text, now()) |
| updated_at | timestamp with time zone |  | timezone('utc'::text, now()) |

**Indexes:**
- `uq_document_sequences_key`: (key) (unique)

### `public.domain_settings`
PK: `id` | ~217 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| domain | settingdomain |  |  |
| key | varchar(120) |  |  |
| value_type | settingvaluetype |  |  |
| value_text | text | YES |  |
| value_json | json | YES |  |
| is_secret | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `ix_domain_settings_domain_is_active`: (domain, is_active)
- `uq_domain_settings_domain_key`: (domain, key) (unique)

### `public.eta_updates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| eta_at | timestamp with time zone |  |  |
| note | text | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `work_order_id` -> `public.work_orders.id`

### `public.event_store`
PK: `id` | ~9,483 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| event_id | uuid |  |  |
| event_type | varchar(100) |  |  |
| payload | jsonb |  |  |
| status | eventstatus |  |  |
| retry_count | integer |  |  |
| error | text | YES |  |
| processed_at | timestamp with time zone | YES |  |
| actor | varchar(255) | YES |  |
| subscriber_id | uuid | YES |  |
| account_id | uuid | YES |  |
| subscription_id | uuid | YES |  |
| invoice_id | uuid | YES |  |
| ticket_id | uuid | YES |  |
| failed_handlers | jsonb | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| is_active | boolean |  |  |
| project_id | uuid | YES |  |
| work_order_id | uuid | YES |  |

**Indexes:**
- `ix_event_store_account_id`: (account_id)
- `ix_event_store_event_id`: (event_id) (unique)
- `ix_event_store_event_type`: (event_type)
- `ix_event_store_project_id`: (project_id)
- `ix_event_store_status`: (status)
- `ix_event_store_status_created_at`: (status, created_at)
- `ix_event_store_status_retry_count`: (status, retry_count)
- `ix_event_store_subscriber_id`: (subscriber_id)
- `ix_event_store_ticket_id`: (ticket_id)
- `ix_event_store_work_order_id`: (work_order_id)

### `public.expense_lines`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid | YES |  |
| project_id | uuid | YES |  |
| amount | numeric(12,2) |  |  |
| currency | varchar(3) |  |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `project_id` -> `public.projects.id`
- `work_order_id` -> `public.work_orders.id`

### `public.external_references`
PK: `id` | ~72,846 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| connector_config_id | uuid | YES |  |
| entity_type | externalentitytype |  |  |
| entity_id | uuid |  |  |
| external_id | varchar(200) |  |  |
| external_url | varchar(500) | YES |  |
| metadata | json | YES |  |
| last_synced_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`

**Indexes:**
- `uq_external_refs_connector_entity`: (connector_config_id, entity_type, entity_id) (unique)
- `uq_external_refs_connector_external`: (connector_config_id, entity_type, external_id) (unique)

### `public.fdh_cabinets`
PK: `id` | ~230 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| code | varchar(80) | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| region_id | uuid | YES |  |

**Indexes:**
- `idx_fdh_cabinets_geom`: (geom)
- `uq_fdh_cabinets_code`: (code) (unique)

### `public.fiber_access_points`
PK: `id` | ~501 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| code | varchar(60) | YES |  |
| name | varchar(160) |  |  |
| access_point_type | varchar(60) | YES |  |
| placement | varchar(60) | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| street | varchar(200) | YES |  |
| city | varchar(100) | YES |  |
| county | varchar(100) | YES |  |
| state | varchar(60) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `fiber_access_points_code_key`: (code) (unique)
- `idx_fiber_access_points_geom`: (geom)

### `public.fiber_asset_merge_logs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| asset_type | varchar(80) |  |  |
| source_asset_id | uuid |  |  |
| target_asset_id | uuid |  |  |
| merged_by_id | uuid | YES |  |
| source_snapshot | json | YES |  |
| field_choices | json | YES |  |
| children_migrated | json | YES |  |
| merged_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `merged_by_id` -> `public.people.id`

**Indexes:**
- `ix_fiber_asset_merge_logs_asset_type`: (asset_type)
- `ix_fiber_asset_merge_logs_source`: (source_asset_id)
- `ix_fiber_asset_merge_logs_target`: (target_asset_id)

### `public.fiber_change_requests`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| asset_type | varchar(80) |  |  |
| asset_id | uuid | YES |  |
| operation | fiberchangerequestoperation |  |  |
| payload | json |  |  |
| status | fiberchangerequeststatus |  |  |
| requested_by_person_id | uuid | YES |  |
| requested_by_vendor_id | uuid | YES |  |
| reviewed_by_person_id | uuid | YES |  |
| review_notes | text | YES |  |
| reviewed_at | timestamp with time zone | YES |  |
| applied_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `requested_by_person_id` -> `public.people.id`
- `requested_by_vendor_id` -> `public.vendors.id`
- `reviewed_by_person_id` -> `public.people.id`

**Indexes:**
- `ix_fiber_change_requests_asset_type`: (asset_type)
- `ix_fiber_change_requests_status`: (status)

### `public.fiber_qa_remediation_logs`
PK: `id` | ~75 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | integer |  | nextval('fiber_qa_remediation_logs_id... |
| asset_type | varchar(50) | YES |  |
| asset_id | uuid | YES |  |
| issue_type | varchar(100) | YES |  |
| old_value | text | YES |  |
| new_value | text | YES |  |
| action_taken | varchar(50) | YES |  |
| performed_by | varchar(100) |  | 'codex_strategy'::character varying |
| created_at | timestamp without time zone |  | CURRENT_TIMESTAMP |
| status | varchar(30) |  | 'pending'::character varying |
| review_notes | text | YES |  |
| target_asset_id | uuid | YES |  |
| approved_by | uuid | YES |  |
| approved_at | timestamp with time zone | YES |  |

**Indexes:**
- `idx_qa_logs_asset`: (asset_type, asset_id)
- `ix_fiber_qa_remediation_logs_issue_type`: (issue_type)
- `ix_fiber_qa_remediation_logs_status`: (status)
- `ix_fiber_qa_remediation_logs_target_asset_id`: (target_asset_id)

### `public.fiber_segments`
PK: `id` | ~2,859 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| segment_type | fibersegmenttype |  |  |
| cable_type | fibercabletype | YES |  |
| fiber_count | integer | YES |  |
| from_point_id | uuid | YES |  |
| to_point_id | uuid | YES |  |
| fiber_strand_id | uuid | YES |  |
| length_m | double precision | YES |  |
| route_geom (PostGIS: LINESTRING, SRID:4326) | geometry | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `fiber_strand_id` -> `public.fiber_strands.id`
- `from_point_id` -> `public.fiber_termination_points.id`
- `to_point_id` -> `public.fiber_termination_points.id`

**Indexes:**
- `idx_fiber_segments_route_geom`: (route_geom)
- `uq_fiber_segments_name`: (name) (unique)

### `public.fiber_splice_closures`
PK: `id` | ~1,254 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `idx_fiber_splice_closures_geom`: (geom)

### `public.fiber_splice_trays`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| closure_id | uuid |  |  |
| tray_number | integer |  |  |
| name | varchar(160) | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `closure_id` -> `public.fiber_splice_closures.id`

**Indexes:**
- `uq_fiber_splice_trays_closure_tray`: (closure_id, tray_number) (unique)

### `public.fiber_splices`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| closure_id | uuid |  |  |
| from_strand_id | uuid |  |  |
| to_strand_id | uuid |  |  |
| tray_id | uuid | YES |  |
| splice_type | varchar(80) | YES |  |
| loss_db | double precision | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `closure_id` -> `public.fiber_splice_closures.id`
- `from_strand_id` -> `public.fiber_strands.id`
- `to_strand_id` -> `public.fiber_strands.id`
- `tray_id` -> `public.fiber_splice_trays.id`

**Indexes:**
- `uq_fiber_splices_from_to`: (from_strand_id, to_strand_id) (unique)

### `public.fiber_strands`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| cable_name | varchar(160) |  |  |
| strand_number | integer |  |  |
| label | varchar(160) | YES |  |
| status | fiberstrandstatus |  |  |
| upstream_type | fiberendpointtype | YES |  |
| upstream_id | uuid | YES |  |
| downstream_type | fiberendpointtype | YES |  |
| downstream_id | uuid | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_fiber_strands_cable_strand`: (cable_name, strand_number) (unique)

### `public.fiber_termination_points`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) | YES |  |
| endpoint_type | odnendpointtype |  |  |
| ref_id | uuid | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.geo_areas`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| area_type | geoareatype |  |  |
| geometry_geojson | json | YES |  |
| geom (PostGIS: GEOMETRY, SRID:4326) | geometry | YES |  |
| min_latitude | double precision | YES |  |
| min_longitude | double precision | YES |  |
| max_latitude | double precision | YES |  |
| max_longitude | double precision | YES |  |
| metadata | json | YES |  |
| tags | json | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `idx_geo_areas_geom`: (geom)

### `public.geo_layers`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| layer_key | varchar(80) |  |  |
| layer_type | geolayertype |  |  |
| source_type | geolayersource |  |  |
| style | json | YES |  |
| filters | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_geo_layers_layer_key`: (layer_key) (unique)

### `public.geo_locations`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| location_type | geolocationtype |  |  |
| latitude | double precision |  |  |
| longitude | double precision |  |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| address_id | uuid | YES |  |
| pop_site_id | uuid | YES |  |
| metadata | json | YES |  |
| tags | json | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| olt_device_id | uuid | YES |  |
| fdh_cabinet_id | uuid | YES |  |

**Indexes:**
- `idx_geo_locations_geom`: (geom)

### `public.installation_project_notes`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| author_person_id | uuid | YES |  |
| body | text |  |  |
| is_internal | boolean |  |  |
| attachments | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `author_person_id` -> `public.people.id`
- `project_id` -> `public.installation_projects.id`

### `public.installation_projects`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| buildout_project_id | uuid | YES |  |
| subscriber_id | uuid | YES |  |
| address_id | uuid | YES |  |
| assigned_vendor_id | uuid | YES |  |
| assignment_type | vendorassignmenttype | YES |  |
| status | installationprojectstatus |  |  |
| bidding_open_at | timestamp with time zone | YES |  |
| bidding_close_at | timestamp with time zone | YES |  |
| approved_quote_id | uuid | YES |  |
| created_by_person_id | uuid | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `subscriber_id` -> `public.subscribers.id`
- `approved_quote_id` -> `public.project_quotes.id`
- `assigned_vendor_id` -> `public.vendors.id`
- `buildout_project_id` -> `public.buildout_projects.id`
- `created_by_person_id` -> `public.people.id`
- `project_id` -> `public.projects.id`

**Indexes:**
- `ix_installation_projects_subscriber_id`: (subscriber_id)
- `uq_installation_projects_project`: (project_id) (unique)

### `public.integration_jobs`
PK: `id` | ~3 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| target_id | uuid |  |  |
| name | varchar(160) |  |  |
| job_type | integrationjobtype |  |  |
| schedule_type | integrationscheduletype |  |  |
| interval_minutes | integer | YES |  |
| interval_seconds | integer | YES |  |
| is_active | boolean |  |  |
| last_run_at | timestamp with time zone | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `target_id` -> `public.integration_targets.id`

### `public.integration_runs`
PK: `id` | ~69,286 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| job_id | uuid |  |  |
| status | integrationrunstatus |  |  |
| started_at | timestamp with time zone |  |  |
| finished_at | timestamp with time zone | YES |  |
| error | text | YES |  |
| metrics | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `job_id` -> `public.integration_jobs.id`

### `public.integration_targets`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| target_type | integrationtargettype |  |  |
| connector_config_id | uuid | YES |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`

### `public.inventory_items`
PK: `id` | ~699 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| sku | varchar(80) | YES |  |
| name | varchar(160) |  |  |
| description | text | YES |  |
| unit | varchar(40) | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.inventory_locations`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| code | varchar(80) | YES |  |
| address_id | uuid | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.inventory_reservations`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| item_id | uuid |  |  |
| location_id | uuid |  |  |
| work_order_id | uuid | YES |  |
| quantity | integer |  |  |
| status | reservationstatus |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `item_id` -> `public.inventory_items.id`
- `location_id` -> `public.inventory_locations.id`
- `work_order_id` -> `public.work_orders.id`

### `public.inventory_stock`
PK: `id` | ~697 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| item_id | uuid |  |  |
| location_id | uuid |  |  |
| quantity_on_hand | integer |  |  |
| reserved_quantity | integer |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `item_id` -> `public.inventory_items.id`
- `location_id` -> `public.inventory_locations.id`

**Indexes:**
- `uq_inventory_stock_item_location`: (item_id, location_id) (unique)

### `public.kpi_aggregates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| key | varchar(120) |  |  |
| period_start | timestamp with time zone |  |  |
| period_end | timestamp with time zone |  |  |
| value | numeric(14,4) |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

### `public.kpi_configs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| key | varchar(120) |  |  |
| name | varchar(160) |  |  |
| description | text | YES |  |
| parameters | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.legal_documents`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| document_type | legaldocumenttype |  |  |
| title | varchar(200) |  |  |
| slug | varchar(100) |  |  |
| version | varchar(20) |  |  |
| summary | text | YES |  |
| content | text | YES |  |
| file_path | varchar(500) | YES |  |
| file_name | varchar(255) | YES |  |
| file_size | integer | YES |  |
| mime_type | varchar(100) | YES |  |
| is_current | boolean |  |  |
| is_published | boolean |  |  |
| published_at | timestamp with time zone | YES |  |
| effective_date | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `legal_documents_slug_key`: (slug) (unique)

### `public.material_request_items`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| material_request_id | uuid |  |  |
| item_id | uuid |  |  |
| quantity | integer |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `item_id` -> `public.inventory_items.id`
- `material_request_id` -> `public.material_requests.id`

### `public.material_requests`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| ticket_id | uuid | YES |  |
| project_id | uuid | YES |  |
| work_order_id | uuid | YES |  |
| requested_by_person_id | uuid |  |  |
| approved_by_person_id | uuid | YES |  |
| status | materialrequeststatus |  | 'draft'::materialrequeststatus |
| priority | materialrequestpriority |  | 'medium'::materialrequestpriority |
| notes | text | YES |  |
| erp_material_request_id | varchar(120) | YES |  |
| number | varchar(40) | YES |  |
| is_active | boolean |  | true |
| metadata | json | YES |  |
| submitted_at | timestamp with time zone | YES |  |
| approved_at | timestamp with time zone | YES |  |
| rejected_at | timestamp with time zone | YES |  |
| fulfilled_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `approved_by_person_id` -> `public.people.id`
- `project_id` -> `public.projects.id`
- `requested_by_person_id` -> `public.people.id`
- `ticket_id` -> `public.tickets.id`
- `work_order_id` -> `public.work_orders.id`

**Indexes:**
- `ix_material_requests_project_id`: (project_id)
- `ix_material_requests_ticket_id`: (ticket_id)

### `public.mfa_methods`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| method_type | mfamethodtype |  |  |
| label | varchar(120) | YES |  |
| secret | varchar(255) | YES |  |
| phone | varchar(40) | YES |  |
| email | varchar(255) | YES |  |
| is_primary | boolean |  |  |
| enabled | boolean |  |  |
| is_active | boolean |  |  |
| verified_at | timestamp with time zone | YES |  |
| last_used_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_mfa_methods_primary_per_person`: (person_id) (unique)

### `public.nextcloud_talk_accounts`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| base_url | varchar(500) |  |  |
| username | varchar(150) |  |  |
| app_password_enc | varchar(2048) |  |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_nextcloud_talk_accounts_person_id`: (person_id) (unique)
- `uq_nextcloud_talk_accounts_person_id`: (person_id) (unique)

### `public.nextcloud_talk_notification_rooms`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| base_url | varchar(500) |  |  |
| notifier_username | varchar(150) |  |  |
| invite_target | varchar(255) |  |  |
| room_token | varchar(255) |  |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_nextcloud_talk_notification_rooms_person_id`: (person_id)
- `uq_nextcloud_talk_notification_rooms_person_instance`: (person_id, base_url, notifier_username) (unique)

### `public.notification_deliveries`
PK: `id` | ~2,730 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| notification_id | uuid |  |  |
| provider | varchar(120) | YES |  |
| provider_message_id | varchar(200) | YES |  |
| status | deliverystatus |  |  |
| response_code | varchar(60) | YES |  |
| response_body | text | YES |  |
| occurred_at | timestamp with time zone |  |  |
| is_active | boolean |  |  |

**Foreign keys:**
- `notification_id` -> `public.notifications.id`

### `public.notification_templates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(120) |  |  |
| code | varchar(120) |  |  |
| channel | notificationchannel |  |  |
| subject | varchar(200) | YES |  |
| body | text |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `notification_templates_code_key`: (code) (unique)

### `public.notifications`
PK: `id` | ~3,318 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| template_id | uuid | YES |  |
| connector_config_id | uuid | YES |  |
| channel | notificationchannel |  |  |
| recipient | varchar(255) |  |  |
| subject | varchar(200) | YES |  |
| body | text | YES |  |
| status | notificationstatus |  |  |
| send_at | timestamp with time zone | YES |  |
| sent_at | timestamp with time zone | YES |  |
| last_error | text | YES |  |
| retry_count | integer |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| from_name | varchar(160) | YES |  |
| from_email | varchar(255) | YES |  |
| reply_to | varchar(255) | YES |  |
| smtp_config_id | uuid | YES |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`
- `smtp_config_id` -> `public.crm_campaign_smtp_configs.id`
- `template_id` -> `public.notification_templates.id`

### `public.oauth_tokens`
PK: `id` | ~4 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| connector_config_id | uuid |  |  |
| provider | varchar(64) |  |  |
| account_type | varchar(64) |  |  |
| external_account_id | varchar(120) |  |  |
| external_account_name | varchar(255) | YES |  |
| access_token | text | YES |  |
| refresh_token | text | YES |  |
| token_type | varchar(64) | YES |  |
| token_expires_at | timestamp with time zone | YES |  |
| scopes | json | YES |  |
| last_refreshed_at | timestamp with time zone | YES |  |
| refresh_error | text | YES |  |
| is_active | boolean |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`

**Indexes:**
- `ix_oauth_tokens_connector_config_id`: (connector_config_id)
- `ix_oauth_tokens_provider`: (provider)
- `ix_oauth_tokens_token_expires_at`: (token_expires_at)
- `uq_oauth_tokens_connector_provider_account`: (connector_config_id, provider, external_account_id) (unique)

### `public.olt_card_ports`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| card_id | uuid |  |  |
| port_number | integer |  |  |
| name | varchar(120) | YES |  |
| port_type | oltporttype |  |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `card_id` -> `public.olt_cards.id`

**Indexes:**
- `uq_olt_card_ports_card_port_number`: (card_id, port_number) (unique)

### `public.olt_cards`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| shelf_id | uuid |  |  |
| slot_number | integer |  |  |
| card_type | varchar(120) | YES |  |
| model | varchar(120) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `shelf_id` -> `public.olt_shelves.id`

**Indexes:**
- `uq_olt_cards_shelf_slot_number`: (shelf_id, slot_number) (unique)

### `public.olt_devices`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| hostname | varchar(160) | YES |  |
| mgmt_ip | varchar(64) | YES |  |
| vendor | varchar(120) | YES |  |
| model | varchar(120) | YES |  |
| serial_number | varchar(120) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| site_role | varchar(32) |  | 'olt'::character varying |

**Indexes:**
- `uq_olt_devices_hostname`: (hostname) (unique)
- `uq_olt_devices_mgmt_ip`: (mgmt_ip) (unique)

### `public.olt_power_units`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| olt_id | uuid |  |  |
| slot | varchar(40) |  |  |
| status | varchar(40) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `olt_id` -> `public.olt_devices.id`

**Indexes:**
- `uq_olt_power_units_olt_slot`: (olt_id, slot) (unique)

### `public.olt_sfp_modules`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| olt_card_port_id | uuid |  |  |
| vendor | varchar(120) | YES |  |
| model | varchar(120) | YES |  |
| serial_number | varchar(120) | YES |  |
| wavelength_nm | integer | YES |  |
| rx_power_dbm | double precision | YES |  |
| tx_power_dbm | double precision | YES |  |
| installed_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `olt_card_port_id` -> `public.olt_card_ports.id`

**Indexes:**
- `uq_olt_sfp_modules_port_serial`: (olt_card_port_id, serial_number) (unique)

### `public.olt_shelves`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| olt_id | uuid |  |  |
| shelf_number | integer |  |  |
| label | varchar(120) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `olt_id` -> `public.olt_devices.id`

**Indexes:**
- `uq_olt_shelves_olt_shelf_number`: (olt_id, shelf_number) (unique)

### `public.on_call_rotation_members`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| rotation_id | uuid |  |  |
| name | varchar(120) |  |  |
| contact | varchar(255) |  |  |
| priority | integer |  |  |
| last_used_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `rotation_id` -> `public.on_call_rotations.id`

### `public.on_call_rotations`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| timezone | varchar(60) |  |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_on_call_rotations_name`: (name) (unique)

### `public.ont_assignments`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| ont_unit_id | uuid |  |  |
| pon_port_id | uuid |  |  |
| person_id | uuid | YES |  |
| assigned_at | timestamp with time zone | YES |  |
| active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `ont_unit_id` -> `public.ont_units.id`
- `person_id` -> `public.people.id`
- `pon_port_id` -> `public.pon_ports.id`

**Indexes:**
- `ix_ont_assignments_active_unit`: (ont_unit_id) (unique)

### `public.ont_units`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| serial_number | varchar(120) |  |  |
| model | varchar(120) | YES |  |
| vendor | varchar(120) | YES |  |
| firmware_version | varchar(120) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_ont_units_serial_number`: (serial_number) (unique)

### `public.organization_memberships`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| organization_id | uuid |  |  |
| person_id | uuid |  |  |
| role | organizationmembershiprole |  | 'member'::organizationmembershiprole |
| is_active | boolean |  | true |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `organization_id` -> `public.organizations.id`
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_organization_memberships_org`: (organization_id)
- `ix_organization_memberships_person`: (person_id)
- `uq_organization_memberships_org_person`: (organization_id, person_id) (unique)

### `public.organizations`
PK: `id` | ~5,874 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| legal_name | varchar(200) | YES |  |
| tax_id | varchar(80) | YES |  |
| domain | varchar(120) | YES |  |
| website | varchar(255) | YES |  |
| address_line1 | varchar(120) | YES |  |
| address_line2 | varchar(120) | YES |  |
| city | varchar(80) | YES |  |
| region | varchar(80) | YES |  |
| postal_code | varchar(20) | YES |  |
| country_code | varchar(2) | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| phone | varchar(40) | YES |  |
| email | varchar(255) | YES |  |
| account_type | accounttype |  | 'prospect'::accounttype |
| account_status | accountstatus |  | 'active'::accountstatus |
| parent_id | uuid | YES |  |
| primary_contact_id | uuid | YES |  |
| owner_id | uuid | YES |  |
| industry | varchar(100) | YES |  |
| employee_count | varchar(40) | YES |  |
| annual_revenue | varchar(60) | YES |  |
| source | varchar(100) | YES |  |
| erp_id | varchar(100) | YES |  |
| tags | json | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  | true |
| erpnext_id | varchar(100) | YES |  |

**Foreign keys:**
- `owner_id` -> `public.people.id`
- `parent_id` -> `public.organizations.id`
- `primary_contact_id` -> `public.people.id`

**Indexes:**
- `ix_organizations_account_type`: (account_type)
- `ix_organizations_erp`: (erp_id) (unique)
- `ix_organizations_erpnext_id`: (erpnext_id) (unique)
- `ix_organizations_owner`: (owner_id)
- `ix_organizations_parent`: (parent_id)
- `ix_organizations_status`: (account_status)

### `public.people`
PK: `id` | ~13,430 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| first_name | varchar(80) |  |  |
| last_name | varchar(80) |  |  |
| display_name | varchar(120) | YES |  |
| avatar_url | varchar(512) | YES |  |
| bio | text | YES |  |
| email | varchar(255) |  |  |
| email_verified | boolean |  |  |
| phone | varchar(40) | YES |  |
| date_of_birth | date | YES |  |
| gender | gender |  |  |
| preferred_contact_method | contactmethod | YES |  |
| locale | varchar(16) | YES |  |
| timezone | varchar(64) | YES |  |
| address_line1 | varchar(120) | YES |  |
| address_line2 | varchar(120) | YES |  |
| city | varchar(80) | YES |  |
| region | varchar(80) | YES |  |
| postal_code | varchar(20) | YES |  |
| country_code | varchar(2) | YES |  |
| party_status | partystatus |  |  |
| organization_id | uuid | YES |  |
| status | personstatus |  |  |
| is_active | boolean |  |  |
| marketing_opt_in | boolean |  |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| job_title | varchar(120) | YES |  |
| erp_customer_id | varchar(100) | YES |  |
| erpnext_id | varchar(100) | YES |  |
| erp_person_id | varchar(100) | YES |  |

**Foreign keys:**
- `organization_id` -> `public.organizations.id`

**Indexes:**
- `ix_people_erp_customer_id`: (erp_customer_id)
- `ix_people_erp_person_id`: (erp_person_id)
- `ix_people_erpnext_id`: (erpnext_id) (unique)
- `people_email_key`: (email) (unique)
- `uq_people_erp_customer_id`: (erp_customer_id) (unique)

### `public.permissions`
PK: `id` | ~151 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| key | varchar(120) |  |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_permissions_key`: (key) (unique)

### `public.person_channels`
PK: `id` | ~12,138 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| channel_type | channeltype |  |  |
| address | varchar(255) |  |  |
| label | varchar(60) | YES |  |
| is_primary | boolean |  |  |
| is_verified | boolean |  |  |
| verified_at | timestamp with time zone | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `uq_person_channels_person_type_address`: (person_id, channel_type, address) (unique)

### `public.person_merge_logs`
PK: `id` | ~423 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| source_person_id | uuid |  |  |
| target_person_id | uuid |  |  |
| merged_by_id | uuid | YES |  |
| source_snapshot | json | YES |  |
| merged_at | timestamp with time zone |  |  |

**Foreign keys:**
- `merged_by_id` -> `public.people.id`
- `target_person_id` -> `public.people.id`

### `public.person_permissions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| permission_id | uuid |  |  |
| granted_at | timestamp with time zone |  |  |
| granted_by_person_id | uuid | YES |  |

**Foreign keys:**
- `granted_by_person_id` -> `public.people.id`
- `permission_id` -> `public.permissions.id`
- `person_id` -> `public.people.id`

**Indexes:**
- `uq_person_permissions_person_permission`: (person_id, permission_id) (unique)

### `public.person_roles`
PK: `id` | ~126 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| role_id | uuid |  |  |
| assigned_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `role_id` -> `public.roles.id`

**Indexes:**
- `uq_person_roles_person_role`: (person_id, role_id) (unique)

### `public.person_status_logs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| from_status | partystatus | YES |  |
| to_status | partystatus |  |  |
| changed_by_id | uuid | YES |  |
| reason | varchar(255) | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `changed_by_id` -> `public.people.id`
- `person_id` -> `public.people.id`

### `public.pon_port_splitter_links`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| pon_port_id | uuid |  |  |
| splitter_port_id | uuid |  |  |
| active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `pon_port_id` -> `public.pon_ports.id`
- `splitter_port_id` -> `public.splitter_ports.id`

**Indexes:**
- `uq_pon_port_splitter_links_pon_port`: (pon_port_id) (unique)

### `public.pon_ports`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| olt_id | uuid |  |  |
| olt_card_port_id | uuid | YES |  |
| name | varchar(120) |  |  |
| port_number | integer | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `olt_card_port_id` -> `public.olt_card_ports.id`
- `olt_id` -> `public.olt_devices.id`

**Indexes:**
- `uq_pon_ports_olt_name`: (olt_id, name) (unique)

### `public.project_comments`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| author_person_id | uuid | YES |  |
| body | text |  |  |
| attachments | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `author_person_id` -> `public.people.id`
- `project_id` -> `public.projects.id`

### `public.project_quotes`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| vendor_id | uuid |  |  |
| status | projectquotestatus |  |  |
| currency | varchar(3) |  |  |
| subtotal | numeric(12,2) |  |  |
| tax_total | numeric(12,2) |  |  |
| total | numeric(12,2) |  |  |
| valid_from | timestamp with time zone | YES |  |
| valid_until | timestamp with time zone | YES |  |
| submitted_at | timestamp with time zone | YES |  |
| reviewed_at | timestamp with time zone | YES |  |
| reviewed_by_person_id | uuid | YES |  |
| review_notes | text | YES |  |
| created_by_person_id | uuid | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `created_by_person_id` -> `public.people.id`
- `project_id` -> `public.installation_projects.id`
- `reviewed_by_person_id` -> `public.people.id`
- `vendor_id` -> `public.vendors.id`

### `public.project_task_assignees`
PK: `task_id, person_id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **task_id** (PK) | uuid |  |  |
| **person_id** (PK) | uuid |  |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `task_id` -> `public.project_tasks.id`

**Indexes:**
- `ix_project_task_assignees_person_id`: (person_id)

### `public.project_task_comments`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| task_id | uuid |  |  |
| author_person_id | uuid | YES |  |
| body | text |  |  |
| attachments | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `author_person_id` -> `public.people.id`
- `task_id` -> `public.project_tasks.id`

### `public.project_task_dependencies`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| task_id | uuid |  |  |
| depends_on_task_id | uuid |  |  |
| dependency_type | taskdependencytype |  | 'finish_to_start'::taskdependencytype |
| lag_days | integer |  | 0 |

**Foreign keys:**
- `depends_on_task_id` -> `public.project_tasks.id`
- `task_id` -> `public.project_tasks.id`

**Indexes:**
- `uq_project_task_dependencies`: (task_id, depends_on_task_id) (unique)

### `public.project_task_status_transitions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| from_status | varchar(40) |  |  |
| to_status | varchar(40) |  |  |
| requires_note | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.project_tasks`
PK: `id` | ~7,494 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| project_id | uuid |  |  |
| parent_task_id | uuid | YES |  |
| title | varchar(200) |  |  |
| description | text | YES |  |
| template_task_id | uuid | YES |  |
| status | project_taskstatus |  |  |
| priority | taskpriority |  |  |
| assigned_to_person_id | uuid | YES |  |
| created_by_person_id | uuid | YES |  |
| ticket_id | uuid | YES |  |
| work_order_id | uuid | YES |  |
| start_at | timestamp with time zone | YES |  |
| due_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| effort_hours | integer | YES |  |
| tags | json | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| number | varchar(40) | YES |  |
| erpnext_id | varchar(100) | YES |  |

**Foreign keys:**
- `assigned_to_person_id` -> `public.people.id`
- `created_by_person_id` -> `public.people.id`
- `parent_task_id` -> `public.project_tasks.id`
- `project_id` -> `public.projects.id`
- `template_task_id` -> `public.project_template_tasks.id`
- `ticket_id` -> `public.tickets.id`
- `work_order_id` -> `public.work_orders.id`

**Indexes:**
- `ix_project_tasks_erpnext_id`: (erpnext_id) (unique)
- `uq_project_tasks_number`: (number) (unique)

### `public.project_template_task_dependency`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| template_task_id | uuid |  |  |
| depends_on_template_task_id | uuid |  |  |
| dependency_type | taskdependencytype |  | 'finish_to_start'::taskdependencytype |
| lag_days | integer |  | 0 |

**Foreign keys:**
- `depends_on_template_task_id` -> `public.project_template_tasks.id`
- `template_task_id` -> `public.project_template_tasks.id`

**Indexes:**
- `uq_project_template_task_dependency`: (template_task_id, depends_on_template_task_id) (unique)

### `public.project_template_tasks`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| template_id | uuid |  |  |
| title | varchar(200) |  |  |
| description | text | YES |  |
| status | project_taskstatus | YES |  |
| priority | taskpriority | YES |  |
| sort_order | integer |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| effort_hours | integer | YES |  |

**Foreign keys:**
- `template_id` -> `public.project_templates.id`

### `public.project_templates`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| project_type | projecttype | YES |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_project_templates_project_type`: (project_type) (unique)

### `public.projects`
PK: `id` | ~977 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| code | varchar(80) | YES |  |
| description | text | YES |  |
| project_type | projecttype | YES |  |
| project_template_id | uuid | YES |  |
| status | projectstatus |  |  |
| priority | projectpriority |  |  |
| subscriber_id | uuid | YES |  |
| created_by_person_id | uuid | YES |  |
| owner_person_id | uuid | YES |  |
| manager_person_id | uuid | YES |  |
| start_at | timestamp with time zone | YES |  |
| due_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| tags | json | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| region | varchar(80) | YES |  |
| lead_id | uuid | YES |  |
| customer_address | text | YES |  |
| project_manager_person_id | uuid | YES |  |
| assistant_manager_person_id | uuid | YES |  |
| number | varchar(40) | YES |  |
| service_team_id | uuid | YES |  |
| erpnext_id | varchar(100) | YES |  |

**Foreign keys:**
- `assistant_manager_person_id` -> `public.people.id`
- `lead_id` -> `public.crm_leads.id`
- `project_manager_person_id` -> `public.people.id`
- `service_team_id` -> `public.service_teams.id`
- `created_by_person_id` -> `public.people.id`
- `manager_person_id` -> `public.people.id`
- `owner_person_id` -> `public.people.id`
- `project_template_id` -> `public.project_templates.id`
- `subscriber_id` -> `public.subscribers.id`

**Indexes:**
- `ix_projects_erpnext_id`: (erpnext_id) (unique)
- `ix_projects_lead_id`: (lead_id)
- `ix_projects_service_team_id`: (service_team_id)
- `uq_projects_number`: (number) (unique)

### `public.proposed_route_revisions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| quote_id | uuid |  |  |
| revision_number | integer |  |  |
| status | proposedrouterevisionstatus |  |  |
| route_geom (PostGIS: LINESTRING, SRID:4326) | geometry | YES |  |
| length_meters | double precision | YES |  |
| submitted_at | timestamp with time zone | YES |  |
| submitted_by_person_id | uuid | YES |  |
| reviewed_at | timestamp with time zone | YES |  |
| reviewed_by_person_id | uuid | YES |  |
| review_notes | text | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `quote_id` -> `public.project_quotes.id`
- `reviewed_by_person_id` -> `public.people.id`
- `submitted_by_person_id` -> `public.people.id`

**Indexes:**
- `idx_proposed_route_revisions_route_geom`: (route_geom)
- `uq_proposed_route_quote_revision`: (quote_id, revision_number) (unique)

### `public.queue_mappings`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| nas_device_id | uuid |  |  |
| queue_name | varchar(255) |  |  |
| subscription_id | uuid |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_queue_mappings_device_queue`: (nas_device_id, queue_name) (unique)

### `public.quote_line_items`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| quote_id | uuid |  |  |
| item_type | varchar(80) | YES |  |
| description | text | YES |  |
| cable_type | varchar(120) | YES |  |
| fiber_count | integer | YES |  |
| splice_count | integer | YES |  |
| quantity | numeric(12,3) |  |  |
| unit_price | numeric(12,2) |  |  |
| amount | numeric(12,2) |  |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `quote_id` -> `public.project_quotes.id`

### `public.role_permissions`
PK: `id` | ~319 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| role_id | uuid |  |  |
| permission_id | uuid |  |  |

**Foreign keys:**
- `permission_id` -> `public.permissions.id`
- `role_id` -> `public.roles.id`

**Indexes:**
- `uq_role_permissions_role_permission`: (role_id, permission_id) (unique)

### `public.roles`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(80) |  |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `uq_roles_name`: (name) (unique)

### `public.sales_order_lines`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| sales_order_id | uuid |  |  |
| inventory_item_id | uuid | YES |  |
| description | varchar(255) |  |  |
| quantity | numeric(12,3) |  |  |
| unit_price | numeric(12,2) |  |  |
| amount | numeric(12,2) |  |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `inventory_item_id` -> `public.inventory_items.id`
- `sales_order_id` -> `public.sales_orders.id`

### `public.sales_orders`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| quote_id | uuid | YES |  |
| person_id | uuid |  |  |
| order_number | varchar(80) | YES |  |
| status | salesorderstatus |  |  |
| payment_status | salesorderpaymentstatus |  |  |
| currency | varchar(3) |  |  |
| subtotal | numeric(12,2) |  |  |
| tax_total | numeric(12,2) |  |  |
| total | numeric(12,2) |  |  |
| amount_paid | numeric(12,2) |  |  |
| balance_due | numeric(12,2) |  |  |
| payment_due_date | timestamp with time zone | YES |  |
| paid_at | timestamp with time zone | YES |  |
| deposit_required | boolean |  |  |
| deposit_paid | boolean |  |  |
| contract_signed | boolean |  |  |
| signed_at | timestamp with time zone | YES |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `quote_id` -> `public.crm_quotes.id`

**Indexes:**
- `uq_sales_orders_order_number`: (order_number) (unique)
- `uq_sales_orders_quote_id`: (quote_id) (unique)

### `public.scheduled_tasks`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| task_name | varchar(200) |  |  |
| schedule_type | scheduletype |  |  |
| interval_seconds | integer |  |  |
| args_json | json | YES |  |
| kwargs_json | json | YES |  |
| enabled | boolean |  |  |
| last_run_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.service_buildings`
PK: `id` | ~2,742 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| code | varchar(60) | YES |  |
| name | varchar(200) |  |  |
| clli | varchar(20) | YES |  |
| latitude | double precision | YES |  |
| longitude | double precision | YES |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| boundary_geom (PostGIS: POLYGON, SRID:4326) | geometry | YES |  |
| street | varchar(200) | YES |  |
| city | varchar(100) | YES |  |
| state | varchar(60) | YES |  |
| zip_code | varchar(20) | YES |  |
| work_order | varchar(100) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `idx_service_buildings_boundary_geom`: (boundary_geom)
- `idx_service_buildings_geom`: (geom)
- `service_buildings_code_key`: (code) (unique)

### `public.service_qualifications`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| coverage_area_id | uuid | YES |  |
| address_id | uuid | YES |  |
| latitude | double precision |  |  |
| longitude | double precision |  |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| requested_tech | varchar(60) | YES |  |
| status | qualificationstatus |  |  |
| buildout_status | buildoutstatus | YES |  |
| estimated_install_window | varchar(120) | YES |  |
| reasons | json | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `coverage_area_id` -> `public.coverage_areas.id`

**Indexes:**
- `idx_service_qualifications_geom`: (geom)

### `public.service_team_members`
PK: `id` | ~114 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| team_id | uuid |  |  |
| person_id | uuid |  |  |
| role | serviceteammemberrole |  | 'member'::serviceteammemberrole |
| is_active | boolean |  | true |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `team_id` -> `public.service_teams.id`

**Indexes:**
- `ix_service_team_members_person_id`: (person_id)
- `uq_service_team_member`: (team_id, person_id) (unique)

### `public.service_teams`
PK: `id` | ~73 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  | gen_random_uuid() |
| name | varchar(160) |  |  |
| team_type | serviceteamtype |  |  |
| region | varchar(80) | YES |  |
| manager_person_id | uuid | YES |  |
| erp_department | varchar(120) | YES |  |
| is_active | boolean |  | true |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `manager_person_id` -> `public.people.id`

**Indexes:**
- `ix_service_teams_erp_department`: (erp_department) (unique)

### `public.sessions`
PK: `id` | ~979 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| status | sessionstatus |  |  |
| token_hash | varchar(255) |  |  |
| previous_token_hash | varchar(255) | YES |  |
| token_rotated_at | timestamp with time zone | YES |  |
| ip_address | varchar(64) | YES |  |
| user_agent | varchar(512) | YES |  |
| created_at | timestamp with time zone |  |  |
| last_seen_at | timestamp with time zone | YES |  |
| expires_at | timestamp with time zone |  |  |
| revoked_at | timestamp with time zone | YES |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ux_sessions_previous_token_hash`: (previous_token_hash) (unique)
- `ux_sessions_token_hash`: (token_hash) (unique)

### `public.shifts`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| technician_id | uuid |  |  |
| start_at | timestamp with time zone |  |  |
| end_at | timestamp with time zone |  |  |
| timezone | varchar(64) | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| shift_type | varchar(60) | YES |  |
| erp_id | varchar(100) | YES |  |
| updated_at | timestamp with time zone | YES | now() |

**Foreign keys:**
- `technician_id` -> `public.technician_profiles.id`

**Indexes:**
- `ix_shifts_erp_id`: (erp_id) (unique)

### `public.skills`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(120) |  |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.sla_breaches`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| clock_id | uuid |  |  |
| status | slabreachstatus |  |  |
| breached_at | timestamp with time zone |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `clock_id` -> `public.sla_clocks.id`

### `public.sla_clocks`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| policy_id | uuid |  |  |
| entity_type | workflowentitytype |  |  |
| entity_id | uuid |  |  |
| priority | varchar(40) | YES |  |
| status | slaclockstatus |  |  |
| started_at | timestamp with time zone |  |  |
| paused_at | timestamp with time zone | YES |  |
| total_paused_seconds | integer |  |  |
| due_at | timestamp with time zone |  |  |
| completed_at | timestamp with time zone | YES |  |
| breached_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `policy_id` -> `public.sla_policies.id`

### `public.sla_policies`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| entity_type | workflowentitytype |  |  |
| description | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.sla_targets`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| policy_id | uuid |  |  |
| priority | varchar(40) | YES |  |
| target_minutes | integer |  |  |
| warning_minutes | integer | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `policy_id` -> `public.sla_policies.id`

**Indexes:**
- `uq_sla_targets_policy_priority`: (policy_id, priority) (unique)

### `public.spatial_ref_sys`
PK: `srid` | ~8,500 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **srid** (PK) | integer |  |  |
| auth_name | varchar(256) | YES |  |
| auth_srid | integer | YES |  |
| srtext | varchar(2048) | YES |  |
| proj4text | varchar(2048) | YES |  |

### `public.splitter_ports`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| splitter_id | uuid |  |  |
| port_number | integer |  |  |
| port_type | splitterporttype |  |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `splitter_id` -> `public.splitters.id`

**Indexes:**
- `uq_splitter_ports_splitter_port_number`: (splitter_id, port_number) (unique)

### `public.splitters`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| fdh_id | uuid | YES |  |
| name | varchar(160) |  |  |
| splitter_ratio | varchar(40) | YES |  |
| input_ports | integer |  |  |
| output_ports | integer |  |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `fdh_id` -> `public.fdh_cabinets.id`

**Indexes:**
- `uq_splitters_fdh_name`: (fdh_id, name) (unique)

### `public.subscribers`
PK: `id` | ~9,364 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid | YES |  |
| organization_id | uuid | YES |  |
| external_id | varchar(120) | YES |  |
| external_system | varchar(60) | YES |  |
| subscriber_number | varchar(60) | YES |  |
| account_number | varchar(60) | YES |  |
| status | subscriberstatus |  |  |
| service_name | varchar(160) | YES |  |
| service_plan | varchar(120) | YES |  |
| service_speed | varchar(60) | YES |  |
| service_address_line1 | varchar(120) | YES |  |
| service_address_line2 | varchar(120) | YES |  |
| service_city | varchar(80) | YES |  |
| service_region | varchar(80) | YES |  |
| service_postal_code | varchar(20) | YES |  |
| service_country_code | varchar(2) | YES |  |
| balance | varchar(40) | YES |  |
| currency | varchar(3) | YES |  |
| billing_cycle | varchar(40) | YES |  |
| next_bill_date | timestamp with time zone | YES |  |
| activated_at | timestamp with time zone | YES |  |
| suspended_at | timestamp with time zone | YES |  |
| terminated_at | timestamp with time zone | YES |  |
| last_synced_at | timestamp with time zone | YES |  |
| sync_error | varchar(500) | YES |  |
| sync_metadata | json | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| sales_order_id | uuid | YES |  |

**Foreign keys:**
- `sales_order_id` -> `public.sales_orders.id`
- `organization_id` -> `public.organizations.id`
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_subscribers_external`: (external_system, external_id)
- `ix_subscribers_sales_order`: (sales_order_id)
- `ix_subscribers_status`: (status)
- `subscribers_subscriber_number_key`: (subscriber_number) (unique)

### `public.survey_invitations`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| survey_id | uuid |  |  |
| person_id | uuid |  |  |
| token | varchar(64) |  |  |
| email | varchar(255) |  |  |
| status | surveyinvitationstatusenum | YES | 'pending'::surveyinvitationstatusenum |
| notification_id | uuid | YES |  |
| ticket_id | uuid | YES |  |
| work_order_id | uuid | YES |  |
| sent_at | timestamp with time zone | YES |  |
| opened_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| expires_at | timestamp with time zone | YES |  |
| created_at | timestamp with time zone | YES | now() |

**Foreign keys:**
- `notification_id` -> `public.notifications.id`
- `person_id` -> `public.people.id`
- `survey_id` -> `public.surveys.id`
- `ticket_id` -> `public.tickets.id`
- `work_order_id` -> `public.work_orders.id`

**Indexes:**
- `ix_survey_invitations_survey_id`: (survey_id)
- `ix_survey_invitations_token`: (token) (unique)
- `survey_invitations_token_key`: (token) (unique)
- `uq_survey_invitation_person`: (survey_id, person_id) (unique)

### `public.survey_los_paths`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| survey_id | uuid |  |  |
| from_point_id | uuid |  |  |
| to_point_id | uuid |  |  |
| distance_m | double precision | YES |  |
| bearing_deg | double precision | YES |  |
| has_clear_los | boolean | YES |  |
| fresnel_clearance_pct | double precision | YES |  |
| max_obstruction_m | double precision | YES |  |
| obstruction_distance_m | double precision | YES |  |
| elevation_profile | json | YES |  |
| free_space_loss_db | double precision | YES |  |
| estimated_rssi_dbm | double precision | YES |  |
| analysis_timestamp | timestamp with time zone | YES |  |
| sample_count | integer | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `from_point_id` -> `public.survey_points.id`
- `survey_id` -> `public.wireless_site_surveys.id`
- `to_point_id` -> `public.survey_points.id`

### `public.survey_points`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| survey_id | uuid |  |  |
| name | varchar(160) |  |  |
| point_type | surveypointtype |  |  |
| latitude | double precision |  |  |
| longitude | double precision |  |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| ground_elevation_m | double precision | YES |  |
| elevation_source | varchar(50) | YES |  |
| elevation_tile | varchar(20) | YES |  |
| antenna_height_m | double precision |  |  |
| antenna_gain_dbi | double precision | YES |  |
| tx_power_dbm | double precision | YES |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| sort_order | integer |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `survey_id` -> `public.wireless_site_surveys.id`

**Indexes:**
- `idx_survey_points_geom`: (geom)

### `public.survey_responses`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| survey_id | uuid |  |  |
| work_order_id | uuid | YES |  |
| ticket_id | uuid | YES |  |
| responses | json | YES |  |
| rating | integer | YES |  |
| created_at | timestamp with time zone |  |  |
| invitation_id | uuid | YES |  |
| person_id | uuid | YES |  |
| completed_at | timestamp with time zone | YES |  |

**Foreign keys:**
- `invitation_id` -> `public.survey_invitations.id`
- `person_id` -> `public.people.id`
- `survey_id` -> `public.surveys.id`
- `ticket_id` -> `public.tickets.id`
- `work_order_id` -> `public.work_orders.id`

### `public.surveys`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| description | text | YES |  |
| questions | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| status | customersurveystatusenum | YES | 'draft'::customersurveystatusenum |
| trigger_type | surveytriggertypeenum | YES | 'manual'::surveytriggertypeenum |
| public_slug | varchar(120) | YES |  |
| thank_you_message | text | YES |  |
| expires_at | timestamp with time zone | YES |  |
| segment_filter | json | YES |  |
| created_by_id | uuid | YES |  |
| total_invited | integer | YES | 0 |
| total_responses | integer | YES | 0 |
| avg_rating | double precision | YES |  |
| nps_score | double precision | YES |  |

**Foreign keys:**
- `created_by_id` -> `public.people.id`

**Indexes:**
- `ix_surveys_public_slug`: (public_slug) (unique)
- `surveys_public_slug_key`: (public_slug) (unique)

### `public.technician_profiles`
PK: `id` | ~47 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| title | varchar(120) | YES |  |
| region | varchar(120) | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| erp_employee_id | varchar(100) | YES |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_technician_profiles_erp_employee_id`: (erp_employee_id) (unique)

### `public.technician_skills`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| technician_id | uuid |  |  |
| skill_id | uuid |  |  |
| proficiency | integer | YES |  |
| is_primary | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `skill_id` -> `public.skills.id`
- `technician_id` -> `public.technician_profiles.id`

### `public.ticket_assignees`
PK: `ticket_id, person_id` | ~174 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **ticket_id** (PK) | uuid |  |  |
| **person_id** (PK) | uuid |  |  |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `ticket_id` -> `public.tickets.id`

**Indexes:**
- `ix_ticket_assignees_person_id`: (person_id)

### `public.ticket_comments`
PK: `id` | ~26,993 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| ticket_id | uuid |  |  |
| author_person_id | uuid | YES |  |
| body | text |  |  |
| is_internal | boolean |  |  |
| attachments | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `author_person_id` -> `public.people.id`
- `ticket_id` -> `public.tickets.id`

### `public.ticket_sla_events`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| ticket_id | uuid |  |  |
| event_type | varchar(60) |  |  |
| expected_at | timestamp with time zone | YES |  |
| actual_at | timestamp with time zone | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `ticket_id` -> `public.tickets.id`

### `public.ticket_status_transitions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| from_status | varchar(40) |  |  |
| to_status | varchar(40) |  |  |
| requires_note | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.tickets`
PK: `id` | ~19,242 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| subscriber_id | uuid | YES |  |
| created_by_person_id | uuid | YES |  |
| assigned_to_person_id | uuid | YES |  |
| title | varchar(200) |  |  |
| description | text | YES |  |
| status | ticketstatus |  |  |
| priority | ticketpriority |  |  |
| channel | ticketchannel |  |  |
| tags | json | YES |  |
| metadata | json | YES |  |
| due_at | timestamp with time zone | YES |  |
| resolved_at | timestamp with time zone | YES |  |
| closed_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| ticket_type | varchar(120) | YES |  |
| lead_id | uuid | YES |  |
| customer_person_id | uuid | YES |  |
| number | varchar(40) | YES |  |
| region | varchar(80) | YES |  |
| ticket_manager_person_id | uuid | YES |  |
| assistant_manager_person_id | uuid | YES |  |
| service_team_id | uuid | YES |  |
| erpnext_id | varchar(100) | YES |  |

**Foreign keys:**
- `assistant_manager_person_id` -> `public.people.id`
- `customer_person_id` -> `public.people.id`
- `lead_id` -> `public.crm_leads.id`
- `service_team_id` -> `public.service_teams.id`
- `ticket_manager_person_id` -> `public.people.id`
- `assigned_to_person_id` -> `public.people.id`
- `created_by_person_id` -> `public.people.id`
- `subscriber_id` -> `public.subscribers.id`

**Indexes:**
- `ix_tickets_customer_person_id`: (customer_person_id)
- `ix_tickets_erpnext_id`: (erpnext_id) (unique)
- `ix_tickets_lead_id`: (lead_id)
- `ix_tickets_service_team_id`: (service_team_id)
- `uq_tickets_number`: (number) (unique)

### `public.user_credentials`
PK: `id` | ~135 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| provider | authprovider |  |  |
| username | varchar(150) | YES |  |
| password_hash | varchar(255) | YES |  |
| must_change_password | boolean |  |  |
| password_updated_at | timestamp with time zone | YES |  |
| failed_login_attempts | integer |  |  |
| locked_until | timestamp with time zone | YES |  |
| last_login_at | timestamp with time zone | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_user_credentials_local_username_unique`: (username) (unique)

### `public.user_filter_preferences`
PK: `id` | ~53 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| person_id | uuid |  |  |
| page_key | varchar(120) |  |  |
| state | json |  |  |
| created_at | timestamp with time zone |  | now() |
| updated_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `person_id` -> `public.people.id`

**Indexes:**
- `ix_user_filter_preferences_person_id`: (person_id)
- `uq_user_filter_preferences_person_page`: (person_id, page_key) (unique)

### `public.vendor_users`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| vendor_id | uuid |  |  |
| person_id | uuid |  |  |
| role | varchar(60) | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `vendor_id` -> `public.vendors.id`

**Indexes:**
- `uq_vendor_users_vendor_person`: (vendor_id, person_id) (unique)

### `public.vendors`
PK: `id` | ~11 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| code | varchar(60) | YES |  |
| contact_name | varchar(160) | YES |  |
| contact_email | varchar(255) | YES |  |
| contact_phone | varchar(40) | YES |  |
| license_number | varchar(120) | YES |  |
| service_area | text | YES |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |
| erp_id | varchar(100) | YES |  |

**Indexes:**
- `ix_vendors_erp_id`: (erp_id) (unique)
- `vendors_code_key`: (code) (unique)

### `public.webhook_dead_letters`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| channel | varchar(40) |  |  |
| trace_id | varchar(64) | YES |  |
| message_id | varchar(200) | YES |  |
| raw_payload | json | YES |  |
| error | text | YES |  |
| created_at | timestamp with time zone |  | now() |

**Indexes:**
- `ix_webhook_dead_letters_channel_created`: (channel, created_at)

### `public.webhook_deliveries`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| subscription_id | uuid |  |  |
| endpoint_id | uuid |  |  |
| event_type | webhookeventtype |  |  |
| status | webhookdeliverystatus |  |  |
| attempt_count | integer |  |  |
| last_attempt_at | timestamp with time zone | YES |  |
| delivered_at | timestamp with time zone | YES |  |
| response_status | integer | YES |  |
| error | text | YES |  |
| payload | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `endpoint_id` -> `public.webhook_endpoints.id`
- `subscription_id` -> `public.webhook_subscriptions.id`

### `public.webhook_endpoints`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| url | varchar(500) |  |  |
| connector_config_id | uuid | YES |  |
| secret | varchar(255) | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `connector_config_id` -> `public.connector_configs.id`

**Indexes:**
- `uq_webhook_endpoints_url`: (url) (unique)

### `public.webhook_subscriptions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| endpoint_id | uuid |  |  |
| event_type | webhookeventtype |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `endpoint_id` -> `public.webhook_endpoints.id`

**Indexes:**
- `uq_webhook_subscriptions_endpoint_event`: (endpoint_id, event_type) (unique)

### `public.widget_visitor_sessions`
PK: `id` | ~36 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| widget_config_id | uuid |  |  |
| visitor_token | varchar(64) |  |  |
| fingerprint_hash | varchar(64) | YES |  |
| person_id | uuid | YES |  |
| conversation_id | uuid | YES |  |
| ip_address | varchar(45) | YES |  |
| user_agent | varchar(512) | YES |  |
| page_url | varchar(2048) | YES |  |
| referrer_url | varchar(2048) | YES |  |
| metadata | json | YES |  |
| is_identified | boolean |  | false |
| identified_at | timestamp with time zone | YES |  |
| identified_email | varchar(255) | YES |  |
| identified_name | varchar(160) | YES |  |
| last_active_at | timestamp with time zone |  | now() |
| created_at | timestamp with time zone |  | now() |

**Foreign keys:**
- `conversation_id` -> `public.crm_conversations.id`
- `person_id` -> `public.people.id`
- `widget_config_id` -> `public.chat_widget_configs.id`

**Indexes:**
- `ix_widget_visitor_sessions_fingerprint_hash`: (fingerprint_hash)
- `ix_widget_visitor_sessions_visitor_token`: (visitor_token) (unique)
- `ix_widget_visitor_sessions_widget_config_id`: (widget_config_id)
- `widget_visitor_sessions_visitor_token_key`: (visitor_token) (unique)

### `public.wireguard_connection_logs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| peer_id | uuid |  |  |
| connected_at | timestamp with time zone |  |  |
| disconnected_at | timestamp with time zone | YES |  |
| endpoint_ip | varchar(64) | YES |  |
| peer_address | varchar(64) | YES |  |
| rx_bytes | bigint |  |  |
| tx_bytes | bigint |  |  |
| disconnect_reason | varchar(255) | YES |  |

**Foreign keys:**
- `peer_id` -> `public.wireguard_peers.id`

### `public.wireguard_peers`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| server_id | uuid |  |  |
| name | varchar(160) |  |  |
| description | text | YES |  |
| public_key | varchar(64) |  |  |
| private_key | text | YES |  |
| preshared_key | text | YES |  |
| allowed_ips | json | YES |  |
| peer_address | varchar(64) | YES |  |
| peer_address_v6 | varchar(64) | YES |  |
| persistent_keepalive | integer |  |  |
| status | wireguardpeerstatus |  |  |
| provision_token_hash | varchar(128) | YES |  |
| provision_token_expires_at | timestamp with time zone | YES |  |
| last_handshake_at | timestamp with time zone | YES |  |
| endpoint_ip | varchar(64) | YES |  |
| rx_bytes | bigint |  |  |
| tx_bytes | bigint |  |  |
| metadata | json | YES |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `server_id` -> `public.wireguard_servers.id`

### `public.wireguard_servers`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(160) |  |  |
| description | text | YES |  |
| interface_name | varchar(32) |  |  |
| listen_port | integer |  |  |
| private_key | text | YES |  |
| public_key | varchar(64) | YES |  |
| public_host | varchar(255) | YES |  |
| public_port | integer | YES |  |
| vpn_address | varchar(64) |  |  |
| vpn_address_v6 | varchar(64) | YES |  |
| mtu | integer |  |  |
| dns_servers | json | YES |  |
| is_active | boolean |  |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `wireguard_servers_name_key`: (name) (unique)

### `public.wireless_masts`
PK: `id` | ~515 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| pop_site_id | uuid | YES |  |
| name | varchar(160) |  |  |
| latitude | double precision |  |  |
| longitude | double precision |  |  |
| geom (PostGIS: POINT, SRID:4326) | geometry | YES |  |
| height_m | double precision | YES |  |
| structure_type | varchar(80) | YES |  |
| owner | varchar(160) | YES |  |
| status | varchar(40) |  |  |
| is_active | boolean |  |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Indexes:**
- `idx_wireless_masts_geom`: (geom)

### `public.wireless_site_surveys`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| name | varchar(200) |  |  |
| description | text | YES |  |
| status | surveystatus |  |  |
| min_latitude | double precision | YES |  |
| min_longitude | double precision | YES |  |
| max_latitude | double precision | YES |  |
| max_longitude | double precision | YES |  |
| frequency_mhz | double precision | YES |  |
| default_antenna_height_m | double precision |  |  |
| default_tx_power_dbm | double precision |  |  |
| notes | text | YES |  |
| metadata | json | YES |  |
| created_by_id | uuid | YES |  |
| project_id | uuid | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `created_by_id` -> `public.people.id`
- `project_id` -> `public.projects.id`

### `public.work_logs`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| person_id | uuid |  |  |
| start_at | timestamp with time zone |  |  |
| end_at | timestamp with time zone | YES |  |
| minutes | integer |  |  |
| hourly_rate | numeric(12,2) | YES |  |
| notes | text | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `work_order_id` -> `public.work_orders.id`

### `public.work_order_assignment_queue`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| status | dispatchqueuestatus |  |  |
| reason | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `work_order_id` -> `public.work_orders.id`

### `public.work_order_assignments`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| person_id | uuid |  |  |
| role | varchar(60) | YES |  |
| assigned_at | timestamp with time zone |  |  |
| is_primary | boolean |  |  |

**Foreign keys:**
- `person_id` -> `public.people.id`
- `work_order_id` -> `public.work_orders.id`

### `public.work_order_materials`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| item_id | uuid |  |  |
| reservation_id | uuid | YES |  |
| quantity | integer |  |  |
| status | materialstatus |  |  |
| notes | text | YES |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `item_id` -> `public.inventory_items.id`
- `reservation_id` -> `public.inventory_reservations.id`
- `work_order_id` -> `public.work_orders.id`

### `public.work_order_notes`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| work_order_id | uuid |  |  |
| author_person_id | uuid | YES |  |
| body | text |  |  |
| is_internal | boolean |  |  |
| attachments | json | YES |  |
| created_at | timestamp with time zone |  |  |

**Foreign keys:**
- `author_person_id` -> `public.people.id`
- `work_order_id` -> `public.work_orders.id`

### `public.work_order_status_transitions`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| from_status | varchar(40) |  |  |
| to_status | varchar(40) |  |  |
| requires_note | boolean |  |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

### `public.work_orders`
PK: `id` | ~0 rows

| Column | Type | Null | Default |
|--------|------|------|---------|
| **id** (PK) | uuid |  |  |
| title | varchar(200) |  |  |
| description | text | YES |  |
| status | workorderstatus |  |  |
| priority | workorderpriority |  |  |
| work_type | workordertype |  |  |
| subscriber_id | uuid | YES |  |
| ticket_id | uuid | YES |  |
| project_id | uuid | YES |  |
| address_id | uuid | YES |  |
| assigned_to_person_id | uuid | YES |  |
| scheduled_start | timestamp with time zone | YES |  |
| scheduled_end | timestamp with time zone | YES |  |
| started_at | timestamp with time zone | YES |  |
| completed_at | timestamp with time zone | YES |  |
| required_skills | json | YES |  |
| estimated_duration_minutes | integer | YES |  |
| estimated_arrival_at | timestamp with time zone | YES |  |
| tags | json | YES |  |
| metadata | json | YES |  |
| is_active | boolean |  |  |
| created_at | timestamp with time zone |  |  |
| updated_at | timestamp with time zone |  |  |

**Foreign keys:**
- `assigned_to_person_id` -> `public.people.id`
- `project_id` -> `public.projects.id`
- `subscriber_id` -> `public.subscribers.id`
- `ticket_id` -> `public.tickets.id`
