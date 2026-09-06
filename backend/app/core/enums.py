"""Enums compartidos por todo el dominio.

Se mapean a tipos ENUM nativos de PostgreSQL (ver `models/base.py`).
El *valor* de cada miembro es lo que se guarda en BD, así que renombrar
un valor exige una migración — renombrar el miembro Python, no.
"""

from __future__ import annotations

from enum import StrEnum


class StageType(StrEnum):
    """Semántica fija de una etapa del pipeline.

    El usuario puede renombrar y reordenar las etapas (`pipeline_stages.name`),
    pero el `stage_type` no cambia: las métricas del embudo se calculan sobre
    él. Sin esta separación, renombrar una columna del Kanban rompe el
    dashboard.
    """

    NEW = "NEW"
    QUALIFIED = "QUALIFIED"
    CONTACT_FOUND = "CONTACT_FOUND"
    CONTACTED = "CONTACTED"
    OPENED = "OPENED"
    REPLIED = "REPLIED"
    CONVERSATION = "CONVERSATION"
    INTERESTED = "INTERESTED"
    MEETING = "MEETING"
    OPPORTUNITY = "OPPORTUNITY"
    PROPOSAL = "PROPOSAL"
    NEGOTIATION = "NEGOTIATION"
    WON = "WON"
    LOST = "LOST"


class LeadStatus(StrEnum):
    OPEN = "OPEN"
    WON = "WON"
    LOST = "LOST"
    DISQUALIFIED = "DISQUALIFIED"
    PAUSED = "PAUSED"


class SourceType(StrEnum):
    """De dónde salió un dato. Se guarda por dato, no por empresa."""

    GOOGLE_MAPS = "GOOGLE_MAPS"
    GOOGLE_PLACES_API = "GOOGLE_PLACES_API"
    APIFY = "APIFY"
    WEBSITE = "WEBSITE"
    LINKEDIN = "LINKEDIN"
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"
    MANUAL = "MANUAL"
    AI = "AI"
    IMPORT = "IMPORT"


class VerificationStatus(StrEnum):
    """Estado de verificación de un email.

    No llegamos a "VERIFIED" por SMTP probing (quema reputación de IP y se
    comporta como un escaneo). MX_OK es lo máximo que afirmamos con certeza.
    """

    UNVERIFIED = "UNVERIFIED"
    SYNTAX_OK = "SYNTAX_OK"
    MX_OK = "MX_OK"
    VERIFIED = "VERIFIED"
    RISKY = "RISKY"
    INVALID = "INVALID"
    BOUNCED = "BOUNCED"


class EmailStatus(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    BOUNCED = "BOUNCED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EmailEventType(StrEnum):
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    OPENED = "OPENED"
    CLICKED = "CLICKED"
    REPLIED = "REPLIED"
    BOUNCED = "BOUNCED"
    COMPLAINED = "COMPLAINED"
    UNSUBSCRIBED = "UNSUBSCRIBED"
    FAILED = "FAILED"


class Direction(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class Channel(StrEnum):
    """Canal de una conversación. EMAIL hoy; WHATSAPP en Fase 10."""

    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    LINKEDIN = "LINKEDIN"
    PHONE = "PHONE"
    MANUAL = "MANUAL"


class MailProviderType(StrEnum):
    GMAIL = "GMAIL"
    MICROSOFT = "MICROSOFT"
    SMTP = "SMTP"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


class ReplyIntent(StrEnum):
    """Intención detectada en una respuesta entrante.

    La IA la sugiere; el usuario siempre puede corregirla.
    """

    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    QUESTION = "QUESTION"
    PRICING = "PRICING"
    MEETING_REQUEST = "MEETING_REQUEST"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    WRONG_PERSON = "WRONG_PERSON"
    UNKNOWN = "UNKNOWN"


class JobType(StrEnum):
    DISCOVERY = "DISCOVERY"
    ENRICHMENT = "ENRICHMENT"
    SCORING = "SCORING"
    SEND_BATCH = "SEND_BATCH"
    INBOX_SYNC = "INBOX_SYNC"
    FOLLOWUP_TICK = "FOLLOWUP_TICK"
    SCRAPER_HEALTH = "SCRAPER_HEALTH"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


class ActivityType(StrEnum):
    COMPANY_FOUND = "COMPANY_FOUND"
    EMAIL_FOUND = "EMAIL_FOUND"
    LEAD_CREATED = "LEAD_CREATED"
    LEAD_QUALIFIED = "LEAD_QUALIFIED"
    STAGE_CHANGED = "STAGE_CHANGED"
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_DELIVERED = "EMAIL_DELIVERED"
    EMAIL_OPENED = "EMAIL_OPENED"
    EMAIL_CLICKED = "EMAIL_CLICKED"
    EMAIL_REPLIED = "EMAIL_REPLIED"
    EMAIL_BOUNCED = "EMAIL_BOUNCED"
    CONVERSATION_STARTED = "CONVERSATION_STARTED"
    FOLLOWUP_SCHEDULED = "FOLLOWUP_SCHEDULED"
    FOLLOWUP_SENT = "FOLLOWUP_SENT"
    MEETING_SCHEDULED = "MEETING_SCHEDULED"
    PROPOSAL_SENT = "PROPOSAL_SENT"
    NOTE = "NOTE"
    TASK_CREATED = "TASK_CREATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    AI_PERSONALIZED = "AI_PERSONALIZED"
    AI_CLASSIFIED = "AI_CLASSIFIED"
    CALL_LOGGED = "CALL_LOGGED"
    WON = "WON"
    LOST = "LOST"


class SerpProvider(StrEnum):
    """Buscador web con el que se consultan perfiles e información pública.

    Los dos son APIs oficiales con su propia cuota: no se rastrea Google ni
    ninguna red, se le pregunta a un índice que ya existe. Google Custom Search
    da 100 consultas al día; Brave, 2.000 al mes con un solo dato de
    configuración (no hace falta crear un motor).
    """

    GOOGLE_CSE = "GOOGLE_CSE"
    BRAVE = "BRAVE"


class AIProvider(StrEnum):
    """Proveedor de IA configurado.

    Claude va por el SDK oficial de Anthropic. Los otros tres exponen una API
    compatible con la de OpenAI, así que comparten un único cliente HTTP: lo
    único que cambia entre ellos es la URL base y el nombre del modelo.
    """

    ANTHROPIC = "ANTHROPIC"
    OPENAI = "OPENAI"
    DEEPSEEK = "DEEPSEEK"
    KIMI = "KIMI"


class CallScriptType(StrEnum):
    """Situación de la llamada.

    No es lo mismo llamar a quien no te conoce que a quien acaba de responder
    un correo: el guion cambia entero, y elegir mal la situación es la causa
    número uno de que una llamada suene a robot.
    """

    COLD_FIRST = "COLD_FIRST"
    GATEKEEPER = "GATEKEEPER"
    INBOUND = "INBOUND"
    FOLLOW_UP = "FOLLOW_UP"
    MEETING = "MEETING"


class CallOutcome(StrEnum):
    """Cómo acabó la llamada.

    Se registra siempre, incluso cuando no contestan: la mitad del valor de
    llamar está en saber cuántos intentos hacen falta para hablar con alguien.
    """

    NO_ANSWER = "NO_ANSWER"
    VOICEMAIL = "VOICEMAIL"
    GATEKEEPER = "GATEKEEPER"
    WRONG_NUMBER = "WRONG_NUMBER"
    CALLBACK = "CALLBACK"
    NOT_INTERESTED = "NOT_INTERESTED"
    INTERESTED = "INTERESTED"
    MEETING_SCHEDULED = "MEETING_SCHEDULED"
    DO_NOT_CALL = "DO_NOT_CALL"


class ActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AI = "AI"
    PROSPECT = "PROSPECT"
