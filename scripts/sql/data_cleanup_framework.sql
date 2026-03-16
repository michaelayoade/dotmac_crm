-- Dotmac CRM: No-trigger Data Cleanup + Analysis Framework
-- Works with DML-only access (SELECT/UPDATE/DELETE).
-- Safe pattern: preview -> dry-run in transaction -> validate -> commit.

-- =========================================================
-- 0) TABLE + KEY COLUMN INVENTORY (from PostgreSQL catalog)
-- =========================================================
-- Lists PK and UNIQUE keys for all public tables.
SELECT
  t.table_name,
  tc.constraint_type,
  tc.constraint_name,
  string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS key_columns
FROM information_schema.tables t
LEFT JOIN information_schema.table_constraints tc
  ON tc.table_schema = t.table_schema
 AND tc.table_name = t.table_name
 AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
LEFT JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_schema = tc.constraint_schema
 AND kcu.constraint_name = tc.constraint_name
WHERE t.table_schema = 'public'
  AND t.table_type = 'BASE TABLE'
GROUP BY t.table_name, tc.constraint_type, tc.constraint_name
ORDER BY t.table_name, tc.constraint_type, tc.constraint_name;


-- =========================================================
-- 1) QUICK DATA-HEALTH SCORECARD
-- =========================================================
-- 1a) Null rate checks (edit list as needed)
SELECT 'people.email' AS metric, COUNT(*) FILTER (WHERE email IS NULL OR btrim(email) = '') AS bad_rows, COUNT(*) AS total FROM people
UNION ALL
SELECT 'crm_messages.external_id', COUNT(*) FILTER (WHERE external_id IS NULL OR btrim(external_id) = ''), COUNT(*) FROM crm_messages
UNION ALL
SELECT 'subscribers.subscriber_number', COUNT(*) FILTER (WHERE subscriber_number IS NULL OR btrim(subscriber_number) = ''), COUNT(*) FROM subscribers;

-- 1b) Known duplicate risk checks by important keys
SELECT 'people(email)' AS check_name, email::text AS key_value, COUNT(*) AS cnt
FROM people
WHERE email IS NOT NULL AND btrim(email) <> ''
GROUP BY email
HAVING COUNT(*) > 1
ORDER BY cnt DESC, key_value
LIMIT 200;

SELECT 'crm_messages(channel_type, external_id)' AS check_name,
       (channel_type::text || '|' || external_id::text) AS key_value,
       COUNT(*) AS cnt
FROM crm_messages
WHERE external_id IS NOT NULL AND btrim(external_id) <> ''
GROUP BY channel_type, external_id
HAVING COUNT(*) > 1
ORDER BY cnt DESC, key_value
LIMIT 200;

SELECT 'crm_conversation_tags(conversation_id, tag)' AS check_name,
       (conversation_id::text || '|' || tag) AS key_value,
       COUNT(*) AS cnt
FROM crm_conversation_tags
GROUP BY conversation_id, tag
HAVING COUNT(*) > 1
ORDER BY cnt DESC, key_value
LIMIT 200;


-- =========================================================
-- 2) REUSABLE SAFE DEDUPE TEMPLATE
-- =========================================================
-- Replace:
--   {{table_name}}, {{pk_col}}, {{key_cols}}, {{winner_order}}
-- Example winner_order: updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC

/*
BEGIN;

WITH ranked AS (
  SELECT
    {{pk_col}} AS pk,
    ROW_NUMBER() OVER (
      PARTITION BY {{key_cols}}
      ORDER BY {{winner_order}}
    ) AS rn
  FROM {{table_name}}
), doomed AS (
  SELECT pk FROM ranked WHERE rn > 1
)
-- Preview count first:
SELECT COUNT(*) AS rows_to_delete FROM doomed;

-- Optional: inspect sample rows before deleting
-- SELECT t.* FROM {{table_name}} t JOIN doomed d ON d.pk = t.{{pk_col}} LIMIT 200;

DELETE FROM {{table_name}} t
USING doomed d
WHERE t.{{pk_col}} = d.pk;

-- Validate no duplicates remain
-- SELECT {{key_cols}}, COUNT(*) FROM {{table_name}} GROUP BY {{key_cols}} HAVING COUNT(*) > 1;

COMMIT;
-- ROLLBACK;  -- use during rehearsal
*/


-- =========================================================
-- 3) CONCRETE CLEANUPS FOR HIGH-VALUE TABLES
-- =========================================================

-- 3a) people(email) duplicate cleanup: keep newest updated/created/id
BEGIN;
WITH ranked AS (
  SELECT
    id,
    email,
    ROW_NUMBER() OVER (
      PARTITION BY lower(btrim(email))
      ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
    ) AS rn
  FROM people
  WHERE email IS NOT NULL AND btrim(email) <> ''
), doomed AS (
  SELECT id FROM ranked WHERE rn > 1
)
SELECT COUNT(*) AS people_email_dupes_to_delete FROM doomed;
-- DELETE FROM people p USING doomed d WHERE p.id = d.id;
ROLLBACK;


-- 3b) crm_messages(channel_type, external_id) duplicate cleanup
BEGIN;
WITH ranked AS (
  SELECT
    id,
    channel_type,
    external_id,
    ROW_NUMBER() OVER (
      PARTITION BY channel_type, external_id
      ORDER BY created_at DESC NULLS LAST, id DESC
    ) AS rn
  FROM crm_messages
  WHERE external_id IS NOT NULL AND btrim(external_id) <> ''
), doomed AS (
  SELECT id FROM ranked WHERE rn > 1
)
SELECT COUNT(*) AS crm_messages_dupes_to_delete FROM doomed;
-- DELETE FROM crm_messages m USING doomed d WHERE m.id = d.id;
ROLLBACK;


-- 3c) crm_conversation_tags(conversation_id, tag) duplicate cleanup
BEGIN;
WITH ranked AS (
  SELECT
    id,
    conversation_id,
    tag,
    ROW_NUMBER() OVER (
      PARTITION BY conversation_id, tag
      ORDER BY id DESC
    ) AS rn
  FROM crm_conversation_tags
), doomed AS (
  SELECT id FROM ranked WHERE rn > 1
)
SELECT COUNT(*) AS conversation_tag_dupes_to_delete FROM doomed;
-- DELETE FROM crm_conversation_tags t USING doomed d WHERE t.id = d.id;
ROLLBACK;


-- =========================================================
-- 4) NORMALIZATION (UPDATE) EXAMPLES
-- =========================================================

-- 4a) Normalize email to lowercase + trimmed
BEGIN;
UPDATE people
SET email = lower(btrim(email))
WHERE email IS NOT NULL
  AND email <> lower(btrim(email));

SELECT COUNT(*) AS remaining_non_normalized_email
FROM people
WHERE email IS NOT NULL
  AND email <> lower(btrim(email));
ROLLBACK;

-- 4b) Normalize phone format (digits + plus only)
BEGIN;
UPDATE person_channels
SET address = regexp_replace(address, '[^0-9+]', '', 'g')
WHERE channel_type::text IN ('sms', 'whatsapp', 'phone')
  AND address IS NOT NULL;

SELECT COUNT(*) AS invalid_phone_chars_left
FROM person_channels
WHERE channel_type::text IN ('sms', 'whatsapp', 'phone')
  AND address ~ '[^0-9+]';
ROLLBACK;


-- =========================================================
-- 5) OPERATIONAL RUNBOOK
-- =========================================================
-- 1) Run section 1 (scorecard) and save output.
-- 2) For each cleanup query in section 3:
--    - Keep DELETE lines commented
--    - Run BEGIN + preview count + sample inspection + ROLLBACK
-- 3) Uncomment DELETE/UPDATE only after validation.
-- 4) Run in small windows (off-peak) and log rows changed.
-- 5) Re-run section 1 after each cleanup to verify improvement.
