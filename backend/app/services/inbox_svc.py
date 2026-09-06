"""Fase 6 — entrada de correo: respuestas, rebotes y auto-respuestas.

Aquí se decide qué es cada mensaje que llega, y esa decisión cambia el embudo:

  - **Respuesta real** → el prospecto avanza, la secuencia se detiene y el hilo
    queda marcado como pendiente de contestar.
  - **Auto-respuesta** ("estoy de vacaciones") → se guarda, pero no cuenta como
    respuesta ni detiene nada: solo aplaza el seguimiento.
  - **Rebote duro** → el contacto pasa a la lista de supresión. Seguir
    escribiendo a una dirección que no existe quema la reputación del dominio.

Confundir los tres es lo que hace que un CRM diga "45 % de respuesta" cuando la
mitad son mensajes automáticos.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.classifier import (
    INTENT_ENGAGEMENT,
    Classification,
    classify,
    looks_like_unsubscribe,
)
from app.ai.client import credentials_from
from app.core.enums import (
    ActivityType,
    ActorType,
    Channel,
    Direction,
    EmailEventType,
    EmailStatus,
    LeadStatus,
    ReplyIntent,
    VerificationStatus,
)
from app.core.logging import get_logger
from app.mail.base import InboundMessage
from app.models.contact import Contact
from app.models.email import (
    Conversation,
    ConversationMessage,
    EmailEvent,
    EmailMessage,
)
from app.models.email_account import EmailAccount
from app.models.lead import Lead
from app.models.settings import AppSettings
from app.services.activity_svc import ActivityService
from app.services.conversation_svc import ConversationService
from app.services.lead_svc import LeadService
from app.services.mail_admin_svc import SuppressionService
from app.services.sequence_svc import FollowUpService
from app.utils.text import snippet

logger = get_logger(__name__)

# Un "vuelvo el lunes" no es una respuesta, pero tampoco un no: se reintenta
# más adelante en vez de descartar al prospecto.
AUTO_REPLY_RETRY_DAYS = 5

# Citas del mensaje anterior. Se recortan del cuerpo guardado para que el
# snippet de la bandeja muestre lo que escribió el prospecto, no nuestro correo
# repetido debajo.
_QUOTE_MARKERS = (
    re.compile(r"^\s*El .+ escribió:\s*$", re.MULTILINE),
    re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Mensaje original\s*-{2,}\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$", re.MULTILINE),
)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Qué se hizo con un mensaje entrante."""

    kind: str  # reply | auto_reply | bounce | unmatched | duplicate
    lead_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    email_id: uuid.UUID | None = None
    detail: str | None = None


class InboxService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.leads = LeadService(session)
        self.conversations = ConversationService(session)
        self.activities = ActivityService(session)
        self.suppression = SuppressionService(session)

    async def ingest(self, account: EmailAccount, inbound: InboundMessage) -> IngestResult:
        """Procesa un mensaje entrante."""
        if await self._already_ingested(inbound.provider_message_id):
            return IngestResult(kind="duplicate", detail=inbound.provider_message_id)

        original = await self._match_original(inbound)

        if inbound.is_bounce:
            return await self.handle_bounce(account, inbound, original)

        if original is None:
            # Sin hilo al que pertenecer no se inventa un prospecto: podría ser
            # correo personal del usuario. Se deja constancia y se ignora.
            logger.info("inbound_unmatched", from_email=inbound.from_email)
            return IngestResult(kind="unmatched", detail=inbound.from_email)

        return await self._record_reply(account, inbound, original)

    # ------------------------------------------------------------ respuestas

    async def _record_reply(
        self, account: EmailAccount, inbound: InboundMessage, original: EmailMessage
    ) -> IngestResult:
        conversation = await self._conversation_of(original)
        lead = await self.leads.get_or_404(original.lead_id) if original.lead_id else None
        body = _strip_quoted(inbound.body_text)

        message = ConversationMessage(
            conversation_id=conversation.id if conversation else None,
            channel=Channel.EMAIL,
            direction=Direction.INBOUND,
            author_name=inbound.from_email,
            body_text=body,
            body_html=inbound.body_html,
            snippet=snippet(body, 200),
            occurred_at=inbound.received_at,
            owner_id=lead.owner_id if lead else None,
        )
        self.session.add(message)
        await self.session.flush()

        email_row = EmailMessage(
            conversation_message_id=message.id,
            conversation_id=conversation.id if conversation else None,
            lead_id=original.lead_id,
            contact_id=original.contact_id,
            email_account_id=account.id,
            direction=Direction.INBOUND,
            from_email=inbound.from_email,
            to_email=inbound.to_email or account.email,
            subject=inbound.subject,
            status=EmailStatus.DELIVERED,
            provider=account.provider,
            provider_message_id=inbound.provider_message_id,
            provider_thread_id=inbound.provider_thread_id,
            in_reply_to=inbound.in_reply_to,
            references_header=inbound.references,
            owner_id=lead.owner_id if lead else None,
        )
        self.session.add(email_row)
        await self.session.flush()

        if conversation is not None:
            conversation.message_count += 1
            conversation.last_message_at = inbound.received_at
            conversation.last_direction = Direction.INBOUND
            conversation.is_unread = True
            conversation.status = "NEEDS_REPLY"

        if inbound.is_auto_reply:
            return await self._handle_auto_reply(lead, original, email_row, conversation)

        # --- respuesta de verdad ---
        original.replied_at = inbound.received_at
        self.session.add(
            EmailEvent(
                email_message_id=original.id,
                event_type=EmailEventType.REPLIED,
                occurred_at=inbound.received_at,
            )
        )

        classification = await self._classify(inbound, original, lead, body)
        if conversation is not None:
            self._store_intent(conversation, classification)

        if lead is not None:
            lead.reply_intent = classification.intent
            lead.replied_at = inbound.received_at
            lead.last_activity_at = inbound.received_at
            # Una respuesta detiene la secuencia: a partir de aquí escribe una
            # persona, no la automatización. Los seguimientos ya programados se
            # cancelan aquí mismo — pausar el lead no basta si alguien lo
            # reanuda después.
            lead.sequence_paused = True
            lead.next_follow_up_at = None
            await FollowUpService(self.session).cancel_pending_for_lead(lead.id, reason="replied")
            await self.leads.add_engagement(lead, "EMAIL_REPLIED")
            # Pedir precio o una reunión vale más que un "gracias": el
            # engagement lo refleja aunque la etapa la decida el usuario.
            extra = INTENT_ENGAGEMENT.get(classification.intent)
            if extra and classification.is_confident:
                await self.leads.add_engagement(lead, extra)
            await self.leads.auto_advance(lead, "EMAIL_REPLIED")
            await self.activities.record(
                ActivityType.EMAIL_REPLIED,
                title=f"Respondió {inbound.from_email}",
                description=snippet(body, 300),
                lead_id=lead.id,
                company_id=lead.company_id,
                contact_id=original.contact_id,
                actor=ActorType.PROSPECT,
                metadata={"email_id": str(email_row.id)},
                owner_id=lead.owner_id,
            )

        # Única acción automática de la clasificación (§9): la baja. El coste de
        # equivocarse al otro lado —seguir escribiendo a quien pidió parar— es
        # mucho mayor que el de suprimir a alguien de más.
        if classification.intent is ReplyIntent.UNSUBSCRIBE or looks_like_unsubscribe(body):
            await self._suppress_on_request(inbound, original, lead)

        await self.session.flush()
        logger.info(
            "inbound_reply",
            lead=str(lead.id) if lead else None,
            from_email=inbound.from_email,
            intent=classification.intent.value,
            intent_source=classification.source,
        )
        return IngestResult(
            kind="reply",
            lead_id=lead.id if lead else None,
            conversation_id=conversation.id if conversation else None,
            email_id=email_row.id,
        )

    # ------------------------------------------------------------ intención

    async def _classify(
        self,
        inbound: InboundMessage,
        original: EmailMessage,
        lead: Lead | None,
        body: str,
    ) -> Classification:
        """Clasifica la respuesta. Nunca falla: si no hay IA, manda el motor de
        reglas."""
        settings = (await self.session.execute(select(AppSettings).limit(1))).scalar_one_or_none()

        return await classify(
            subject=inbound.subject,
            body=body,
            company_name=lead.company.name if lead else inbound.from_email,
            service_name=lead.service.name if lead and lead.service else None,
            our_last_message=original.subject,
            ai_enabled=bool(settings and settings.ai_enabled),
            credentials=credentials_from(settings),
        )

    @staticmethod
    def _store_intent(conversation: Conversation, classification: Classification) -> None:
        """Guarda la sugerencia en el hilo.

        Si el usuario ya revisó la intención de esta conversación, no se pisa:
        su corrección vale más que la siguiente inferencia.
        """
        if conversation.intent_reviewed:
            return

        conversation.reply_intent = classification.intent
        conversation.intent_confidence = Decimal(str(round(classification.confidence, 2)))
        conversation.intent_summary = classification.summary
        conversation.intent_suggested_stage = classification.suggested_stage
        conversation.intent_reply_points = classification.suggested_reply_points or None
        conversation.intent_source = classification.source

    async def _suppress_on_request(
        self, inbound: InboundMessage, original: EmailMessage, lead: Lead | None
    ) -> None:
        await self.suppression.add(
            email=inbound.from_email,
            reason="unsubscribed",
            notes="Lo pidió por respuesta al correo.",
            source_email_id=original.id,
        )
        if original.contact_id:
            contact = await self.session.get(Contact, original.contact_id)
            if contact is not None:
                contact.do_not_contact = True

        if lead is not None:
            lead.status = LeadStatus.DISQUALIFIED
            lead.sequence_paused = True
            lead.next_follow_up_at = None
            await FollowUpService(self.session).cancel_pending_for_lead(
                lead.id, reason="unsubscribed"
            )
            await self.activities.record(
                ActivityType.NOTE,
                title="Pidió no recibir más correos",
                description="Detectado en su respuesta. Dirección añadida a la lista de "
                "no contactar.",
                lead_id=lead.id,
                company_id=lead.company_id,
                actor=ActorType.PROSPECT,
                owner_id=lead.owner_id,
            )
        logger.info("unsubscribe_by_reply", email=inbound.from_email)

    async def _handle_auto_reply(
        self,
        lead: Lead | None,
        original: EmailMessage,
        email_row: EmailMessage,
        conversation: Conversation | None,
    ) -> IngestResult:
        """Auto-respuesta: ni avance de etapa ni contador de respuestas.

        Contarla inflaría la tasa de respuesta y, peor, movería el prospecto a
        "Respondió" cuando nadie lo ha leído todavía.
        """
        if conversation is not None:
            # No exige atención humana: la bandeja no la marca como pendiente.
            conversation.status = "AWAITING_REPLY"
            conversation.is_unread = False

        if lead is not None:
            lead.next_follow_up_at = datetime.now(UTC) + timedelta(days=AUTO_REPLY_RETRY_DAYS)
            await self.activities.record(
                ActivityType.NOTE,
                title="Respuesta automática recibida",
                description=(
                    f"Se reprograma el seguimiento en {AUTO_REPLY_RETRY_DAYS} días. "
                    "No cuenta como respuesta."
                ),
                lead_id=lead.id,
                company_id=lead.company_id,
                actor=ActorType.SYSTEM,
                owner_id=lead.owner_id,
            )

        await self.session.flush()
        logger.info("inbound_auto_reply", lead=str(lead.id) if lead else None)
        return IngestResult(
            kind="auto_reply",
            lead_id=lead.id if lead else None,
            conversation_id=conversation.id if conversation else None,
            email_id=email_row.id,
            detail=f"reprogramado +{AUTO_REPLY_RETRY_DAYS}d",
        )

    # ------------------------------------------------------------ rebotes

    async def handle_bounce(
        self,
        account: EmailAccount,
        inbound: InboundMessage,
        original: EmailMessage | None,
    ) -> IngestResult:
        """Rebote. El tipo decide todo lo demás.

        Duro (5.x.x): la dirección no existe. Va a supresión — insistir contra
        un buzón inexistente es la señal más clara de lista comprada y hunde la
        reputación del dominio.

        Blando (4.x.x): buzón lleno o servidor caído. No se descarta a nadie
        por eso.
        """
        hard = inbound.bounce_type == "hard"
        target = original.to_email if original else _extract_failed_recipient(inbound)

        if original is not None:
            original.bounced_at = inbound.received_at
            original.bounce_type = inbound.bounce_type
            original.status = EmailStatus.BOUNCED
            self.session.add(
                EmailEvent(
                    email_message_id=original.id,
                    event_type=EmailEventType.BOUNCED,
                    occurred_at=inbound.received_at,
                    event_metadata={"type": inbound.bounce_type},
                )
            )

        if hard and target:
            await self.suppression.add(
                email=target,
                reason="hard_bounce",
                notes=f"Rebote duro recibido el {inbound.received_at:%Y-%m-%d}.",
                source_email_id=original.id if original else None,
            )
            contact = await self._contact_by_email(target)
            if contact is not None:
                contact.email_verified = VerificationStatus.BOUNCED
                # Se escribe ya. La sesión va con `autoflush=False`, y la
                # siguiente consulta (`get_or_404`, con `populate_existing`)
                # recarga el contacto desde la base: sin este flush el cambio
                # se perdería y la dirección muerta seguiría dándose por buena.
                await self.session.flush()

        lead = None
        if original is not None and original.lead_id:
            lead = await self.leads.get_or_404(original.lead_id)
            if hard:
                lead.sequence_paused = True
                lead.next_follow_up_at = None
                await FollowUpService(self.session).cancel_pending_for_lead(
                    lead.id, reason="hard_bounce"
                )
            await self.activities.record(
                ActivityType.EMAIL_BOUNCED,
                title="Rebote duro" if hard else "Rebote temporal",
                description=(
                    f"{target}: la dirección no existe o rechaza el correo."
                    if hard
                    else f"{target}: rechazo temporal, se puede reintentar."
                ),
                lead_id=lead.id,
                company_id=lead.company_id,
                actor=ActorType.SYSTEM,
                metadata={"bounce_type": inbound.bounce_type},
                owner_id=lead.owner_id,
            )

        await self.session.flush()
        logger.info("inbound_bounce", target=target, bounce_type=inbound.bounce_type)
        return IngestResult(
            kind="bounce",
            lead_id=lead.id if lead else None,
            email_id=original.id if original else None,
            detail=inbound.bounce_type,
        )

    # ------------------------------------------------------------ emparejado

    async def _already_ingested(self, provider_message_id: str) -> bool:
        """Los proveedores reentregan: el webhook de Gmail puede repetir un
        mensaje y el delta de Graph también."""
        if not provider_message_id:
            return False
        result = await self.session.execute(
            select(EmailMessage.id)
            .where(EmailMessage.provider_message_id == provider_message_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _match_original(self, inbound: InboundMessage) -> EmailMessage | None:
        """Encuentra el correo saliente al que responde este mensaje.

        Por orden de fiabilidad: `In-Reply-To`/`References` (estándar y exacto),
        el hilo nativo del proveedor, y por último la dirección del remitente.
        """
        candidates = [inbound.in_reply_to, *(inbound.references or "").split()]
        message_ids = [c.strip() for c in candidates if c and c.strip()]
        if message_ids:
            result = await self.session.execute(
                select(EmailMessage)
                .where(
                    EmailMessage.provider_message_id.in_(message_ids),
                    EmailMessage.direction == Direction.OUTBOUND,
                )
                .order_by(EmailMessage.sent_at.desc())
                .limit(1)
            )
            found = result.scalar_one_or_none()
            if found is not None:
                return found

        if inbound.provider_thread_id:
            result = await self.session.execute(
                select(EmailMessage)
                .where(
                    EmailMessage.provider_thread_id == inbound.provider_thread_id,
                    EmailMessage.direction == Direction.OUTBOUND,
                )
                .order_by(EmailMessage.sent_at.desc())
                .limit(1)
            )
            found = result.scalar_one_or_none()
            if found is not None:
                return found

        # Último recurso: alguien que responde desde otra dirección o con el
        # hilo roto por su cliente de correo. Se busca a quién le escribimos.
        if inbound.from_email:
            result = await self.session.execute(
                select(EmailMessage)
                .where(
                    EmailMessage.to_email == inbound.from_email,
                    EmailMessage.direction == Direction.OUTBOUND,
                )
                .order_by(EmailMessage.sent_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

        return None

    async def _conversation_of(self, original: EmailMessage) -> Conversation | None:
        if original.conversation_id is None:
            return None
        return await self.session.get(Conversation, original.conversation_id)

    async def _contact_by_email(self, email: str) -> Contact | None:
        result = await self.session.execute(select(Contact).where(Contact.email == email).limit(1))
        return result.scalar_one_or_none()


def _strip_quoted(body: str) -> str:
    """Recorta la cita del mensaje anterior."""
    earliest = len(body)
    for marker in _QUOTE_MARKERS:
        match = marker.search(body)
        if match and match.start() < earliest:
            earliest = match.start()

    # Líneas que empiezan por ">" al final del cuerpo.
    trimmed = body[:earliest].rstrip()
    lines = trimmed.splitlines()
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()

    return "\n".join(lines).strip() or body.strip()


def _extract_failed_recipient(inbound: InboundMessage) -> str | None:
    """Saca la dirección que rebotó del cuerpo del DSN."""
    for header in ("x-failed-recipients", "original-recipient", "final-recipient"):
        value = inbound.raw_headers.get(header)
        if value:
            return value.rsplit(";", 1)[-1].strip()

    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", inbound.body_text)
    return match.group(0) if match else None


async def leads_pending_reply(session: AsyncSession, *, min_days: int = 0) -> list[Lead]:
    """Prospectos que abrieron y no han respondido (§20 del diseño).

    Se usa desde el worker de seguimiento; la API expone lo mismo por
    `/leads/segments/opened-no-reply`.
    """
    cutoff = datetime.now(UTC) - timedelta(days=min_days)
    result = await session.execute(
        select(Lead)
        .where(
            Lead.replied_at.is_(None),
            Lead.status == LeadStatus.OPEN,
            select(EmailMessage.id)
            .where(
                EmailMessage.lead_id == Lead.id,
                EmailMessage.direction == Direction.OUTBOUND,
                EmailMessage.opened_at.is_not(None),
                EmailMessage.opened_at <= cutoff,
                or_(EmailMessage.bounced_at.is_(None), EmailMessage.bounce_type != "hard"),
            )
            .exists(),
        )
        .order_by(Lead.engagement_score.desc())
    )
    return list(result.scalars().all())
