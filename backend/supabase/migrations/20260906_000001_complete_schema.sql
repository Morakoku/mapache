-- Mapache CRM - Complete Schema Migration
-- Generated from Alembic migrations (all phases)

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

CREATE TYPE job_type AS ENUM ('DISCOVERY', 'ENRICHMENT', 'SCORING', 'SEND_BATCH', 'INBOX_SYNC', 'FOLLOWUP_TICK', 'SCRAPER_HEALTH');
CREATE TYPE job_status AS ENUM ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');
CREATE TYPE source_type AS ENUM ('GOOGLE_MAPS', 'GOOGLE_PLACES_API', 'APIFY', 'WEBSITE', 'LINKEDIN', 'INSTAGRAM', 'FACEBOOK', 'MANUAL', 'AI', 'IMPORT');
CREATE TYPE verification_status AS ENUM ('UNVERIFIED', 'SYNTAX_OK', 'MX_OK', 'VERIFIED', 'RISKY', 'INVALID', 'BOUNCED');
CREATE TYPE pipeline_stage_type AS ENUM ('NEW', 'CONTACTED', 'QUALIFIED', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST', 'ON_HOLD');
CREATE TYPE contact_method AS ENUM ('EMAIL', 'PHONE', 'WHATSAPP', 'MESSENGER', 'LINKEDIN', 'INSTAGRAM', 'IN_PERSON');
CREATE TYPE communication_direction AS ENUM ('INBOUND', 'OUTBOUND');
CREATE TYPE communication_channel AS ENUM ('EMAIL', 'PHONE', 'WHATSAPP', 'MESSENGER', 'LINKEDIN', 'INSTAGRAM', 'MEETING', 'NOTE');
CREATE TYPE lead_status AS ENUM ('NEW', 'CONTACTED', 'QUALIFIED', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST', 'ON_HOLD');
CREATE TYPE lead_source AS ENUM ('WEBSITE', 'REFERRAL', 'SOCIAL_MEDIA', 'ADVERTISEMENT', 'EVENT', 'COLD_OUTREACH', 'PARTNERSHIP', 'OTHER');
CREATE TYPE scrape_status AS ENUM ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');
CREATE TYPE scrape_type AS ENUM ('FULL', 'INCREMENTAL', 'SINGLE');
CREATE TYPE script_type AS ENUM ('OUTBOUND', 'FOLLOWUP', 'OBJECTION', 'CLOSING');
CREATE TYPE call_outcome AS ENUM ('NO_ANSWER', 'LEFT_MESSAGE', 'CALLBACK', 'NOT_INTERESTED', 'INTERESTED', 'SENT', 'BOUNCED');
CREATE TYPE batch_status AS ENUM ('DRAFT', 'QUEUED', 'SENDING', 'PAUSED', 'COMPLETED', 'FAILED');
CREATE TYPE stage_type AS ENUM ('NEW', 'QUALIFIED', 'CONTACT_FOUND', 'CONTACTED', 'OPENED', 'REPLIED', 'CONVERSATION', 'INTERESTED', 'MEETING', 'OPPORTUNITY', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST');
CREATE TYPE reply_intent AS ENUM ('POSITIVE', 'NEUTRAL', 'NEGATIVE', 'QUESTION', 'PRICING', 'MEETING_REQUEST', 'OUT_OF_OFFICE', 'UNSUBSCRIBE', 'WRONG_PERSON', 'UNKNOWN');
CREATE TYPE actor_type AS ENUM ('USER', 'SYSTEM', 'AI', 'PROSPECT');
CREATE TYPE activity_type AS ENUM ('COMPANY_FOUND', 'EMAIL_FOUND', 'LEAD_CREATED', 'LEAD_QUALIFIED', 'STAGE_CHANGED', 'EMAIL_SENT', 'EMAIL_DELIVERED', 'EMAIL_OPENED', 'EMAIL_CLICKED', 'EMAIL_REPLIED', 'EMAIL_BOUNCED', 'CONVERSATION_STARTED', 'FOLLOWUP_SCHEDULED', 'FOLLOWUP_SENT', 'MEETING_SCHEDULED', 'PROPOSAL_SENT', 'NOTE', 'TASK_CREATED', 'TASK_COMPLETED', 'AI_PERSONALIZED', 'AI_CLASSIFIED', 'WON', 'LOST');
CREATE TYPE channel AS ENUM ('EMAIL', 'WHATSAPP', 'MESSENGER', 'PHONE', 'MEETING', 'LINKEDIN', 'INSTAGRAM', 'SMS');
CREATE TYPE direction AS ENUM ('INBOUND', 'OUTBOUND');
CREATE TYPE message_status AS ENUM ('DRAFT', 'QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'OPENED', 'CLICKED', 'REPLIED', 'BOUNCED', 'FAILED', 'SPAM', 'UNSUBSCRIBED');
CREATE TYPE sequence_status AS ENUM ('DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED');
CREATE TYPE step_type AS ENUM ('EMAIL', 'WAIT', 'TASK', 'CONDITION', 'SPLIT');
CREATE TYPE followup_trigger AS ENUM ('NO_REPLY', 'NO_OPEN', 'NO_CLICK', 'POSITIVE_REPLY', 'NEGATIVE_REPLY', 'MEETING_SCHEDULED');

-- Phase 1: Jobs
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

-- Phase 2: Companies, Services, Contacts, Leads
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
    price_min NUMERIC(10, 2),
    price_max NUMERIC(10, 2),
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
    signal_type VARCHAR(60) NOT NULL,
    value TEXT,
    confidence FLOAT,
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_company_signals PRIMARY KEY (id),
    CONSTRAINT fk_company_signals_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_company_signals_company ON company_signals (company_id);

CREATE TABLE company_socials (
    company_id UUID NOT NULL,
    platform VARCHAR(40) NOT NULL,
    url TEXT NOT NULL,
    handle VARCHAR(120),
    followers INTEGER,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_company_socials PRIMARY KEY (id),
    CONSTRAINT fk_company_socials_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_company_socials_company ON company_socials (company_id);

CREATE TABLE company_sources (
    company_id UUID NOT NULL,
    source_type source_type NOT NULL,
    source_name VARCHAR(120),
    source_url TEXT,
    first_seen_at TIMESTAMP WITH TIME ZONE,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_company_sources PRIMARY KEY (id),
    CONSTRAINT fk_company_sources_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE CASCADE
);

CREATE INDEX ix_company_sources_company ON company_sources (company_id);

CREATE TABLE searches (
    name VARCHAR(255) NOT NULL,
    query TEXT,
    category VARCHAR(120),
    city VARCHAR(120),
    state VARCHAR(120),
    country VARCHAR(120),
    radius_km INTEGER,
    max_results INTEGER,
    is_active BOOLEAN DEFAULT true NOT NULL,
    last_run_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_searches PRIMARY KEY (id)
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

-- Phase 3: Pipeline, Contacts, Leads, Activities, Tasks
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
CREATE INDEX ix_contacts_email ON contacts (email) WHERE email IS NOT NULL;
CREATE INDEX ix_contacts_owner_id ON contacts (owner_id);

CREATE TABLE leads (
    company_id UUID,
    contact_id UUID,
    stage_id UUID,
    status lead_status DEFAULT 'NEW' NOT NULL,
    source lead_source DEFAULT 'OTHER' NOT NULL,
    priority SMALLINT DEFAULT '0' NOT NULL,
    estimated_value NUMERIC(12, 2),
    estimated_close_date DATE,
    actual_close_date DATE,
    assigned_to UUID,
    first_contact_at TIMESTAMP WITH TIME ZONE,
    last_activity_at TIMESTAMP WITH TIME ZONE,
    next_followup_at TIMESTAMP WITH TIME ZONE,
    next_followup_note TEXT,
    is_customer BOOLEAN DEFAULT false NOT NULL,
    lost_reason TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_leads PRIMARY KEY (id),
    CONSTRAINT fk_leads_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE SET NULL,
    CONSTRAINT fk_leads_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
    CONSTRAINT fk_leads_stage_id_pipeline_stages FOREIGN KEY(stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL
);

CREATE INDEX ix_leads_company ON leads (company_id);
CREATE INDEX ix_leads_contact ON leads (contact_id);
CREATE INDEX ix_leads_stage ON leads (stage_id);
CREATE INDEX ix_leads_status ON leads (status);
CREATE INDEX ix_leads_owner_id ON leads (owner_id);
CREATE INDEX ix_leads_next_followup ON leads (next_followup_at) WHERE next_followup_at IS NOT NULL;

CREATE TABLE activities (
    lead_id UUID,
    contact_id UUID,
    company_id UUID,
    activity_type activity_type NOT NULL,
    actor_type actor_type DEFAULT 'SYSTEM' NOT NULL,
    actor_id UUID,
    subject TEXT,
    body TEXT,
    metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
    is_system_generated BOOLEAN DEFAULT false NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_activities PRIMARY KEY (id),
    CONSTRAINT fk_activities_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE,
    CONSTRAINT fk_activities_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
    CONSTRAINT fk_activities_company_id_companies FOREIGN KEY(company_id) REFERENCES companies (id) ON DELETE SET NULL
);

CREATE INDEX ix_activities_lead ON activities (lead_id);
CREATE INDEX ix_activities_contact ON activities (contact_id);
CREATE INDEX ix_activities_company ON activities (company_id);
CREATE INDEX ix_activities_type ON activities (activity_type);
CREATE INDEX ix_activities_created ON activities (created_at);
CREATE INDEX ix_activities_owner_id ON activities (owner_id);

CREATE TABLE lead_stage_history (
    lead_id UUID NOT NULL,
    from_stage_id UUID,
    to_stage_id UUID,
    changed_by UUID,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    duration_in_stage INTEGER,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_lead_stage_history PRIMARY KEY (id),
    CONSTRAINT fk_lead_stage_history_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE,
    CONSTRAINT fk_lead_stage_history_from_stage_id_pipeline_stages FOREIGN KEY(from_stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL,
    CONSTRAINT fk_lead_stage_history_to_stage_id_pipeline_stages FOREIGN KEY(to_stage_id) REFERENCES pipeline_stages (id) ON DELETE SET NULL
);

CREATE INDEX ix_lead_stage_history_lead ON lead_stage_history (lead_id);

CREATE TABLE tasks (
    lead_id UUID,
    contact_id UUID,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    due_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    assigned_to UUID,
    priority SMALLINT DEFAULT '0' NOT NULL,
    is_completed BOOLEAN DEFAULT false NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_tasks PRIMARY KEY (id),
    CONSTRAINT fk_tasks_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE,
    CONSTRAINT fk_tasks_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL
);

CREATE INDEX ix_tasks_lead ON tasks (lead_id);
CREATE INDEX ix_tasks_due ON tasks (due_at) WHERE due_at IS NOT NULL;
CREATE INDEX ix_tasks_owner_id ON tasks (owner_id);

UPDATE alembic_version SET version_num='99360f8ac979' WHERE alembic_version.version_num = '1dbee674b5e4';

-- Phase 4: Email Accounts & Settings
CREATE TABLE email_accounts (
    email CITEXT NOT NULL,
    display_name VARCHAR(120),
    provider VARCHAR(40) DEFAULT 'GMAIL' NOT NULL,
    imap_host VARCHAR(255),
    imap_port INTEGER DEFAULT 993,
    imap_username CITEXT,
    imap_password_encrypted TEXT,
    smtp_host VARCHAR(255),
    smtp_port INTEGER DEFAULT 587,
    smtp_username CITEXT,
    smtp_password_encrypted TEXT,
    is_active BOOLEAN DEFAULT true NOT NULL,
    is_primary BOOLEAN DEFAULT false NOT NULL,
    last_sync_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    daily_limit INTEGER DEFAULT 100,
    hourly_limit INTEGER DEFAULT 20,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_email_accounts PRIMARY KEY (id),
    CONSTRAINT uq_email_accounts_owner_email UNIQUE (owner_id, email)
);

CREATE INDEX ix_email_accounts_owner_id ON email_accounts (owner_id);

CREATE TABLE app_settings (
    key VARCHAR(120) NOT NULL,
    value JSONB NOT NULL,
    description TEXT,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_app_settings PRIMARY KEY (id),
    CONSTRAINT uq_app_settings_owner_key UNIQUE (owner_id, key)
);

CREATE INDEX ix_app_settings_owner_id ON app_settings (owner_id);

UPDATE alembic_version SET version_num='2df69f93ac48' WHERE alembic_version.version_num = '99360f8ac979';

-- Phase 5: Email Templates & Conversations
CREATE TABLE email_templates (
    name VARCHAR(160) NOT NULL,
    subject TEXT NOT NULL,
    body_html TEXT,
    body_text TEXT,
    category VARCHAR(60),
    is_active BOOLEAN DEFAULT true NOT NULL,
    usage_count INTEGER DEFAULT '0' NOT NULL,
    last_used_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_email_templates PRIMARY KEY (id)
);

CREATE INDEX ix_email_templates_owner_id ON email_templates (owner_id);

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

CREATE INDEX ix_conversations_lead ON conversations (lead_id);
CREATE INDEX ix_conversations_contact ON conversations (contact_id);
CREATE INDEX ix_conversations_owner_id ON conversations (owner_id);

CREATE TABLE conversation_messages (
    conversation_id UUID NOT NULL,
    direction direction NOT NULL,
    subject TEXT,
    body_html TEXT,
    body_text TEXT,
    raw_payload JSONB,
    sent_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    opened_at TIMESTAMP WITH TIME ZONE,
    clicked_at TIMESTAMP WITH TIME ZONE,
    replied_at TIMESTAMP WITH TIME ZONE,
    bounced_at TIMESTAMP WITH TIME ZONE,
    status message_status DEFAULT 'DRAFT' NOT NULL,
    external_id VARCHAR(255),
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_conversation_messages PRIMARY KEY (id),
    CONSTRAINT fk_conversation_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);

CREATE INDEX ix_conversation_messages_conversation ON conversation_messages (conversation_id);
CREATE INDEX ix_conversation_messages_external ON conversation_messages (external_id) WHERE external_id IS NOT NULL;
CREATE INDEX ix_conversation_messages_owner_id ON conversation_messages (owner_id);

CREATE TABLE email_messages (
    conversation_id UUID,
    message_id_header VARCHAR(255),
    thread_id_header VARCHAR(255),
    from_email CITEXT NOT NULL,
    from_name VARCHAR(255),
    to_emails CITEXT[] DEFAULT '{}'::citext[] NOT NULL,
    cc_emails CITEXT[] DEFAULT '{}'::citext[],
    bcc_emails CITEXT[] DEFAULT '{}'::citext[],
    subject TEXT,
    body_html TEXT,
    body_text TEXT,
    direction direction NOT NULL,
    status message_status DEFAULT 'DRAFT' NOT NULL,
    sent_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    opened_at TIMESTAMP WITH TIME ZONE,
    clicked_at TIMESTAMP WITH TIME ZONE,
    replied_at TIMESTAMP WITH TIME ZONE,
    bounced_at TIMESTAMP WITH TIME ZONE,
    is_automated BOOLEAN DEFAULT false NOT NULL,
    is_template_based BOOLEAN DEFAULT false NOT NULL,
    template_id UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_email_messages PRIMARY KEY (id),
    CONSTRAINT fk_email_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE SET NULL,
    CONSTRAINT fk_email_messages_template_id_email_templates FOREIGN KEY(template_id) REFERENCES email_templates (id) ON DELETE SET NULL
);

CREATE INDEX ix_email_messages_conversation ON email_messages (conversation_id);
CREATE INDEX ix_email_messages_from ON email_messages (from_email);
CREATE INDEX ix_email_messages_status ON email_messages (status);
CREATE INDEX ix_email_messages_owner_id ON email_messages (owner_id);

CREATE TABLE email_links (
    email_message_id UUID NOT NULL,
    url TEXT NOT NULL,
    click_count INTEGER DEFAULT '0' NOT NULL,
    last_clicked_at TIMESTAMP WITH TIME ZONE,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_email_links PRIMARY KEY (id),
    CONSTRAINT fk_email_links_email_message_id_email_messages FOREIGN KEY(email_message_id) REFERENCES email_messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_email_links_email ON email_links (email_message_id);

CREATE TABLE suppression_list (
    email CITEXT NOT NULL,
    reason VARCHAR(60) NOT NULL,
    source VARCHAR(60),
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_suppression_list PRIMARY KEY (id),
    CONSTRAINT uq_suppression_list_owner_email UNIQUE (owner_id, email)
);

CREATE INDEX ix_suppression_list_owner_id ON suppression_list (owner_id);

CREATE TABLE email_events (
    email_message_id UUID,
    event_type VARCHAR(40) NOT NULL,
    event_data JSONB DEFAULT '{}'::jsonb NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_email_events PRIMARY KEY (id),
    CONSTRAINT fk_email_events_email_message_id_email_messages FOREIGN KEY(email_message_id) REFERENCES email_messages (id) ON DELETE CASCADE
);

CREATE INDEX ix_email_events_email ON email_events (email_message_id);
CREATE INDEX ix_email_events_type ON email_events (event_type);

UPDATE alembic_version SET version_num='8c1a4f6d2b73' WHERE alembic_version.version_num = '2df69f93ac48';

-- Phase 6: Sequences & Follow-ups
CREATE TABLE sequences (
    name VARCHAR(160) NOT NULL,
    description TEXT,
    status sequence_status DEFAULT 'DRAFT' NOT NULL,
    total_steps SMALLINT DEFAULT '0' NOT NULL,
    is_active BOOLEAN DEFAULT false NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_sequences PRIMARY KEY (id)
);

CREATE INDEX ix_sequences_owner_id ON sequences (owner_id);

CREATE TABLE sequence_steps (
    sequence_id UUID NOT NULL,
    step_type step_type NOT NULL,
    position SMALLINT NOT NULL,
    name VARCHAR(160),
    wait_interval INTEGER,
    wait_unit VARCHAR(20),
    email_template_id UUID,
    condition_json JSONB,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_sequence_steps PRIMARY KEY (id),
    CONSTRAINT fk_sequence_steps_sequence_id_sequences FOREIGN KEY(sequence_id) REFERENCES sequences (id) ON DELETE CASCADE,
    CONSTRAINT fk_sequence_steps_email_template_id_email_templates FOREIGN KEY(email_template_id) REFERENCES email_templates (id) ON DELETE SET NULL
);

CREATE INDEX ix_sequence_steps_sequence ON sequence_steps (sequence_id);

CREATE TABLE follow_ups (
    lead_id UUID NOT NULL,
    contact_id UUID,
    trigger followup_trigger NOT NULL,
    trigger_data JSONB DEFAULT '{}'::jsonb NOT NULL,
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
    executed_at TIMESTAMP WITH TIME ZONE,
    status batch_status DEFAULT 'QUEUED' NOT NULL,
    sequence_step_id UUID,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_follow_ups PRIMARY KEY (id),
    CONSTRAINT fk_follow_ups_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE,
    CONSTRAINT fk_follow_ups_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL,
    CONSTRAINT fk_follow_ups_sequence_step_id_sequence_steps FOREIGN KEY(sequence_step_id) REFERENCES sequence_steps (id) ON DELETE SET NULL
);

CREATE INDEX ix_follow_ups_lead ON follow_ups (lead_id);
CREATE INDEX ix_follow_ups_scheduled ON follow_ups (scheduled_at);
CREATE INDEX ix_follow_ups_status ON follow_ups (status);
CREATE INDEX ix_follow_ups_owner_id ON follow_ups (owner_id);

UPDATE alembic_version SET version_num='bf6d7b8ab808' WHERE alembic_version.version_num = '8c1a4f6d2b73';

-- Phase 7: Call Scripts & Logs
CREATE TABLE call_scripts (
    name VARCHAR(160) NOT NULL,
    script_type script_type NOT NULL,
    body TEXT NOT NULL,
    is_active BOOLEAN DEFAULT true NOT NULL,
    usage_count INTEGER DEFAULT '0' NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_call_scripts PRIMARY KEY (id)
);

CREATE INDEX ix_call_scripts_owner_id ON call_scripts (owner_id);
CREATE INDEX ix_call_scripts_type ON call_scripts (owner_id, script_type, is_active);

CREATE TABLE call_logs (
    lead_id UUID NOT NULL,
    contact_id UUID,
    direction direction NOT NULL,
    outcome call_outcome,
    duration_seconds INTEGER,
    notes TEXT,
    recording_url TEXT,
    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    owner_id UUID,
    CONSTRAINT pk_call_logs PRIMARY KEY (id),
    CONSTRAINT fk_call_logs_lead_id_leads FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE,
    CONSTRAINT fk_call_logs_contact_id_contacts FOREIGN KEY(contact_id) REFERENCES contacts (id) ON DELETE SET NULL
);

CREATE INDEX ix_call_logs_lead ON call_logs (lead_id, occurred_at);
CREATE INDEX ix_call_logs_outcome ON call_logs (owner_id, outcome, occurred_at);
CREATE INDEX ix_call_logs_owner_id ON call_logs (owner_id);

UPDATE alembic_version SET version_num='0b31298f593b' WHERE alembic_version.version_num = 'bf6d7b8ab808';
