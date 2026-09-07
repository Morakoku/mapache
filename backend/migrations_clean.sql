
CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 46e017724c24

CREATE TYPE job_type AS ENUM ('DISCOVERY', 'ENRICHMENT', 'SCORING', 'SEND_BATCH', 'INBOX_SYNC', 'FOLLOWUP_TICK', 'SCRAPER_HEALTH');

CREATE TYPE job_status AS ENUM ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');

CREATE TABLE jobs (
    job_type job_type NOT NULL, 
    status job_status DEFAULT 'QUEUED' NOT NULL, 
    payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
    result JSONB, 
    progress_current INTEGER DEFAULT '0' NOT NULL, 
    progress_total INTEGER, 
    progress_message VARCHAR(255), 
    error_message VARCHAR, 
    attempts SMALLINT DEFAULT '0' NOT NULL, 
    max_attempts SMALLINT DEFAULT '3' NOT NULL, 
    scheduled_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_jobs PRIMARY KEY (id)
);

CREATE INDEX ix_jobs_owner_id ON jobs (owner_id);

CREATE INDEX ix_jobs_pending ON jobs (status, scheduled_at) WHERE status IN ('QUEUED', 'RUNNING');

CREATE INDEX ix_jobs_type_created ON jobs (job_type, created_at);

INSERT INTO alembic_version (version_num) VALUES ('46e017724c24') RETURNING alembic_version.version_num;

-- Running upgrade 46e017724c24 -> 1dbee674b5e4

CREATE TYPE source_type AS ENUM ('GOOGLE_MAPS', 'GOOGLE_PLACES_API', 'APIFY', 'WEBSITE', 'LINKEDIN', 'INSTAGRAM', 'FACEBOOK', 'MANUAL', 'AI', 'IMPORT');

CREATE TYPE verification_status AS ENUM ('UNVERIFIED', 'SYNTAX_OK', 'MX_OK', 'VERIFIED', 'RISKY', 'INVALID', 'BOUNCED');

CREATE TABLE companies (
    name VARCHAR(255) NOT NULL, 
    legal_name VARCHAR(255), 
    description TEXT, 
    category VARCHAR(160), 
    categories TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    address TEXT, 
    city VARCHAR(120), 
    state VARCHAR(120), 
    country VARCHAR(120), 
    postal_code VARCHAR(20), 
    phone VARCHAR(20), 
    phone_raw VARCHAR(60), 
    email CITEXT, 
    website TEXT, 
    website_domain VARCHAR(255), 
    google_maps_url TEXT, 
    google_ftid VARCHAR(120), 
    google_place_id VARCHAR(255), 
    latitude FLOAT, 
    longitude FLOAT, 
    rating NUMERIC(2, 1), 
    reviews_count INTEGER, 
    price_level SMALLINT, 
    opening_hours JSONB, 
    is_permanently_closed BOOLEAN DEFAULT false NOT NULL, 
    employee_range VARCHAR(40), 
    data_quality_score SMALLINT DEFAULT '0' NOT NULL, 
    dedupe_key VARCHAR(40) NOT NULL, 
    first_extracted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_extracted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_enriched_at TIMESTAMP WITH TIME ZONE, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_companies PRIMARY KEY (id), 
    CONSTRAINT uq_companies_owner_dedupe_key UNIQUE (owner_id, dedupe_key)
);

CREATE INDEX ix_companies_city_category ON companies (city, category);

CREATE INDEX ix_companies_name_trgm ON companies USING gin (name gin_trgm_ops);

CREATE INDEX ix_companies_owner_id ON companies (owner_id);

CREATE INDEX ix_companies_website_domain ON companies (website_domain) WHERE website_domain IS NOT NULL;

CREATE UNIQUE INDEX uq_companies_google_ftid ON companies (google_ftid) WHERE google_ftid IS NOT NULL;

CREATE UNIQUE INDEX uq_companies_google_place_id ON companies (google_place_id) WHERE google_place_id IS NOT NULL;

CREATE TABLE services (
    name VARCHAR(160) NOT NULL, 
    description TEXT, 
    ideal_customer VARCHAR(255), 
    target_industries TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    problems_solved TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    opportunity_signals TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    value_proposition TEXT, 
    price_from NUMERIC(12, 2), 
    currency VARCHAR(3) DEFAULT 'COP' NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_services PRIMARY KEY (id)
);

CREATE INDEX ix_services_owner_id ON services (owner_id);

CREATE UNIQUE INDEX uq_services_owner_name ON services (owner_id, lower(name));

CREATE TABLE company_signals (
    company_id UUID NOT NULL, 
    signal_key VARCHAR(60) NOT NULL, 
    value JSONB, 
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    source source_type NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_company_signals PRIMARY KEY (id), 
    CONSTRAINT fk_company_signals_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_company_signals_company_key UNIQUE (company_id, signal_key)
);

CREATE INDEX ix_company_signals_key ON company_signals (signal_key);

CREATE TABLE company_socials (
    company_id UUID NOT NULL, 
    platform VARCHAR(40) NOT NULL, 
    url TEXT NOT NULL, 
    handle VARCHAR(120), 
    followers_count INTEGER, 
    last_post_at TIMESTAMP WITH TIME ZONE, 
    source source_type NOT NULL, 
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_company_socials PRIMARY KEY (id), 
    CONSTRAINT fk_company_socials_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_company_socials_company_url UNIQUE (company_id, platform, url)
);

CREATE TABLE company_sources (
    company_id UUID NOT NULL, 
    field_name VARCHAR(60) NOT NULL, 
    value TEXT NOT NULL, 
    source source_type NOT NULL, 
    source_url TEXT, 
    confidence SMALLINT DEFAULT '50' NOT NULL, 
    verification verification_status DEFAULT 'UNVERIFIED' NOT NULL, 
    is_primary BOOLEAN DEFAULT false NOT NULL, 
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_company_sources PRIMARY KEY (id), 
    CONSTRAINT fk_company_sources_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT uq_company_sources_company_field_value UNIQUE (company_id, field_name, value)
);

CREATE INDEX ix_company_sources_company_field ON company_sources (company_id, field_name);

CREATE TABLE searches (
    service_id UUID, 
    name VARCHAR(160) NOT NULL, 
    business_type VARCHAR(160) NOT NULL, 
    keywords TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    country VARCHAR(120), 
    region VARCHAR(120), 
    city VARCHAR(120) NOT NULL, 
    zone VARCHAR(160), 
    latitude FLOAT, 
    longitude FLOAT, 
    radius_km NUMERIC(6, 2) DEFAULT '10' NOT NULL, 
    target_count INTEGER DEFAULT '100' NOT NULL, 
    source source_type DEFAULT 'GOOGLE_MAPS' NOT NULL, 
    min_rating NUMERIC(2, 1), 
    max_reviews INTEGER, 
    exclude_chains BOOLEAN DEFAULT false NOT NULL, 
    auto_enrich BOOLEAN DEFAULT true NOT NULL, 
    auto_score BOOLEAN DEFAULT true NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_searches PRIMARY KEY (id), 
    CONSTRAINT fk_searches_service_id_services FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE SET NULL
);

CREATE INDEX ix_searches_owner_id ON searches (owner_id);

CREATE TABLE search_runs (
    search_id UUID NOT NULL, 
    job_id UUID, 
    status job_status DEFAULT 'QUEUED' NOT NULL, 
    provider source_type NOT NULL, 
    results_found INTEGER DEFAULT '0' NOT NULL, 
    results_new INTEGER DEFAULT '0' NOT NULL, 
    results_duplicate INTEGER DEFAULT '0' NOT NULL, 
    error_message TEXT, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_search_runs PRIMARY KEY (id), 
    CONSTRAINT fk_search_runs_job_id_jobs FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE SET NULL, 
    CONSTRAINT fk_search_runs_search_id_searches FOREIGN KEY(search_id) REFERENCES searches (id) ON DELETE CASCADE
);

CREATE INDEX ix_search_runs_search_created ON search_runs (search_id, created_at);

CREATE TABLE search_results (
    search_run_id UUID NOT NULL, 
    company_id UUID NOT NULL, 
    is_new BOOLEAN NOT NULL, 
    position SMALLINT, 
    raw_payload JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_search_results PRIMARY KEY (search_run_id, company_id), 
    CONSTRAINT fk_search_results_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_search_results_search_run_id_search_runs FOREIGN KEY(search_run_id) REFERENCES search_runs (id) ON DELETE CASCADE
);

UPDATE alembic_version SET version_num='1dbee674b5e4' WHERE alembic_version.version_num = '46e017724c24';

-- Running upgrade 1dbee674b5e4 -> 99360f8ac979

CREATE TYPE stage_type AS ENUM ('NEW', 'QUALIFIED', 'CONTACT_FOUND', 'CONTACTED', 'OPENED', 'REPLIED', 'CONVERSATION', 'INTERESTED', 'MEETING', 'OPPORTUNITY', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST');

CREATE TYPE lead_status AS ENUM ('OPEN', 'WON', 'LOST', 'DISQUALIFIED', 'PAUSED');

CREATE TYPE reply_intent AS ENUM ('POSITIVE', 'NEUTRAL', 'NEGATIVE', 'QUESTION', 'PRICING', 'MEETING_REQUEST', 'OUT_OF_OFFICE', 'UNSUBSCRIBE', 'WRONG_PERSON', 'UNKNOWN');

CREATE TYPE actor_type AS ENUM ('USER', 'SYSTEM', 'AI', 'PROSPECT');

CREATE TYPE activity_type AS ENUM ('COMPANY_FOUND', 'EMAIL_FOUND', 'LEAD_CREATED', 'LEAD_QUALIFIED', 'STAGE_CHANGED', 'EMAIL_SENT', 'EMAIL_DELIVERED', 'EMAIL_OPENED', 'EMAIL_CLICKED', 'EMAIL_REPLIED', 'EMAIL_BOUNCED', 'CONVERSATION_STARTED', 'FOLLOWUP_SCHEDULED', 'FOLLOWUP_SENT', 'MEETING_SCHEDULED', 'PROPOSAL_SENT', 'NOTE', 'TASK_CREATED', 'TASK_COMPLETED', 'AI_PERSONALIZED', 'AI_CLASSIFIED', 'WON', 'LOST');

CREATE TABLE pipeline_stages (
    name VARCHAR(80) NOT NULL, 
    stage_key VARCHAR(60) NOT NULL, 
    stage_type stage_type NOT NULL, 
    position INTEGER NOT NULL, 
    color VARCHAR(9) DEFAULT '#64748b' NOT NULL, 
    is_default BOOLEAN DEFAULT false NOT NULL, 
    is_won BOOLEAN DEFAULT false NOT NULL, 
    is_lost BOOLEAN DEFAULT false NOT NULL, 
    is_system BOOLEAN DEFAULT false NOT NULL, 
    auto_advance_on TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_pipeline_stages PRIMARY KEY (id), 
    CONSTRAINT uq_pipeline_stages_owner_key UNIQUE (owner_id, stage_key)
);

CREATE INDEX ix_pipeline_stages_owner_id ON pipeline_stages (owner_id);

CREATE INDEX ix_pipeline_stages_position ON pipeline_stages (owner_id, position);

CREATE TABLE contacts (
    company_id UUID NOT NULL, 
    first_name VARCHAR(120), 
    last_name VARCHAR(120), 
    full_name VARCHAR(255), 
    job_title VARCHAR(160), 
    seniority VARCHAR(20), 
    email CITEXT, 
    email_verified verification_status DEFAULT 'UNVERIFIED' NOT NULL, 
    is_role_email BOOLEAN DEFAULT false NOT NULL, 
    phone VARCHAR(20), 
    whatsapp VARCHAR(20), 
    linkedin_url TEXT, 
    source source_type DEFAULT 'WEBSITE' NOT NULL, 
    is_primary BOOLEAN DEFAULT false NOT NULL, 
    do_not_contact BOOLEAN DEFAULT false NOT NULL, 
    notes TEXT, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_contacts PRIMARY KEY (id), 
    CONSTRAINT fk_contacts_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_contacts_company ON contacts (company_id);

CREATE INDEX ix_contacts_email ON contacts (lower(email));

CREATE INDEX ix_contacts_owner_id ON contacts (owner_id);

CREATE UNIQUE INDEX uq_contacts_company_email ON contacts (company_id, lower(email)) WHERE email IS NOT NULL;

CREATE TABLE leads (
    company_id UUID NOT NULL, 
    service_id UUID NOT NULL, 
    contact_id UUID, 
    stage_id UUID NOT NULL, 
    status lead_status DEFAULT 'OPEN' NOT NULL, 
    score SMALLINT DEFAULT '0' NOT NULL, 
    score_breakdown JSONB, 
    score_computed_at TIMESTAMP WITH TIME ZONE, 
    engagement_score SMALLINT DEFAULT '0' NOT NULL, 
    reply_intent reply_intent, 
    estimated_value NUMERIC(12, 2), 
    currency VARCHAR(3) DEFAULT 'COP' NOT NULL, 
    first_contact_at TIMESTAMP WITH TIME ZONE, 
    last_contact_at TIMESTAMP WITH TIME ZONE, 
    last_activity_at TIMESTAMP WITH TIME ZONE, 
    next_follow_up_at TIMESTAMP WITH TIME ZONE, 
    replied_at TIMESTAMP WITH TIME ZONE, 
    won_at TIMESTAMP WITH TIME ZONE, 
    lost_at TIMESTAMP WITH TIME ZONE, 
    lost_reason TEXT, 
    sequence_id UUID, 
    sequence_step SMALLINT DEFAULT '0' NOT NULL, 
    sequence_paused BOOLEAN DEFAULT false NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_leads PRIMARY KEY (id), 
    CONSTRAINT fk_leads_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_leads_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_leads_service_id_services FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_leads_stage_id_pipeline_stages FOREIGN KEY(stage_id) REFERENCES pipeline_stages (id), 
    CONSTRAINT uq_leads_company_service UNIQUE (company_id, service_id)
);

CREATE INDEX ix_leads_next_followup ON leads (next_follow_up_at) WHERE next_follow_up_at IS NOT NULL AND status = 'OPEN';

CREATE INDEX ix_leads_owner_id ON leads (owner_id);

CREATE INDEX ix_leads_owner_status_activity ON leads (owner_id, status, last_activity_at);

CREATE INDEX ix_leads_stage_score ON leads (stage_id, score);

CREATE TABLE activities (
    id BIGSERIAL NOT NULL, 
    owner_id UUID, 
    lead_id UUID, 
    company_id UUID, 
    contact_id UUID, 
    activity_type activity_type NOT NULL, 
    actor actor_type DEFAULT 'SYSTEM' NOT NULL, 
    title VARCHAR(255) NOT NULL, 
    description TEXT, 
    metadata JSONB, 
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_activities PRIMARY KEY (id), 
    CONSTRAINT fk_activities_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_activities_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_activities_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE
);

CREATE INDEX ix_activities_company ON activities (company_id, occurred_at);

CREATE INDEX ix_activities_lead ON activities (lead_id, occurred_at);

CREATE INDEX ix_activities_type ON activities (activity_type, occurred_at);

CREATE TABLE lead_stage_history (
    lead_id UUID NOT NULL, 
    from_stage_id UUID, 
    to_stage_id UUID, 
    from_stage_type stage_type, 
    to_stage_type stage_type NOT NULL, 
    actor actor_type DEFAULT 'USER' NOT NULL, 
    reason TEXT, 
    entered_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    duration_seconds INTEGER, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_lead_stage_history PRIMARY KEY (id), 
    CONSTRAINT fk_lead_stage_history_from_stage_id_pipeline_stages FOREIGN KEY(from_stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL, 
    CONSTRAINT fk_lead_stage_history_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, 
    CONSTRAINT fk_lead_stage_history_to_stage_id_pipeline_stages FOREIGN KEY(to_stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL
);

CREATE INDEX ix_lead_stage_history_lead ON lead_stage_history (lead_id, entered_at);

CREATE INDEX ix_lead_stage_history_type ON lead_stage_history (to_stage_type, entered_at);

CREATE TABLE tasks (
    lead_id UUID, 
    company_id UUID, 
    title VARCHAR(255) NOT NULL, 
    description TEXT, 
    due_at TIMESTAMP WITH TIME ZONE, 
    priority SMALLINT DEFAULT '2' NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_tasks PRIMARY KEY (id), 
    CONSTRAINT fk_tasks_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE, 
    CONSTRAINT fk_tasks_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE
);

CREATE INDEX ix_tasks_lead ON tasks (lead_id);

CREATE INDEX ix_tasks_owner_id ON tasks (owner_id);

CREATE INDEX ix_tasks_pending ON tasks (due_at) WHERE completed_at IS NULL;

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('cf7645a3-8e66-46af-80f9-4eada66a399a', 'Prospecto', 'prospect', 'NEW', 1, '#94a3b8', true, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('c1db7c46-3ff8-48d0-abc9-8943411355ab', 'Calificado', 'qualified', 'QUALIFIED', 2, '#64748b', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('026e8f21-eaba-4165-b17e-f68c0f2baba9', 'Contacto encontrado', 'contact_found', 'CONTACT_FOUND', 3, '#0ea5e9', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('075afca3-94c5-491b-b970-7e24fe794061', 'Primer contacto', 'first_contact', 'CONTACTED', 4, '#3b82f6', false, false, false, true, ARRAY['EMAIL_SENT']);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('0f592eb2-af5f-481d-b17e-5449bf5f753b', 'Email abierto', 'opened', 'OPENED', 5, '#6366f1', false, false, false, true, ARRAY['EMAIL_OPENED']);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('637b5d63-48a4-4bd4-bf4f-2eaf378bd192', 'Respondió', 'replied', 'REPLIED', 6, '#8b5cf6', false, false, false, true, ARRAY['EMAIL_REPLIED']);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('9aba2a14-e5f9-4fe0-924a-e2f53a0a936d', 'Conversación', 'conversation', 'CONVERSATION', 7, '#a855f7', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('c8702131-14d0-4e4c-9ced-9edd6da6df0f', 'Interesado', 'interested', 'INTERESTED', 8, '#d946ef', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('e9ee071a-40bb-4766-934b-e2b5e1809524', 'Reunión', 'meeting', 'MEETING', 9, '#ec4899', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('45ce592d-0a32-4f6a-9298-9272207c9b30', 'Oportunidad', 'opportunity', 'OPPORTUNITY', 10, '#f59e0b', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('956c8bb0-f51a-4160-9e43-945f926f323e', 'Propuesta', 'proposal', 'PROPOSAL', 11, '#f97316', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('23c1a01e-b7bf-4429-b43c-8bfad51f0a85', 'Negociación', 'negotiation', 'NEGOTIATION', 12, '#ef4444', false, false, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('97afbabc-d267-4c98-8899-eae47486a7d8', 'Ganado', 'won', 'WON', 13, '#22c55e', false, true, false, true, ARRAY[]);

INSERT INTO pipeline_stages (id, name, stage_key, stage_type, position, color, is_default, is_won, is_lost, is_system, auto_advance_on) VALUES ('a9cf6936-3173-4acc-97db-7b94badfb0bb', 'Perdido', 'lost', 'LOST', 14, '#71717a', false, false, true, true, ARRAY[]);

UPDATE alembic_version SET version_num='99360f8ac979' WHERE alembic_version.version_num = '1dbee674b5e4';

-- Running upgrade 99360f8ac979 -> 2df69f93ac48

CREATE TYPE channel AS ENUM ('EMAIL', 'WHATSAPP', 'LINKEDIN', 'PHONE', 'MANUAL');

CREATE TYPE direction AS ENUM ('OUTBOUND', 'INBOUND');

CREATE TYPE mail_provider AS ENUM ('GMAIL', 'MICROSOFT', 'SMTP');

CREATE TYPE account_status AS ENUM ('ACTIVE', 'TOKEN_EXPIRED', 'REVOKED', 'ERROR', 'DISABLED');

CREATE TYPE email_status AS ENUM ('DRAFT', 'QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'BOUNCED', 'FAILED', 'CANCELLED');

CREATE TYPE email_event_type AS ENUM ('SENT', 'DELIVERED', 'OPENED', 'CLICKED', 'REPLIED', 'BOUNCED', 'COMPLAINED', 'UNSUBSCRIBED', 'FAILED');

CREATE TABLE email_accounts (
    provider mail_provider NOT NULL, 
    email CITEXT NOT NULL, 
    display_name VARCHAR(120), 
    status account_status DEFAULT 'ACTIVE' NOT NULL, 
    oauth_access_token_enc TEXT, 
    oauth_refresh_token_enc TEXT, 
    oauth_expires_at TIMESTAMP WITH TIME ZONE, 
    oauth_scopes TEXT[], 
    external_account_id VARCHAR(255), 
    smtp_host VARCHAR(255), 
    smtp_port INTEGER, 
    smtp_user VARCHAR(255), 
    smtp_password_enc TEXT, 
    smtp_use_tls BOOLEAN DEFAULT true NOT NULL, 
    imap_host VARCHAR(255), 
    imap_port INTEGER, 
    imap_user VARCHAR(255), 
    imap_password_enc TEXT, 
    sync_cursor TEXT, 
    last_synced_at TIMESTAMP WITH TIME ZONE, 
    watch_expires_at TIMESTAMP WITH TIME ZONE, 
    sync_error TEXT, 
    sent_today INTEGER DEFAULT '0' NOT NULL, 
    sent_this_hour INTEGER DEFAULT '0' NOT NULL, 
    counters_reset_at TIMESTAMP WITH TIME ZONE, 
    last_sent_at TIMESTAMP WITH TIME ZONE, 
    is_default BOOLEAN DEFAULT false NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_email_accounts PRIMARY KEY (id)
);

CREATE INDEX ix_email_accounts_owner_id ON email_accounts (owner_id);

CREATE INDEX ix_email_accounts_unhealthy ON email_accounts (status) WHERE status <> 'ACTIVE';

CREATE INDEX ix_email_accounts_watch_expiry ON email_accounts (watch_expires_at) WHERE watch_expires_at IS NOT NULL;

CREATE UNIQUE INDEX uq_email_accounts_owner_email ON email_accounts (owner_id, lower(email));

CREATE TABLE app_settings (
    id SMALLINT DEFAULT '1' NOT NULL, 
    sender_name VARCHAR(120) DEFAULT '' NOT NULL, 
    default_account_id UUID, 
    reply_to CITEXT, 
    tracking_domain VARCHAR(255), 
    address_of_sender TEXT, 
    country_code VARCHAR(2) DEFAULT 'CO' NOT NULL, 
    phone_region VARCHAR(2) DEFAULT 'CO' NOT NULL, 
    currency VARCHAR(3) DEFAULT 'COP' NOT NULL, 
    locale VARCHAR(10) DEFAULT 'es-CO' NOT NULL, 
    timezone VARCHAR(60) DEFAULT 'America/Bogota' NOT NULL, 
    daily_send_limit INTEGER DEFAULT '100' NOT NULL, 
    hourly_send_limit INTEGER DEFAULT '20' NOT NULL, 
    min_seconds_between INTEGER DEFAULT '45' NOT NULL, 
    send_window_start TIME WITHOUT TIME ZONE DEFAULT '08:00' NOT NULL, 
    send_window_end TIME WITHOUT TIME ZONE DEFAULT '18:00' NOT NULL, 
    skip_weekends BOOLEAN DEFAULT true NOT NULL, 
    warmup_enabled BOOLEAN DEFAULT true NOT NULL, 
    warmup_started_on DATE, 
    ai_enabled BOOLEAN DEFAULT true NOT NULL, 
    ai_model VARCHAR(60) DEFAULT 'claude-opus-5' NOT NULL, 
    ai_tone VARCHAR(20) DEFAULT 'usted' NOT NULL, 
    discovery_provider VARCHAR(40) DEFAULT 'google_maps_scraper' NOT NULL, 
    fallback_discovery_provider VARCHAR(40), 
    scraper_concurrency SMALLINT DEFAULT '2' NOT NULL, 
    scraper_delay_ms_min INTEGER DEFAULT '1200' NOT NULL, 
    scraper_delay_ms_max INTEGER DEFAULT '3500' NOT NULL, 
    scraper_headless BOOLEAN DEFAULT true NOT NULL, 
    google_places_key_enc TEXT, 
    apify_token_enc TEXT, 
    score_weights JSONB DEFAULT '{"fit": 0.3, "opportunity": 0.25, "contactability": 0.2, "data_quality": 0.1, "intent": 0.1, "timing": 0.05}' NOT NULL, 
    automations_paused BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_app_settings PRIMARY KEY (id), 
    CONSTRAINT ck_app_settings_single_row CHECK (id = 1), 
    CONSTRAINT fk_app_settings_default_account_id_email_accounts FOREIGN KEY(default_account_id) REFERENCES email_accounts (id) ON DELETE SET NULL
);

CREATE TABLE email_templates (
    name VARCHAR(160) NOT NULL, 
    category VARCHAR(40) NOT NULL, 
    service_id UUID, 
    subject TEXT NOT NULL, 
    body_text TEXT NOT NULL, 
    body_html TEXT, 
    variables_used TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    times_used INTEGER DEFAULT '0' NOT NULL, 
    open_rate NUMERIC(5, 2), 
    reply_rate NUMERIC(5, 2), 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_email_templates PRIMARY KEY (id), 
    CONSTRAINT fk_email_templates_service_id_services FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE SET NULL
);

CREATE INDEX ix_email_templates_category ON email_templates (category);

CREATE INDEX ix_email_templates_owner_id ON email_templates (owner_id);

CREATE UNIQUE INDEX uq_email_templates_owner_name ON email_templates (owner_id, lower(name));

CREATE TABLE conversations (
    lead_id UUID NOT NULL, 
    contact_id UUID, 
    channel channel DEFAULT 'EMAIL' NOT NULL, 
    subject TEXT, 
    thread_key VARCHAR(255) NOT NULL, 
    status VARCHAR(20) DEFAULT 'OPEN' NOT NULL, 
    is_unread BOOLEAN DEFAULT false NOT NULL, 
    message_count INTEGER DEFAULT '0' NOT NULL, 
    last_message_at TIMESTAMP WITH TIME ZONE, 
    last_direction direction, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_conversations PRIMARY KEY (id), 
    CONSTRAINT fk_conversations_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_conversations_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, 
    CONSTRAINT uq_conversations_lead_channel_thread UNIQUE (lead_id, channel, thread_key)
);

CREATE INDEX ix_conversations_owner_id ON conversations (owner_id);

CREATE INDEX ix_conversations_status ON conversations (owner_id, status, last_message_at);

CREATE INDEX ix_conversations_unread ON conversations (owner_id, channel) WHERE is_unread;

CREATE TABLE conversation_messages (
    conversation_id UUID NOT NULL, 
    channel channel NOT NULL, 
    direction direction NOT NULL, 
    author_name VARCHAR(160), 
    body_text TEXT NOT NULL, 
    body_html TEXT, 
    snippet VARCHAR(255), 
    attachments JSONB, 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    is_automated BOOLEAN DEFAULT false NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_conversation_messages PRIMARY KEY (id), 
    CONSTRAINT fk_conversation_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversation_messages_owner_id ON conversation_messages (owner_id);

CREATE INDEX ix_conversation_messages_thread ON conversation_messages (conversation_id, occurred_at);

CREATE TABLE email_messages (
    conversation_message_id UUID, 
    conversation_id UUID, 
    lead_id UUID, 
    contact_id UUID, 
    template_id UUID, 
    email_account_id UUID, 
    direction direction NOT NULL, 
    from_email CITEXT NOT NULL, 
    to_email CITEXT NOT NULL, 
    cc TEXT[], 
    subject TEXT NOT NULL, 
    status email_status DEFAULT 'DRAFT' NOT NULL, 
    provider mail_provider, 
    provider_message_id VARCHAR(255), 
    provider_thread_id VARCHAR(255), 
    in_reply_to VARCHAR(255), 
    references_header TEXT, 
    tracking_token UUID DEFAULT gen_random_uuid() NOT NULL, 
    unsubscribe_token UUID DEFAULT gen_random_uuid() NOT NULL, 
    tracking_enabled BOOLEAN DEFAULT true NOT NULL, 
    sent_at TIMESTAMP WITH TIME ZONE, 
    delivered_at TIMESTAMP WITH TIME ZONE, 
    opened_at TIMESTAMP WITH TIME ZONE, 
    first_opened_at TIMESTAMP WITH TIME ZONE, 
    clicked_at TIMESTAMP WITH TIME ZONE, 
    replied_at TIMESTAMP WITH TIME ZONE, 
    bounced_at TIMESTAMP WITH TIME ZONE, 
    open_count INTEGER DEFAULT '0' NOT NULL, 
    click_count INTEGER DEFAULT '0' NOT NULL, 
    bounce_type VARCHAR(20), 
    error_message TEXT, 
    is_ai_generated BOOLEAN DEFAULT false NOT NULL, 
    ai_model VARCHAR(60), 
    was_edited_by_user BOOLEAN DEFAULT false NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_email_messages PRIMARY KEY (id), 
    CONSTRAINT fk_email_messages_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_email_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE SET NULL, 
    CONSTRAINT fk_email_messages_conversation_message_id_conversation_messages FOREIGN KEY(conversation_message_id) REFERENCES conversation_messages (id) ON DELETE CASCADE, 
    CONSTRAINT fk_email_messages_email_account_id_email_accounts FOREIGN KEY(email_account_id) REFERENCES email_accounts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_email_messages_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, 
    CONSTRAINT fk_email_messages_template_id_email_templates FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE SET NULL, 
    CONSTRAINT uq_email_messages_conversation_message_id UNIQUE (conversation_message_id)
);

CREATE INDEX ix_email_messages_account ON email_messages (email_account_id, sent_at);

CREATE INDEX ix_email_messages_conversation ON email_messages (conversation_id, created_at);

CREATE INDEX ix_email_messages_lead ON email_messages (lead_id, created_at);

CREATE INDEX ix_email_messages_owner_id ON email_messages (owner_id);

CREATE INDEX ix_email_messages_pending ON email_messages (status) WHERE status IN ('QUEUED', 'SENDING');

CREATE INDEX ix_email_messages_thread ON email_messages (provider_thread_id) WHERE provider_thread_id IS NOT NULL;

CREATE UNIQUE INDEX uq_email_messages_provider_message_id ON email_messages (provider_message_id) WHERE provider_message_id IS NOT NULL;

CREATE UNIQUE INDEX uq_email_messages_tracking_token ON email_messages (tracking_token);

CREATE UNIQUE INDEX uq_email_messages_unsubscribe_token ON email_messages (unsubscribe_token);

CREATE TABLE email_links (
    email_message_id UUID NOT NULL, 
    tracking_token UUID DEFAULT gen_random_uuid() NOT NULL, 
    original_url TEXT NOT NULL, 
    label VARCHAR(255), 
    position INTEGER, 
    click_count INTEGER DEFAULT '0' NOT NULL, 
    first_clicked_at TIMESTAMP WITH TIME ZONE, 
    last_clicked_at TIMESTAMP WITH TIME ZONE, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_email_links PRIMARY KEY (id), 
    CONSTRAINT fk_email_links_email_message_id_email_messages FOREIGN KEY(email_message_id) REFERENCES email_messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_email_links_message ON email_links (email_message_id);

CREATE UNIQUE INDEX uq_email_links_token ON email_links (tracking_token);

CREATE TABLE suppression_list (
    email CITEXT, 
    domain VARCHAR(255), 
    reason VARCHAR(40) NOT NULL, 
    source_email_id UUID, 
    notes TEXT, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_suppression_list PRIMARY KEY (id), 
    CONSTRAINT fk_suppression_list_source_email_id_email_messages FOREIGN KEY(source_email_id) REFERENCES email_messages (id) ON DELETE SET NULL
);

CREATE INDEX ix_suppression_list_owner_id ON suppression_list (owner_id);

CREATE UNIQUE INDEX uq_suppression_domain ON suppression_list (owner_id, lower(domain)) WHERE domain IS NOT NULL;

CREATE UNIQUE INDEX uq_suppression_email ON suppression_list (owner_id, lower(email)) WHERE email IS NOT NULL;

CREATE TABLE email_events (
    id BIGSERIAL NOT NULL, 
    email_message_id UUID NOT NULL, 
    email_link_id UUID, 
    event_type email_event_type NOT NULL, 
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    user_agent TEXT, 
    ip_address INET, 
    is_likely_bot BOOLEAN DEFAULT false NOT NULL, 
    metadata JSONB, 
    CONSTRAINT pk_email_events PRIMARY KEY (id), 
    CONSTRAINT fk_email_events_email_link_id_email_links FOREIGN KEY(email_link_id) REFERENCES email_links (id) ON DELETE SET NULL, 
    CONSTRAINT fk_email_events_email_message_id_email_messages FOREIGN KEY(email_message_id) REFERENCES email_messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_email_events_message ON email_events (email_message_id, occurred_at);

CREATE INDEX ix_email_events_type ON email_events (event_type, occurred_at);

INSERT INTO app_settings (id, sender_name) VALUES (1, '') ON CONFLICT (id) DO NOTHING;

UPDATE alembic_version SET version_num='2df69f93ac48' WHERE alembic_version.version_num = '99360f8ac979';

-- Running upgrade 2df69f93ac48 -> 8c1a4f6d2b73

DROP INDEX IF EXISTS uq_services_owner_name;

CREATE UNIQUE INDEX uq_services_owner_name ON services (owner_id, lower(name)) NULLS NOT DISTINCT;

DROP INDEX IF EXISTS uq_email_templates_owner_name;

CREATE UNIQUE INDEX uq_email_templates_owner_name ON email_templates (owner_id, lower(name)) NULLS NOT DISTINCT;

DROP INDEX IF EXISTS uq_email_accounts_owner_email;

CREATE UNIQUE INDEX uq_email_accounts_owner_email ON email_accounts (owner_id, lower(email)) NULLS NOT DISTINCT;

DROP INDEX IF EXISTS uq_suppression_email;

CREATE UNIQUE INDEX uq_suppression_email ON suppression_list (owner_id, lower(email)) NULLS NOT DISTINCT WHERE email IS NOT NULL;

DROP INDEX IF EXISTS uq_suppression_domain;

CREATE UNIQUE INDEX uq_suppression_domain ON suppression_list (owner_id, lower(domain)) NULLS NOT DISTINCT WHERE domain IS NOT NULL;

ALTER TABLE companies DROP CONSTRAINT IF EXISTS uq_companies_owner_dedupe_key;

ALTER TABLE companies ADD CONSTRAINT uq_companies_owner_dedupe_key UNIQUE NULLS NOT DISTINCT (owner_id, dedupe_key);

ALTER TABLE pipeline_stages DROP CONSTRAINT IF EXISTS uq_pipeline_stages_owner_key;

ALTER TABLE pipeline_stages ADD CONSTRAINT uq_pipeline_stages_owner_key UNIQUE NULLS NOT DISTINCT (owner_id, stage_key);

UPDATE alembic_version SET version_num='8c1a4f6d2b73' WHERE alembic_version.version_num = '2df69f93ac48';

-- Running upgrade 8c1a4f6d2b73 -> bf6d7b8ab808

CREATE TABLE sequences (
    name VARCHAR(160) NOT NULL, 
    description TEXT, 
    service_id UUID, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    stop_on_reply BOOLEAN DEFAULT true NOT NULL, 
    stop_on_click BOOLEAN DEFAULT false NOT NULL, 
    stop_on_meeting BOOLEAN DEFAULT true NOT NULL, 
    max_steps SMALLINT DEFAULT '3' NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_sequences PRIMARY KEY (id), 
    CONSTRAINT fk_sequences_service_id_services FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE SET NULL
);

CREATE INDEX ix_sequences_owner_id ON sequences (owner_id);

CREATE UNIQUE INDEX uq_sequences_owner_name ON sequences (owner_id, lower(name)) NULLS NOT DISTINCT;

CREATE TABLE sequence_steps (
    sequence_id UUID NOT NULL, 
    step_number SMALLINT NOT NULL, 
    template_id UUID NOT NULL, 
    delay_days SMALLINT DEFAULT '3' NOT NULL, 
    delay_hours SMALLINT DEFAULT '0' NOT NULL, 
    condition JSONB, 
    send_window_start TIME WITHOUT TIME ZONE, 
    send_window_end TIME WITHOUT TIME ZONE, 
    skip_weekends BOOLEAN DEFAULT true NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_sequence_steps PRIMARY KEY (id), 
    CONSTRAINT fk_sequence_steps_sequence_id_sequences FOREIGN KEY(sequence_id) REFERENCES sequences (id) ON DELETE CASCADE, 
    CONSTRAINT fk_sequence_steps_template_id_email_templates FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_sequence_steps_number UNIQUE (sequence_id, step_number)
);

CREATE TABLE follow_ups (
    lead_id UUID NOT NULL, 
    sequence_id UUID, 
    sequence_step SMALLINT, 
    template_id UUID, 
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    status VARCHAR(20) DEFAULT 'PENDING' NOT NULL, 
    sent_email_id UUID, 
    executed_at TIMESTAMP WITH TIME ZONE, 
    skip_reason VARCHAR(40), 
    attempts SMALLINT DEFAULT '0' NOT NULL, 
    is_manual BOOLEAN DEFAULT false NOT NULL, 
    note TEXT, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_follow_ups PRIMARY KEY (id), 
    CONSTRAINT fk_follow_ups_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, 
    CONSTRAINT fk_follow_ups_sent_email_id_email_messages FOREIGN KEY(sent_email_id) REFERENCES email_messages (id) ON DELETE SET NULL, 
    CONSTRAINT fk_follow_ups_sequence_id_sequences FOREIGN KEY(sequence_id) REFERENCES sequences (id) ON DELETE SET NULL, 
    CONSTRAINT fk_follow_ups_template_id_email_templates FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE SET NULL
);

CREATE INDEX ix_follow_ups_due ON follow_ups (scheduled_at) WHERE status = 'PENDING';

CREATE INDEX ix_follow_ups_lead ON follow_ups (lead_id, scheduled_at);

CREATE INDEX ix_follow_ups_owner_id ON follow_ups (owner_id);

CREATE UNIQUE INDEX uq_follow_ups_lead_step ON follow_ups (lead_id, sequence_id, sequence_step) WHERE sequence_id IS NOT NULL AND status = 'PENDING';

ALTER TABLE email_messages ADD COLUMN follow_up_id UUID;

ALTER TABLE email_messages ADD CONSTRAINT fk_email_messages_follow_up_id_follow_ups FOREIGN KEY(follow_up_id) REFERENCES follow_ups (id) ON DELETE SET NULL;

UPDATE alembic_version SET version_num='bf6d7b8ab808' WHERE alembic_version.version_num = '8c1a4f6d2b73';

-- Running upgrade bf6d7b8ab808 -> 0b31298f593b

ALTER TABLE conversations ADD COLUMN reply_intent reply_intent;

ALTER TABLE conversations ADD COLUMN intent_confidence NUMERIC(3, 2);

ALTER TABLE conversations ADD COLUMN intent_summary TEXT;

ALTER TABLE conversations ADD COLUMN intent_suggested_stage stage_type;

ALTER TABLE conversations ADD COLUMN intent_reply_points TEXT[];

ALTER TABLE conversations ADD COLUMN intent_source VARCHAR(10);

ALTER TABLE conversations ADD COLUMN intent_reviewed BOOLEAN DEFAULT false NOT NULL;

CREATE INDEX ix_conversations_intent_pending ON conversations (reply_intent) WHERE reply_intent IS NOT NULL AND intent_reviewed IS FALSE;

UPDATE alembic_version SET version_num='0b31298f593b' WHERE alembic_version.version_num = 'bf6d7b8ab808';

-- Running upgrade 0b31298f593b -> 3004cd69e632

CREATE TYPE call_script_type AS ENUM ('COLD_FIRST', 'GATEKEEPER', 'INBOUND', 'FOLLOW_UP', 'MEETING');

CREATE TYPE call_outcome AS ENUM ('NO_ANSWER', 'VOICEMAIL', 'GATEKEEPER', 'WRONG_NUMBER', 'CALLBACK', 'NOT_INTERESTED', 'INTERESTED', 'MEETING_SCHEDULED', 'DO_NOT_CALL');

ALTER TYPE activity_type ADD VALUE IF NOT EXISTS 'CALL_LOGGED';

CREATE TABLE call_scripts (
    name VARCHAR(160) NOT NULL, 
    script_type call_script_type NOT NULL, 
    service_id UUID, 
    opening TEXT NOT NULL, 
    context TEXT, 
    questions TEXT[] DEFAULT '{}'::text[] NOT NULL, 
    value_pitch TEXT, 
    close TEXT, 
    objections JSONB DEFAULT '[]'::jsonb NOT NULL, 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    is_system BOOLEAN DEFAULT false NOT NULL, 
    times_used INTEGER DEFAULT '0' NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_call_scripts PRIMARY KEY (id), 
    CONSTRAINT fk_call_scripts_service_id_services FOREIGN KEY(service_id) REFERENCES services (id) ON DELETE CASCADE, 
    CONSTRAINT uq_call_scripts_owner_name UNIQUE NULLS NOT DISTINCT (owner_id, name)
);

CREATE INDEX ix_call_scripts_owner_id ON call_scripts (owner_id);

CREATE INDEX ix_call_scripts_type ON call_scripts (owner_id, script_type, is_active);

CREATE TABLE call_logs (
    lead_id UUID NOT NULL, 
    contact_id UUID, 
    script_id UUID, 
    phone VARCHAR(40), 
    outcome call_outcome NOT NULL, 
    duration_seconds INTEGER, 
    notes TEXT, 
    attempt SMALLINT DEFAULT '1' NOT NULL, 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    owner_id UUID, 
    CONSTRAINT pk_call_logs PRIMARY KEY (id), 
    CONSTRAINT fk_call_logs_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL, 
    CONSTRAINT fk_call_logs_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE, 
    CONSTRAINT fk_call_logs_script_id_call_scripts FOREIGN KEY(script_id) REFERENCES call_scripts (id) ON DELETE SET NULL
);

CREATE INDEX ix_call_logs_lead ON call_logs (lead_id, occurred_at);

CREATE INDEX ix_call_logs_outcome ON call_logs (owner_id, outcome, occurred_at);

CREATE INDEX ix_call_logs_owner_id ON call_logs (owner_id);

Traceback (most recent call last):
  File "<frozen runpy>", line 203, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\__main__.py", line 4, in <module>
    main(prog="alembic")
    ~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\config.py", line 1039, in main
    CommandLine(prog=prog).main(argv=argv)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\config.py", line 1029, in main
    self.run_cmd(cfg, options)
    ~~~~~~~~~~~~^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\config.py", line 963, in run_cmd
    fn(
    ~~^
        config,
        ^^^^^^^
        *[getattr(options, k, None) for k in positional],
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        **{k: getattr(options, k, None) for k in kwarg},
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\command.py", line 487, in upgrade
    script.run_env()
    ~~~~~~~~~~~~~~^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\script\base.py", line 550, in run_env
    util.load_python_file(self.dir, "env.py")
    ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\util\pyfiles.py", line 114, in load_python_file
    module = load_module_py(module_id, path)
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\util\pyfiles.py", line 132, in load_module_py
    spec.loader.exec_module(module)  # type: ignore
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "C:\Users\edwin\Documents\Trinidad\mapache\backend\migrations\env.py", line 82, in <module>
    run_migrations_offline()
    ~~~~~~~~~~~~~~~~~~~~~~^^
  File "C:\Users\edwin\Documents\Trinidad\mapache\backend\migrations\env.py", line 51, in run_migrations_offline
    context.run_migrations()
    ~~~~~~~~~~~~~~~~~~~~~~^^
  File "<string>", line 8, in run_migrations
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\runtime\environment.py", line 970, in run_migrations
    self.get_context().run_migrations(**kw)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\runtime\migration.py", line 621, in run_migrations
    step.migration_fn(**kw)
    ~~~~~~~~~~~~~~~~~^^^^^^
  File "C:\Users\edwin\Documents\Trinidad\mapache\backend\migrations\versions\20260728_1757_fase_10_llamadas.py", line 183, in upgrade
    op.bulk_insert(
    ~~~~~~~~~~~~~~^
        sa.table(
        ^^^^^^^^^
    ...<28 lines>...
        ],
        ^^
    )
    ^
  File "<string>", line 8, in bulk_insert
  File "<string>", line 3, in bulk_insert
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\operations\ops.py", line 2539, in bulk_insert
    operations.invoke(op)
    ~~~~~~~~~~~~~~~~~^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\operations\base.py", line 448, in invoke
    return fn(self, operation)
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\operations\toimpl.py", line 250, in bulk_insert
    operations.impl.bulk_insert(  # type: ignore[union-attr]
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        operation.table, operation.rows, multiinsert=operation.multiinsert
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\ddl\impl.py", line 489, in bulk_insert
    self._exec(
    ~~~~~~~~~~^
        table.insert()
        ^^^^^^^^^^^^^^
    ...<14 lines>...
        )
        ^
    )
    ^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\ddl\impl.py", line 232, in _exec
    compiled = construct.compile(dialect=self.dialect, **compile_kw)
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\elements.py", line 311, in compile
    return self._compiler(dialect, **kw)
           ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\elements.py", line 323, in _compiler
    return dialect.statement_compiler(dialect, self, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 1462, in __init__
    Compiled.__init__(self, dialect, statement, **kwargs)
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 902, in __init__
    self.string = self.process(self.statement, **compile_kwargs)
                  ~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 948, in process
    return obj._compiler_dispatch(self, **kwargs)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\visitors.py", line 138, in _compiler_dispatch
    return meth(self, **kw)  # type: ignore  # noqa: E501
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 5955, in visit_insert
    crud_params_struct = crud._get_crud_params(
        self,
    ...<4 lines>...
        **kw,
    )
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\crud.py", line 315, in _get_crud_params
    use_insertmanyvalues, use_sentinel_columns = _scan_cols(
                                                 ~~~~~~~~~~^
        compiler,
        ^^^^^^^^^
    ...<9 lines>...
        kw,
        ^^^
    )
    ^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\crud.py", line 711, in _scan_cols
    _append_param_parameter(
    ~~~~~~~~~~~~~~~~~~~~~~~^
        compiler,
        ^^^^^^^^^
    ...<12 lines>...
        kw,
        ^^^
    )
    ^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\crud.py", line 937, in _append_param_parameter
    value = _handle_values_anonymous_param(
        compiler,
    ...<9 lines>...
        **kw,
    )
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\crud.py", line 517, in _handle_values_anonymous_param
    return value._compiler_dispatch(compiler, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\ext\compiler.py", line 539, in <lambda>
    lambda *arg, **kw: existing(*arg, **kw),
                       ~~~~~~~~^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\ext\compiler.py", line 592, in __call__
    expr = fn(element, compiler, **kw)
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\alembic\util\sqla_compat.py", line 517, in _render_literal_bindparam
    return compiler.render_literal_bindparam(element, **kw)
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 3902, in render_literal_bindparam
    return self.render_literal_value(value, bindparam.type)
           ~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\dialects\postgresql\base.py", line 2155, in render_literal_value
    value = super().render_literal_value(value, type_)
  File "C:\Users\edwin\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\sqlalchemy\sql\compiler.py", line 3937, in render_literal_value
    raise exc.CompileError(
    ...<3 lines>...
    )
sqlalchemy.exc.CompileError: No literal value renderer is available for literal value "[{'objection': 'Estoy ocupado', 'response': 'Te entiendo, llamé sin avisar. ¿Te llamo mañana a esta misma hora o prefieres que te escriba?'}, {'object ... (552 characters truncated) ... a trabajamos con alguien', 'response': 'Perfecto, no vengo a que cambien. ¿Qué les falta de lo que tienen hoy? Si no falta nada, te dejo tranquilo.'}]" with datatype JSONB
