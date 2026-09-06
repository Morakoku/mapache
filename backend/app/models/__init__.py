"""Registro de modelos.

Todo modelo nuevo se importa aquí. Alembic autogenera contra
`Base.metadata`, y lo que no esté importado no existe para él: se traduce en
una migración que borra tablas en producción.
"""

from __future__ import annotations

from app.models.activity import Activity, Task
from app.models.audit import AuditLog
from app.models.base import Base, BaseModel, OwnedModel
from app.models.call import DEFAULT_SCRIPTS, CallLog, CallScript
from app.models.company import Company, CompanySignal, CompanySocial, CompanySource
from app.models.contact import Contact
from app.models.email import (
    Conversation,
    ConversationMessage,
    EmailEvent,
    EmailLink,
    EmailMessage,
    EmailTemplate,
    SuppressionEntry,
)
from app.models.email_account import EmailAccount
from app.models.idempotency import IdempotencyEvent
from app.models.job import Job
from app.models.lead import Lead, LeadStageHistory
from app.models.pipeline import DEFAULT_STAGES, PipelineStage
from app.models.search import Search, SearchResult, SearchRun
from app.models.sequence import FollowUp, Sequence, SequenceStep
from app.models.service import Service
from app.models.settings import AppSettings

__all__ = [
    "DEFAULT_SCRIPTS",
    "DEFAULT_STAGES",
    "Activity",
    "AppSettings",
    "AuditLog",
    "Base",
    "BaseModel",
    "CallLog",
    "CallScript",
    "Company",
    "CompanySignal",
    "CompanySocial",
    "CompanySource",
    "Contact",
    "Conversation",
    "ConversationMessage",
    "EmailAccount",
    "EmailEvent",
    "EmailLink",
    "EmailMessage",
    "EmailTemplate",
    "FollowUp",
    "IdempotencyEvent",
    "Job",
    "Lead",
    "LeadStageHistory",
    "OwnedModel",
    "PipelineStage",
    "Search",
    "SearchResult",
    "SearchRun",
    "Sequence",
    "SequenceStep",
    "Service",
    "SuppressionEntry",
    "Task",
]
