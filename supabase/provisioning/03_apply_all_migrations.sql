-- =============================================================================
-- Creative Studio Provisioning: 03_apply_all_migrations.sql
-- Exact ordered apply script for all Creative Studio migrations.
-- All four migrations are strictly idempotent and safe to re-run.
-- =============================================================================

-- Execution order:
-- 1. 20261030210000_cs_b1_platform_foundation.sql
--    Creates schemas (public, cs_private), enums, base tables (workspaces, runs,
--    work items, assets, download grants), and base SECURITY DEFINER procedures.
--
-- 2. 20261031140100_cs_b2_brand_campaign_inputs.sql
--    Creates brand package imports, campaign source adapters, external bindings
--    (marketing-os, bci), and pre-run operation queues.
--
-- 3. 20261031140300_cs_b3_provider_execution.sql
--    Creates provider execution pipeline, ledger, attempt settling, cost quotes,
--    encrypted credentials table, and single Higgsfield engine integration.
--
-- 4. 20261031140400_cs_b4_composition_exports.sql
--    Creates composition versions, cs_exports (v1 and v2), cs_handoffs,
--    and export publication procedures.

\echo 'Applying CS-B1 platform foundation...'
\i ../migrations/20261030210000_cs_b1_platform_foundation.sql

\echo 'Applying CS-B2 brand & campaign inputs...'
\i ../migrations/20261031140100_cs_b2_brand_campaign_inputs.sql

\echo 'Applying CS-B3 provider execution...'
\i ../migrations/20261031140300_cs_b3_provider_execution.sql

\echo 'Applying CS-B4 composition & exports...'
\i ../migrations/20261031140400_cs_b4_composition_exports.sql

\echo 'All Creative Studio migrations applied successfully.'
