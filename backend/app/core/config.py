"""Configuración de la aplicación.

Todo se lee del entorno (o de un `.env` local). No hay valores secretos
hardcodeados. Ver `.env.example` para el conjunto completo.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app
    app_name: str = "CRM Prospección"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # URL pública del backend. La usan el pixel de tracking, los links
    # rastreados y los callbacks de OAuth, así que tiene que ser
    # alcanzable desde fuera — no vale "localhost" en producción.
    public_base_url: str = "http://localhost:8000"

    # Orígenes permitidos para el frontend.
    # `NoDecode` evita que pydantic-settings intente parsear el valor del .env
    # como JSON: aquí se acepta una lista separada por comas (ver validador).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # A dónde volver tras un callback de OAuth. Si está vacío, el callback
    # muestra una página propia en vez de redirigir: así el backend funciona
    # solo, sin frontend levantado.
    frontend_base_url: str | None = None

    # ---------------------------------------------------------------- db
    database_url: PostgresDsn
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_pre_ping: bool = True

    # ---------------------------------------------------------------- Supabase REST API (serverless fallback)
    # Project URL from Supabase Dashboard → Settings → API (e.g., https://xyz.supabase.co)
    supabase_url: str | None = None
    # Anon (public) key from Supabase Dashboard → Settings → API
    supabase_anon_key: SecretStr | None = None
    # Service role key (bypasses RLS) - ONLY for server-side admin operations
    supabase_service_role_key: SecretStr | None = None

    # ---------------------------------------------------------------- seguridad
    # Clave maestra para cifrar credenciales en BD (Fernet, 32 bytes url-safe b64).
    # Generar con:
    #   python -c "from cryptography.fernet import Fernet; \
    #              print(Fernet.generate_key().decode())"
    encryption_key: SecretStr

    # Firma de tokens de sesión.
    secret_key: SecretStr
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 días

    # ------------------------------------------------------------ servicio-a-servicio (L1)
    # Autenticación mínima para clientes de servicio (Hermes → Mapache) mediante
    # token de portador con firma HMAC-SHA256:
    #   Authorization: Bearer <clien...ure>
    # `service_auth_enabled=True` exige token válido en toda la API /api/v1/*
    # (/health, /tracking y /auth quedan públicos; los consumen terceros y el
    # orquestador). Con `False` (default local) la capa pasa transparente.
    service_auth_enabled: bool = False
    service_token_key: SecretStr | None = None
    service_token_ttl_seconds: int = 300
    # Identidades de servicio autorizadas a llamar a Mapache (lista separada por
    # comas). Hoy el único consumidor es "hermes"; el allowlist permite rechazar
    # tokens firmados por una identidad desconocida aunque se posea el HMAC
    # (lo exige el test 9 de LOOP-07). En claro nunca se expone el valor de la
    # clave: `service_token_key` es `SecretStr` y aquí solo guardamos el nombre
    # de la identidad, no su secreto.
    service_trusted_client_ids: str = "hermes"
    # L2 (LOOP-14): scopes concedidos a la identidad `hermes` (lista separada
    # por comas). MÍNIMO privilegio del contrato: hermes.dispatch + hermes.jobs.read.
    # Cualquier otro scope (crm.read, scrape.run, jobs.read, mail.send, admin…)
    # NO es grantable a una identidad de servicio → DENY.
    hermes_scopes: str = "hermes.dispatch,hermes.jobs.read"
    # L2 (LOOP-23): scopes concedidos a la identidad `guaki` (lista separada
    # por comas). MÍNIMO privilegio: guaki.read (prospectos, funnel, links).
    guaki_scopes: str = "guaki.read"
    # Strict mode is required before production can accept service jobs.
    tenant_isolation_enforced: bool = False

    # ---------------------------------------------------------------- localización (Colombia)
    default_country_code: str = "CO"
    default_phone_region: str = "CO"
    default_currency: str = "COP"
    default_locale: str = "es-CO"
    default_timezone: str = "America/Bogota"

    # ---------------------------------------------------------------- OAuth de correo
    # Sin estas credenciales, la UI solo ofrece SMTP. Para Gmail hay que crear
    # un proyecto en Google Cloud Console; para Outlook, registrar una app en
    # Azure. Los scopes solicitados son el mínimo para enviar y leer.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None

    # ---------------------------------------------------------------- planificador
    # Sin esto, las secuencias existen pero nadie las dispara. Se puede apagar
    # para levantar una segunda instancia de la API sin duplicar los ticks.
    scheduler_enabled: bool = True
    followup_tick_minutes: int = 15
    # Con Gmail/Outlook conectados hay webhook y esto sobra; con SMTP/IMAP es
    # la única forma de enterarse de una respuesta.
    inbox_sync_minutes: int = 10

    # ---------------------------------------------------------------- IA
    # Sin clave, la IA se apaga sola: las plantillas se siguen renderizando y
    # la clasificación de respuestas cae al motor de reglas. Es una mejora,
    # no una dependencia dura.
    anthropic_api_key: SecretStr | None = None
    ai_max_output_tokens: int = 8000
    ai_timeout_seconds: int = 90

    # ---------------------------------------------------------------- jobs
    # "inprocess" para el MVP; "arq" cuando se migre a Redis (Fase 9).
    job_queue_backend: Literal["inprocess", "arq"] = "inprocess"
    job_max_attempts: int = 3
    job_worker_concurrency: int = 2

    # ---------------------------------------------------------------- logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False  # True en producción, consola legible en local

    # ------------------------------------------------------- seguridad (LOOP-13)
    # Cabeceras defensivas (CSP, nosniff, XFO, Referrer-Policy) en toda la API.
    security_headers_enabled: bool = True
    # HSTS SOLO cuando haya TLS real delante; sin TLS no se envía.
    hsts_enabled: bool = False
    # Auditoría best-effort de POST/PATCH/PUT/DELETE + acciones sensibles.
    audit_enabled: bool = True
    # Idempotencia por Idempotency-Key en operaciones con efecto.
    idempotency_enabled: bool = True
    # Rate limiting por categoría + IP (email, scraping, settings, jobs, tracking).
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    # DELETE y cancelación de jobs exigen ?confirm=true.
    destructive_confirm_required: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Permite pasar CORS_ORIGINS como lista separada por comas."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def _reject_incomplete_production_configuration(self) -> Settings:
        """Evita que una configuracion de produccion incompleta arranque.

        Este gate no habilita produccion ni sustituye la autenticacion humana o
        el aislamiento por tenant. Solo hace que un despliegue accidental falle
        antes de exponer una API sin la barrera Hermes o detras de HTTP.
        """
        if not self.is_production:
            return self
        if self.debug:
            raise ValueError("production requires DEBUG=false")
        if not self.public_base_url.startswith("https://"):
            raise ValueError("production requires an HTTPS PUBLIC_BASE_URL")
        if not self.service_auth_enabled:
            raise ValueError("production requires SERVICE_AUTH_ENABLED=true")
        service_key = self.service_token_key.get_secret_value() if self.service_token_key else ""
        if not service_key or service_key == "<placeholder>":
            raise ValueError("production requires a managed SERVICE_TOKEN_KEY")
        if not self.tenant_isolation_enforced:
            raise ValueError("production requires TENANT_ISOLATION_ENFORCED=true")
        return self

    @property
    def hermes_scope_set(self) -> frozenset[str]:
        """Scopes concedidos al cliente `hermes` (parsea `hermes_scopes`)."""
        return frozenset(p.strip() for p in self.hermes_scopes.split(",") if p.strip())

    def service_scopes_for(self, client_id: str) -> frozenset[str]:
        """Scopes concedidos a una identidad de servicio.

        Solo `hermes` y `guaki` tienen identidad hoy; cualquier otra identidad
        queda sin scopes (frozenset vacío) y por tanto se le deniegan todas
        las operaciones protegidas en L2.
        """
        if client_id == "hermes":
            return self.hermes_scope_set
        if client_id == "guaki":
            return self.guaki_scope_set
        return frozenset()

    @property
    def guaki_scope_set(self) -> frozenset[str]:
        """Scopes concedidos al cliente `guaki` (parsea `guaki_scopes`)."""
        return frozenset(p.strip() for p in self.guaki_scopes.split(",") if p.strip())

    @property
    def trusted_client_ids(self) -> frozenset[str]:
        """Identidades de cliente permitidas (`service_trusted_client_ids`).

        ``SERVICE_TRUSTED_CLIENT_IDS`` es una lista separada por comas:
        ``"hermes"`` por defecto. Solo ``hermes`` puede llamar a la API de
        servicio mientras la lista no cambie (rotación futura: añadir el nuevo
        id, desplegar, y después retirar el viejo).
        """
        return frozenset(p.strip() for p in self.service_trusted_client_ids.split(",") if p.strip())

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def supabase_anon_key_value(self) -> str | None:
        """Get the anon key as a plain string."""
        return self.supabase_anon_key.get_secret_value() if self.supabase_anon_key else None

    @property
    def supabase_service_role_key_value(self) -> str | None:
        """Get the service role key as a plain string."""
        return self.supabase_service_role_key.get_secret_value() if self.supabase_service_role_key else None

    @property
    def sqlalchemy_url(self) -> str:
        """DSN con el driver async explícito, compatible con asyncpg."""
        url = str(self.database_url)
        if url.startswith("postgresql://"):
            # asyncpg no acepta sslmode en el DSN; quitar sslmode
            if "sslmode=" in url:
                import re
                url = re.sub(r"[?&]sslmode=[^&]+", "", url)
                url = re.sub(r"\?&", "?", url)
                url = url.rstrip("&?")
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def is_serverless(self) -> bool:
        """True si corremos en Vercel/serverless (pooler transaccional)."""
        url = str(self.database_url)
        return "pooler.supabase.com:6543" in url or "pooler.supabase.com:5432" in url

    @property
    def alembic_url(self) -> str:
        """Alembic corre migraciones de forma síncrona: driver psycopg/asyncpg aparte.

        Usamos el mismo driver async — `migrations/env.py` abre un engine async.
        """
        return self.sqlalchemy_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única de configuración.

    Cacheada: leer el entorno en cada request es innecesario y hace los tests
    impredecibles. Para sobrescribir en tests: `get_settings.cache_clear()`.
    """
    # Los campos obligatorios llegan del entorno; no se pasan por argumento.
    return Settings()