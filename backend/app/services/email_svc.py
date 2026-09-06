"""Módulos 9, 10 y 12 — preview y envío de correo.

Todo envío pasa por `send_one`, que aplica los guardrails **antes** de tocar
el proveedor. Si alguien añade otra ruta de envío que se los salte, el CRM
deja de cumplir la normativa: por eso viven aquí y no en el router.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import (
    ActivityType,
    ActorType,
    Channel,
    Direction,
    EmailEventType,
    EmailStatus,
    StageType,
)
from app.core.exceptions import ConfigurationError, NotFoundError
from app.core.logging import get_logger
from app.mail import builder, renderer
from app.mail.base import OutboundMessage
from app.mail.factory import provider_for
from app.mail.guardrails import Guardrails, SkipReason
from app.models.company import CompanySignal
from app.models.email import (
    Conversation,
    ConversationMessage,
    EmailEvent,
    EmailLink,
    EmailMessage,
    EmailTemplate,
)
from app.models.email_account import EmailAccount
from app.models.lead import Lead
from app.models.settings import AppSettings
from app.services.activity_svc import ActivityService
from app.services.lead_svc import LeadService
from app.utils.text import snippet

logger = get_logger(__name__)


@dataclass(slots=True)
class EmailDraft:
    """Borrador listo para revisar antes de enviar (Módulo 9)."""

    lead_id: uuid.UUID
    company_name: str
    to_email: str | None
    contact_name: str | None
    subject: str
    body_text: str
    body_html: str | None = None
    template_id: uuid.UUID | None = None
    warnings: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def can_send(self) -> bool:
        return self.blocked_reason is None and bool(self.to_email)


@dataclass(slots=True)
class SendOutcome:
    lead_id: uuid.UUID
    sent: bool
    email_id: uuid.UUID | None = None
    conversation_message_id: uuid.UUID | None = None
    skip_reason: SkipReason | None = None
    message: str | None = None


class EmailService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.guardrails = Guardrails(session)
        self.activities = ActivityService(session)
        self.leads = LeadService(session)

    # ------------------------------------------------------------ configuración

    async def get_settings_row(self) -> AppSettings:
        result = await self.session.execute(select(AppSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            raise ConfigurationError(
                "Falta la configuración de la aplicación.", code="SETTINGS_MISSING"
            )
        return settings

    async def resolve_account(self, account_id: uuid.UUID | None = None) -> EmailAccount:
        """Cuenta desde la que se envía: la pedida, la marcada por defecto o
        la primera activa."""
        if account_id is not None:
            account = await self.session.get(EmailAccount, account_id)
            if account is None:
                raise NotFoundError.for_entity("email_account", account_id)
            return account

        settings = await self.get_settings_row()
        if settings.default_account_id:
            account = await self.session.get(EmailAccount, settings.default_account_id)
            if account is not None:
                return account

        result = await self.session.execute(
            select(EmailAccount)
            .where(EmailAccount.status == "ACTIVE")
            .order_by(EmailAccount.is_default.desc(), EmailAccount.created_at)
            .limit(1)
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ConfigurationError(
                "No hay ninguna cuenta de correo conectada. Conecta una en Configuración "
                "antes de enviar.",
                code="NO_EMAIL_ACCOUNT",
            )
        return account

    # ------------------------------------------------------------ preview

    async def build_draft(
        self,
        lead: Lead,
        *,
        template: EmailTemplate | None,
        settings: AppSettings,
        account: EmailAccount,
        override_subject: str | None = None,
        override_body: str | None = None,
    ) -> EmailDraft:
        """Renderiza el correo de un lead sin enviarlo (Módulo 9).

        Devuelve también las advertencias: variables sin valor, ausencia de
        email, guardrails que lo bloquearían. El usuario ve el problema antes
        de pulsar enviar, no después.
        """
        contact = lead.contact
        to_email = (contact.email if contact else None) or lead.company.email

        signals = await self._signal_keys(lead.company_id)
        context = renderer.build_context(
            company_name=lead.company.name,
            sender_name=settings.sender_name or account.display_name or "",
            service_name=lead.service.name if lead.service else None,
            contact_name=contact.display_name if contact else None,
            city=lead.company.city,
            category=lead.company.category,
            website=lead.company.website,
            signals=signals,
            # El valor real se sustituye al enviar, cuando ya existe el token.
            unsubscribe_url="{{unsubscribe_url}}",
        )

        subject = override_subject or (template.subject if template else "")
        body_text = override_body or (template.body_text if template else "")
        body_html = template.body_html if template and not override_body else None

        rendered = renderer.render(
            subject=subject, body_text=body_text, body_html=body_html, context=context
        )

        warnings: list[str] = []
        for missing in rendered.missing:
            if missing != "unsubscribe_url":
                warnings.append(f"Sin dato para {{{{{missing}}}}}")
        if contact is None:
            warnings.append("El prospecto no tiene contacto asignado")

        verdict = await self.guardrails.check(
            settings=settings,
            account=account,
            to_email=to_email,
            contact=contact,
            lead_id=lead.id,
            template_id=template.id if template else None,
        )

        return EmailDraft(
            lead_id=lead.id,
            company_name=lead.company.name,
            to_email=to_email,
            contact_name=contact.display_name if contact else None,
            subject=rendered.subject,
            body_text=rendered.body_text,
            body_html=rendered.body_html,
            template_id=template.id if template else None,
            warnings=warnings,
            blocked_reason=verdict.message,
        )

    async def _signal_keys(self, company_id: uuid.UUID) -> list[str]:
        result = await self.session.execute(
            select(CompanySignal.signal_key).where(CompanySignal.company_id == company_id)
        )
        return [row[0] for row in result]

    # ------------------------------------------------------------ envío

    async def send_one(
        self,
        lead: Lead,
        *,
        subject: str,
        body_text: str,
        body_html: str | None,
        template: EmailTemplate | None,
        settings: AppSettings,
        account: EmailAccount,
        was_edited: bool = False,
        is_automated: bool = False,
        manual: bool = False,
    ) -> SendOutcome:
        """Envía un correo a un prospecto, con todas las barreras aplicadas.

        `lead` tiene que venir de `LeadService.get_or_404`: se accede a
        `lead.company` y `lead.contact`, y en async un lazy load implícito
        rompe con `MissingGreenlet`.

        `manual=True` marca la respuesta escrita a mano en una conversación
        abierta: se salta el ritmo y la ventana horaria, nunca las barreras de
        cumplimiento (ver `Guardrails.check`).
        """
        now = datetime.now(UTC)
        Guardrails.reset_counters_if_needed(account, now, settings=settings)

        contact = lead.contact
        to_email = (contact.email if contact else None) or lead.company.email

        verdict = await self.guardrails.check(
            settings=settings,
            account=account,
            to_email=to_email,
            contact=contact,
            lead_id=lead.id,
            template_id=template.id if template else None,
            now=now,
            manual=manual,
        )
        if not verdict.allowed:
            logger.info("email_skipped", lead=str(lead.id), reason=verdict.reason)
            return SendOutcome(
                lead_id=lead.id,
                sent=False,
                skip_reason=verdict.reason,
                message=verdict.message,
            )

        assert to_email is not None  # garantizado por el guardrail NO_EMAIL

        conversation = await self._get_or_create_conversation(lead, subject)
        base_url = get_settings().public_base_url

        # 1. Registro del sobre: los tokens se generan aquí porque el pixel y
        #    los links del cuerpo tienen que apuntar a ellos.
        email_row = EmailMessage(
            conversation_id=conversation.id,
            lead_id=lead.id,
            contact_id=contact.id if contact else None,
            template_id=template.id if template else None,
            email_account_id=account.id,
            direction=Direction.OUTBOUND,
            from_email=account.email,
            to_email=to_email,
            subject=subject,
            status=EmailStatus.QUEUED,
            provider=account.provider,
            owner_id=lead.owner_id,
            tracking_token=uuid.uuid4(),
            unsubscribe_token=uuid.uuid4(),
            was_edited_by_user=was_edited,
        )
        self.session.add(email_row)
        await self.session.flush()

        root = base_url.rstrip("/")
        unsubscribe_url = f"{root}/tracking/unsubscribe/{email_row.unsubscribe_token}"
        pixel_url = f"{root}/tracking/open/{email_row.tracking_token}.gif"

        # 2. Sustituir el placeholder de baja por la URL real.
        body_text = body_text.replace("{{unsubscribe_url}}", unsubscribe_url)
        if body_html:
            body_html = body_html.replace("{{unsubscribe_url}}", unsubscribe_url)

        # 3. Pie legal: identidad, dirección y baja. No es opcional.
        footer_text, footer_html = builder.build_footer(
            sender_name=settings.sender_name or account.display_name or account.email,
            sender_address=settings.address_of_sender,
            unsubscribe_url=unsubscribe_url,
        )
        body_text += footer_text
        if body_html:
            body_html += footer_html

        # 4. Reescribir links (el de baja se excluye: no se rastrea la baja).
        body_text, body_html, tracked = builder.rewrite_links(
            body_text=body_text,
            body_html=body_html,
            base_url=base_url,
            skip_urls={unsubscribe_url},
        )
        for link in tracked:
            self.session.add(
                EmailLink(
                    email_message_id=email_row.id,
                    tracking_token=link.token,
                    original_url=link.original_url,
                    position=link.position,
                )
            )

        # 5. Pixel de apertura.
        if email_row.tracking_enabled:
            body_html = builder.inject_pixel(body_html, pixel_url)

        # 6. Threading: mantiene la respuesta en el mismo hilo del prospecto.
        previous = await self._last_outbound(conversation.id, exclude=email_row.id)
        outbound = OutboundMessage(
            from_email=account.email,
            from_name=settings.sender_name or account.display_name or "",
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to=settings.reply_to,
            in_reply_to=previous.provider_message_id if previous else None,
            references=builder.build_references(
                previous.references_header if previous else None,
                previous.provider_message_id if previous else None,
            ),
            provider_thread_id=previous.provider_thread_id if previous else None,
            unsubscribe_url=unsubscribe_url,
        )

        # 7. Enviar por el proveedor de la cuenta.
        email_row.status = EmailStatus.SENDING
        await self.session.flush()

        provider = await provider_for(account, self.session)
        try:
            result = await provider.send(outbound)
        except Exception as exc:  # noqa: BLE001 - un fallo del proveedor no aborta el lote
            email_row.status = EmailStatus.FAILED
            email_row.error_message = str(exc)[:2000]
            self.session.add(
                EmailEvent(email_message_id=email_row.id, event_type=EmailEventType.FAILED)
            )
            await self.session.flush()
            logger.warning("email_send_failed", lead=str(lead.id), error=str(exc))
            return SendOutcome(lead_id=lead.id, sent=False, email_id=email_row.id, message=str(exc))

        # 8. Persistir el resultado y el cuerpo en la conversación.
        email_row.status = EmailStatus.SENT
        email_row.sent_at = result.sent_at or now
        email_row.provider_message_id = result.provider_message_id
        email_row.provider_thread_id = result.provider_thread_id
        email_row.references_header = outbound.references

        message = ConversationMessage(
            conversation_id=conversation.id,
            channel=Channel.EMAIL,
            direction=Direction.OUTBOUND,
            author_name=settings.sender_name or account.email,
            body_text=body_text,
            body_html=body_html,
            snippet=snippet(body_text, 200),
            occurred_at=email_row.sent_at,
            is_automated=is_automated,
            owner_id=lead.owner_id,
        )
        self.session.add(message)
        await self.session.flush()
        email_row.conversation_message_id = message.id

        conversation.message_count += 1
        conversation.last_message_at = email_row.sent_at
        conversation.last_direction = Direction.OUTBOUND
        conversation.status = "AWAITING_REPLY"

        # 9. Contadores, eventos y estado del lead.
        account.sent_today += 1
        account.sent_this_hour += 1
        account.last_sent_at = email_row.sent_at

        self.session.add(EmailEvent(email_message_id=email_row.id, event_type=EmailEventType.SENT))
        if template is not None:
            template.times_used += 1

        if lead.first_contact_at is None:
            lead.first_contact_at = email_row.sent_at
        lead.last_contact_at = email_row.sent_at
        lead.last_activity_at = email_row.sent_at

        await self.activities.record(
            ActivityType.EMAIL_SENT,
            title=f"Correo enviado a {to_email}",
            description=subject,
            lead_id=lead.id,
            company_id=lead.company_id,
            contact_id=contact.id if contact else None,
            actor=ActorType.SYSTEM if is_automated else ActorType.USER,
            metadata={"email_id": str(email_row.id), "subject": subject},
            owner_id=lead.owner_id,
        )
        await self.leads.auto_advance(lead, "EMAIL_SENT")
        await self.session.flush()

        logger.info("email_sent", lead=str(lead.id), to=to_email, email_id=str(email_row.id))
        return SendOutcome(
            lead_id=lead.id,
            sent=True,
            email_id=email_row.id,
            conversation_message_id=message.id,
        )

    async def _get_or_create_conversation(self, lead: Lead, subject: str) -> Conversation:
        """Un hilo por lead y canal.

        El `thread_key` se deriva del lead: todos los correos a ese prospecto
        van al mismo hilo, aunque cambie el asunto entre seguimientos.
        """
        thread_key = f"lead:{lead.id}"
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.lead_id == lead.id,
                Conversation.channel == Channel.EMAIL,
                Conversation.thread_key == thread_key,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is not None:
            return conversation

        conversation = Conversation(
            lead_id=lead.id,
            contact_id=lead.contact_id,
            channel=Channel.EMAIL,
            subject=subject,
            thread_key=thread_key,
            owner_id=lead.owner_id,
        )
        self.session.add(conversation)
        await self.session.flush()

        await self.activities.record(
            ActivityType.CONVERSATION_STARTED,
            title="Conversación iniciada",
            lead_id=lead.id,
            company_id=lead.company_id,
            owner_id=lead.owner_id,
        )
        return conversation

    async def _last_outbound(
        self, conversation_id: uuid.UUID, *, exclude: uuid.UUID
    ) -> EmailMessage | None:
        result = await self.session.execute(
            select(EmailMessage)
            .where(
                EmailMessage.conversation_id == conversation_id,
                EmailMessage.id != exclude,
                EmailMessage.provider_message_id.is_not(None),
            )
            .order_by(EmailMessage.sent_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------ etapas

    async def advance_on_first_contact(self, lead: Lead) -> None:
        """Mueve el lead a CONTACTADO si aún está antes de esa etapa."""
        stage = await self.leads.pipeline.get_by_type(StageType.CONTACTED)
        if stage is not None:
            await self.leads.move_stage(
                lead, stage.id, actor=ActorType.SYSTEM, reason="Primer correo enviado"
            )
