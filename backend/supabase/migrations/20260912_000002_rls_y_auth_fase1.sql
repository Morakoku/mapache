-- ============================================================================
-- Mapache CRM — FASE 1: RLS + policies de ownership + rol de solo lectura
-- Archivo:   20260912_000002_rls_y_auth_fase1.sql
-- Esquema:   crm
-- Aplica a:  31 tablas del esquema `crm` en Supabase (proyecto Guaki)
-- Objetivo:  habilitar Row Level Security (RLS) y dejar policies coherentes
--            por owner/tenant, más un rol de solo lectura para BI/Appsmith.
--
-- ESTADO:    PENDIENTE DE REVISIÓN. NO aplicar a producción sin aprobación.
--
-- ----------------------------------------------------------------------------
-- HALLAZGOS DE LA AUDITORÍA (esquema en vivo, 2026-09-12)
-- ----------------------------------------------------------------------------
--  · Las 31 tablas de `crm` tienen RLS DESACTIVADO y 0 policies.
--  · El rol `ops_veyra` tiene SELECT/INSERT/UPDATE/DELETE total.
--  · La app entra por PostgREST con la service_role key (BYPASSA RLS) usando
--    `Accept-Profile: crm` / `Content-Profile: crm`.
--  · La introspección viva (PostgREST OpenAPI + pg_catalog) confirma:
--      19 tablas CON `owner_id` (uuid, nullable).
--      12 tablas SIN columna de ownership (ver EXCEPCIONES al final).
--
-- ----------------------------------------------------------------------------
-- SUPUESTOS DE OWNERSHIP (leer antes de aprobar)
-- ----------------------------------------------------------------------------
--  1. El tenant/owner canónico es `owner_id` (uuid). No existe `tenant_id` ni
--     `account_id` en la BD viva.
--  2. `owner_id` es NULL en gran parte de los datos actuales (MVP mono-usuario;
--     el scheduler usa el UUID fijo ...0001 y la importación ...0002). Mientras
--     no se haga backfill, las policies `owner_id = auth.uid()` NO dejan ver
--     esas filas a ningún usuario autenticado. Esto NO afecta a la app actual
--     porque entra con service_role (bypass). El backfill de ownership es FASE 2.
--  3. `auth.uid()` es la función de Supabase Auth. Si la app todavía no usa
--     Supabase Auth, las policies quedan dormidas: no hay rol `authenticated`
--     emitiendo JWT contra `crm`.
--  4. Tablas hijas sin owner propio derivan la propiedad de su padre mediante
--     EXISTS (...) sobre el `owner_id` del padre. No hay ciclos de policies.
--
-- ----------------------------------------------------------------------------
-- CÓMO EJECUTAR (revisar primero, luego aplicar)
-- ----------------------------------------------------------------------------
--   Opción A (Supabase CLI, recomendado):
--       supabase db push            -- aplica migraciones pendientes
--   Opción B (SQL editor de Supabase): pegar el contenido íntegro.
--   Reverso: backend/supabase/rollbacks/20260912_000002_rls_y_auth_fase1.down.sql
--
--   NO se debe ejecutar `ALTER TABLE ... FORCE ROW LEVEL SECURITY`: forzaría
--   RLS sobre el dueño de la tabla (rol `postgres`), rompiendo la conexión
--   directa/SQLAlchemy y las migraciones.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 0) Esquema y guardas de idempotencia (roles)
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS crm;

-- Rol de solo lectura para BI/Appsmith. SIN contraseña en el repositorio:
-- la credencial se fija fuera de banda (ver plan de aplicación):
--     ALTER ROLE crm_bi_readonly WITH PASSWORD '<secreto gestionado>';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_bi_readonly') THEN
        CREATE ROLE crm_bi_readonly LOGIN;
    END IF;
END
$$;

-- Roles que en Supabase gestionado ya existen; se crean solo si faltan para
-- que la migración sea verificable también en un Postgres local de dry-run.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_veyra') THEN
        CREATE ROLE ops_veyra NOLOGIN;
    END IF;
END
$$;

-- ----------------------------------------------------------------------------
-- 1) Habilitar RLS en las 31 tablas de `crm`
--    (idempotente: ENABLE es no-op si ya estaba activo)
-- ----------------------------------------------------------------------------
ALTER TABLE IF EXISTS crm.activities            ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.app_settings          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.audit_log             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.call_logs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.call_scripts          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.companies             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_signals       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_socials       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.company_sources       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.contacts              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.conversations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_accounts        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_links           ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_messages        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.email_templates       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.follow_ups            ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.idempotency_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.jobs                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.lead_stage_history    ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.leads                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.pipeline_stages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.search_results        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.search_runs           ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.searches              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.sequence_steps        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.sequences             ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.services              ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.suppression_list      ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS crm.tasks                 ENABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------------------------
-- 2) Policies de ownership para las 19 tablas CON `owner_id`
--    Plantilla por tabla: SELECT / INSERT / UPDATE / DELETE para `authenticated`,
--    siempre atadas a `owner_id = auth.uid()`.
--    `anon` queda deliberadamente SIN policy (acceso denegado).
--    Se recrean para ser idempotentes (DROP ... IF EXISTS + CREATE).
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
    t text;
BEGIN
    FOREACH t IN ARRAY v_owner_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_owner_select', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR SELECT TO authenticated '
                       'USING (owner_id = auth.uid())',
                       'p_' || t || '_owner_select', t);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_owner_insert', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR INSERT TO authenticated '
                       'WITH CHECK (owner_id = auth.uid())',
                       'p_' || t || '_owner_insert', t);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_owner_update', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR UPDATE TO authenticated '
                       'USING (owner_id = auth.uid()) '
                       'WITH CHECK (owner_id = auth.uid())',
                       'p_' || t || '_owner_update', t);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_owner_delete', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR DELETE TO authenticated '
                       'USING (owner_id = auth.uid())',
                       'p_' || t || '_owner_delete', t);
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- 3a) Policies para tablas hijas SIN `owner_id` (ownership derivado del padre)
--     La propiedad se resuelve con EXISTS sobre el `owner_id` del padre.
--     Nota: el sub-SELECT entra en las policies del padre, que ya están
--     definidas arriba; no hay recursión porque no existen ciclos padre-hijo.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    v_child_tables text[] := ARRAY[
        'company_signals', 'company_socials', 'company_sources',
        'email_links', 'sequence_steps', 'search_runs'
    ];
    v_parents text[] := ARRAY[
        'companies', 'companies', 'companies',
        'email_messages', 'sequences', 'searches'
    ];
    v_fk_cols text[] := ARRAY[
        'company_id', 'company_id', 'company_id',
        'email_message_id', 'sequence_id', 'search_id'
    ];
    i int;
    t text;
    p text;
    fk text;
    expr text;
BEGIN
    FOR i IN 1..array_length(v_child_tables, 1) LOOP
        t  := v_child_tables[i];
        p  := v_parents[i];
        fk := v_fk_cols[i];

        -- EXISTS (... crm.<hija>.<fk> ...) correlaciona con la fila evaluada.
        expr := format(
            'EXISTS (SELECT 1 FROM crm.%I p WHERE p.id = crm.%I.%I '
            'AND p.owner_id = auth.uid())', p, t, fk);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_parent_select', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR SELECT TO authenticated '
                       'USING (%s)', 'p_' || t || '_parent_select', t, expr);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_parent_insert', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR INSERT TO authenticated '
                       'WITH CHECK (%s)', 'p_' || t || '_parent_insert', t, expr);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_parent_update', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR UPDATE TO authenticated '
                       'USING (%s) WITH CHECK (%s)',
                       'p_' || t || '_parent_update', t, expr, expr);

        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_parent_delete', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR DELETE TO authenticated '
                       'USING (%s)', 'p_' || t || '_parent_delete', t, expr);
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- 3b) Caso transitivo: `search_results` NO tiene owner y su padre directo
--     (`search_runs`) tampoco. La propiedad se resuelve en dos saltos:
--     search_results -> search_runs -> searches.owner_id.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    t text := 'search_results';
    expr text :=
        'EXISTS (SELECT 1 FROM crm.search_runs p '
        'JOIN crm.searches s ON s.id = p.search_id '
        'WHERE p.id = crm.search_results.search_run_id '
        'AND s.owner_id = auth.uid())';
BEGIN
    EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                   'p_' || t || '_parent_select', t);
    EXECUTE format('CREATE POLICY %I ON crm.%I FOR SELECT TO authenticated '
                   'USING (%s)', 'p_' || t || '_parent_select', t, expr);

    EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                   'p_' || t || '_parent_insert', t);
    EXECUTE format('CREATE POLICY %I ON crm.%I FOR INSERT TO authenticated '
                   'WITH CHECK (%s)', 'p_' || t || '_parent_insert', t, expr);

    EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                   'p_' || t || '_parent_update', t);
    EXECUTE format('CREATE POLICY %I ON crm.%I FOR UPDATE TO authenticated '
                   'USING (%s) WITH CHECK (%s)',
                   'p_' || t || '_parent_update', t, expr, expr);

    EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                   'p_' || t || '_parent_delete', t);
    EXECUTE format('CREATE POLICY %I ON crm.%I FOR DELETE TO authenticated '
                   'USING (%s)', 'p_' || t || '_parent_delete', t, expr);
END
$$;

-- ----------------------------------------------------------------------------
-- 4) Grants de tabla para `authenticated` en las 26 tablas cubiertas
--    (19 con owner + 7 hijas). RLS restringe las filas; el GRANT habilita el
--    comando. Las 5 tablas de excepción NO reciben grants (ver sección 6).
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA crm TO authenticated;

DO $$
DECLARE
    v_covered text[] := ARRAY[
        -- con owner_id
        'activities', 'call_logs', 'call_scripts', 'companies', 'contacts',
        'conversation_messages', 'conversations', 'email_accounts',
        'email_messages', 'email_templates', 'follow_ups', 'jobs', 'leads',
        'pipeline_stages', 'searches', 'sequences', 'services',
        'suppression_list', 'tasks',
        -- hijas con ownership derivado
        'company_signals', 'company_socials', 'company_sources',
        'email_links', 'sequence_steps', 'search_runs', 'search_results'
    ];
    t text;
BEGIN
    FOREACH t IN ARRAY v_covered LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON crm.%I TO authenticated', t);
    END LOOP;
END
$$;

-- ----------------------------------------------------------------------------
-- 5) Rol de solo lectura para BI/Appsmith (`crm_bi_readonly`)
--    Grants mínimos: USAGE de esquema + SELECT. Sin INSERT/UPDATE/DELETE,
--    sin BYPASSRLS, sin DDL.
--    Se EXCLUYEN tablas con material sensible/cifrado (`app_settings`,
--    `email_accounts`). Si Appsmith necesita alguna, añadirla explícitamente.
--    Al tener RLS activo, se requiere además una policy de lectura para el rol.
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA crm TO crm_bi_readonly;

DO $$
DECLARE
    v_bi_tables text[] := ARRAY[
        'activities', 'audit_log', 'call_logs', 'call_scripts', 'companies',
        'company_signals', 'company_socials', 'company_sources', 'contacts',
        'conversation_messages', 'conversations', 'email_events', 'email_links',
        'email_messages', 'email_templates', 'follow_ups', 'idempotency_events',
        'jobs', 'lead_stage_history', 'leads', 'pipeline_stages',
        'search_results', 'search_runs', 'searches', 'sequence_steps',
        'sequences', 'services', 'suppression_list', 'tasks'
    ];
    t text;
BEGIN
    FOREACH t IN ARRAY v_bi_tables LOOP
        EXECUTE format('GRANT SELECT ON crm.%I TO crm_bi_readonly', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_bi_select', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR SELECT TO crm_bi_readonly '
                       'USING (true)', 'p_' || t || '_bi_select', t);
    END LOOP;
END
$$;

-- Nuevas tablas futuras del esquema: el rol BI hereda SELECT automáticamente.
ALTER DEFAULT PRIVILEGES IN SCHEMA crm
    GRANT SELECT ON TABLES TO crm_bi_readonly;

-- ----------------------------------------------------------------------------
-- 6) EXCEPCIONES: tablas SIN ownership y SIN policy (acceso solo por
--    dueño de tabla / service_role / roles de servicio). Se documentan en vez
--    de exponerlas a `authenticated`:
--
--      app_settings        Config global, incluye secretos cifrados
--                          (ai_api_key_enc, google_places_key_enc, ...). NO exponer.
--      audit_log           Log inmutable de auditoría (infra).
--      idempotency_events  Idempotencia de la capa de servicio (infra).
--      email_events        Log inmutable de tracking; lo escribe el sistema.
--      lead_stage_history  Historial inmutable de cambios de etapa.
--
--    Estas tablas tienen RLS activo y CERO policies => deniegan a anon/authenticated.
--    `crm_bi_readonly` sí las lee (excepto `app_settings`), por la sección 5.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 7) Compatibilidad transitoria con el rol de automatización `ops_veyra`
--    `ops_veyra` NO es dueño de las tablas ni tiene BYPASSRLS: al activar RLS
--    sus scripts (extracción VEYRA, warm-up, etc.) quedarían sin acceso.
--    Se le conceden policies de acceso total para NO romper la operación.
--    PENDIENTE FASE 2: migrar esos scripts a service_role y ELIMINAR este bloque.
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA crm TO ops_veyra;

DO $$
DECLARE
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
BEGIN
    FOREACH t IN ARRAY v_all_tables LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON crm.%I TO ops_veyra', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON crm.%I',
                       'p_' || t || '_trusted_all', t);
        EXECUTE format('CREATE POLICY %I ON crm.%I FOR ALL TO ops_veyra '
                       'USING (true) WITH CHECK (true)',
                       'p_' || t || '_trusted_all', t);
    END LOOP;
END
$$;

-- ============================================================================
-- FIN FASE 1. Ver reverso en:
-- backend/supabase/rollbacks/20260912_000002_rls_y_auth_fase1.down.sql
-- ============================================================================
