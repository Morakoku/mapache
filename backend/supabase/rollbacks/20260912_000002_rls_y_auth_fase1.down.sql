-- ============================================================================
-- Mapache CRM — REVERSO de la FASE 1 (RLS + policies + rol BI)
-- Archivo:  rollbacks/20260912_000002_rls_y_auth_fase1.down.sql
--
-- IMPORTANTE:
--   · NO vive en migrations/ porque el CLI de Supabase ejecutaría cualquier
--     *.sql de esa carpeta como una migración hacia adelante.
--   · Este script NO borra tablas, columnas ni datos. Solo deshace policies,
--     RLS, grants y el rol creado por la migración.
--   · Usar únicamente para revertir la Fase 1 ante un problema en producción.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Eliminar policies creadas por la Fase 1
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_owner_tables text[] := ARRAY[
        'activities', 'call_logs', 'call_scripts', 'companies', 'contacts',
        'conversation_messages', 'conversations', 'email_accounts',
        'email_messages', 'email_templates', 'follow_ups', 'jobs', 'leads',
        'pipeline_stages', 'searches', 'sequences', 'services',
        'suppression_list', 'tasks'
    ];
    v_child_tables text[] := ARRAY[
        'company_signals', 'company_socials', 'company_sources',
        'email_links', 'sequence_steps', 'search_runs', 'search_results'
    ];
    v_bi_tables text[] := ARRAY[
        'activities', 'audit_log', 'call_logs', 'call_scripts', 'companies',
        'company_signals', 'company_socials', 'company_sources', 'contacts',
        'conversation_messages', 'conversations', 'email_events', 'email_links',
        'email_messages', 'email_templates', 'follow_ups', 'idempotency_events',
        'jobs', 'lead_stage_history', 'leads', 'pipeline_stages',
        'search_results', 'search_runs', 'searches', 'sequence_steps',
        'sequences', 'services', 'suppression_list', 'tasks'
    ];
    v_all_tables text[] := ARRAY[
        'activities', 'app_settings', 'audit_log', 'call_logs', 'call_scripts',
        'companies', 'company_signals', 'company_socials', 'company_sources',
        'contacts', 'conversation_messages', 'conversations', 'email_accounts',
        'email_events', 'email_links', 'email_messages', 'email_templates',
        'follow_ups', 'idempotency_events', 'jobs', 'lead_stage_history',
        'leads', 'pipeline_stages', 'search_results', 'search_runs',
        'searches', 'sequence_steps', 'sequences', 'services',
        'suppression_list', 'tasks'
    ];
    t text;
    cmd text;
BEGIN
    FOREACH t IN ARRAY v_owner_tables LOOP
        FOREACH cmd IN ARRAY ARRAY['select', 'insert', 'update', 'delete'] LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                           'p_' || t || '_owner_' || cmd, t);
        END LOOP;
    END LOOP;

    FOREACH t IN ARRAY v_child_tables LOOP
        FOREACH cmd IN ARRAY ARRAY['select', 'insert', 'update', 'delete'] LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                           'p_' || t || '_parent_' || cmd, t);
        END LOOP;
    END LOOP;

    FOREACH t IN ARRAY v_bi_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_bi_select', t);
    END LOOP;

    FOREACH t IN ARRAY v_all_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_trusted_all', t);
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- 2) Revocar grants otorgados a `authenticated` y a BI
-- ----------------------------------------------------------------------------
REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA crm FROM authenticated;
REVOKE USAGE ON SCHEMA crm FROM authenticated;

REVOKE SELECT ON ALL TABLES IN SCHEMA crm FROM crm_bi_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA crm REVOKE SELECT ON TABLES FROM crm_bi_readonly;
REVOKE USAGE ON SCHEMA crm FROM crm_bi_readonly;

-- `ops_veyra` conserva sus grants originales (ya existían antes de la Fase 1);
-- solo se retiran las policies de compatibilidad (hecho en el paso 1).

-- ----------------------------------------------------------------------------
-- 3) Deshabilitar RLS en las 31 tablas (estado previo a la Fase 1)
-- ----------------------------------------------------------------------------
ALTER TABLE IF EXISTS crm.activities            DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.app_settings          DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.audit_log             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.call_logs             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.call_scripts          DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.companies             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_signals       DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_socials       DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_sources       DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.contacts              DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.conversation_messages DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.conversations         DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_accounts        DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_events          DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_links           DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_messages        DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_templates       DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.follow_ups            DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.idempotency_events    DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.jobs                  DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.lead_stage_history    DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.leads                 DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.pipeline_stages       DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.search_results        DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.search_runs           DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.searches              DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.sequence_steps        DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.sequences             DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.services              DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.suppression_list      DISABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.tasks                 DISABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------------------------
-- 4) Eliminar el rol de BI (no se toca ningún otro rol)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_bi_readonly') THEN
        EXECUTE 'DROP OWNED BY crm_bi_readonly';
        EXECUTE 'DROP ROLE crm_bi_readonly';
    END IF;
END
$$;
