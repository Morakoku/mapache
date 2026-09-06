"""Cuentas de correo, plantillas y lista de supresión."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccountStatus, MailProviderType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import decrypt_optional, encrypt_optional
from app.mail import oauth
from app.mail.base import MailAuthError
from app.mail.factory import provider_for
from app.mail.guardrails import local_now
from app.mail.renderer import REQUIRED_VARIABLE, validate_template
from app.models.email import EmailTemplate, SuppressionEntry
from app.models.email_account import EmailAccount
from app.models.settings import AppSettings
from app.repositories.base import BaseRepository

logger = get_logger(__name__)


class EmailAccountRepository(BaseRepository[EmailAccount]):
    model = EmailAccount


class TemplateRepository(BaseRepository[EmailTemplate]):
    model = EmailTemplate


class SuppressionRepository(BaseRepository[SuppressionEntry]):
    model = SuppressionEntry


class EmailAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EmailAccountRepository(session)

    async def list_accounts(self) -> list[EmailAccount]:
        result = await self.session.execute(
            select(EmailAccount).order_by(EmailAccount.is_default.desc(), EmailAccount.created_at)
        )
        return list(result.scalars().all())

    async def get_or_404(self, account_id: uuid.UUID) -> EmailAccount:
        account = await self.repo.get(account_id)
        if account is None:
            raise NotFoundError.for_entity("email_account", account_id)
        return account

    async def create_smtp(self, data: dict) -> EmailAccount:
        """Alta manual de una cuenta SMTP/IMAP.

        Las contraseñas se cifran antes de tocar la base: un dump de Postgres
        no debe llevarse los buzones del usuario.
        """
        password = data.pop("smtp_password", None)
        imap_password = data.pop("imap_password", None)

        account = EmailAccount(
            provider=MailProviderType.SMTP,
            smtp_password_enc=encrypt_optional(password),
            imap_password_enc=encrypt_optional(imap_password or password),
            **data,
        )
        try:
            await self.repo.add(account)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Ya existe una cuenta con el correo {data.get('email')}.",
                code="ACCOUNT_ALREADY_EXISTS",
            ) from exc

        await self._ensure_one_default(account)
        return account

    async def upsert_oauth(
        self, provider: MailProviderType, tokens: oauth.OAuthTokens
    ) -> EmailAccount:
        """Crea o actualiza la cuenta tras un callback de OAuth."""
        if not tokens.email:
            raise ValidationError(
                "El proveedor no devolvió el correo de la cuenta.",
                code="OAUTH_NO_EMAIL",
            )

        existing = await self.repo.get_by(email=tokens.email)
        account = existing or EmailAccount(provider=provider, email=tokens.email)

        account.provider = provider
        account.oauth_access_token_enc = encrypt_optional(tokens.access_token)
        if tokens.refresh_token:
            account.oauth_refresh_token_enc = encrypt_optional(tokens.refresh_token)
        account.oauth_expires_at = tokens.expires_at
        account.oauth_scopes = tokens.scopes
        account.external_account_id = tokens.external_id
        account.status = AccountStatus.ACTIVE
        account.sync_error = None

        if existing is None:
            await self.repo.add(account)
        else:
            await self.session.flush()

        await self._ensure_one_default(account)
        logger.info("oauth_account_connected", provider=provider.value, email=tokens.email)
        return account

    async def update(self, account_id: uuid.UUID, data: dict) -> EmailAccount:
        account = await self.get_or_404(account_id)
        for key, value in data.items():
            setattr(account, key, value)
        await self.session.flush()
        if data.get("is_default"):
            await self._ensure_one_default(account)
        return account

    async def disconnect(self, account_id: uuid.UUID) -> None:
        """Desconecta la cuenta y revoca el token **en el proveedor**.

        Borrarlo solo de nuestra base dejaría el acceso vivo en Google: el
        usuario creería haberlo revocado y no sería cierto.
        """
        account = await self.get_or_404(account_id)

        if account.is_oauth:
            token = decrypt_optional(account.oauth_refresh_token_enc) or decrypt_optional(
                account.oauth_access_token_enc
            )
            if token:
                try:
                    await oauth.revoke(account.provider, token)
                except Exception as exc:  # noqa: BLE001 - el borrado local sigue
                    logger.warning("oauth_revoke_failed", error=str(exc))

        await self.repo.delete(account)

    async def verify(self, account_id: uuid.UUID) -> EmailAccount:
        """Comprueba las credenciales y refleja el resultado en el estado."""
        account = await self.get_or_404(account_id)
        try:
            provider = await provider_for(account, self.session)
            await provider.verify()
            account.status = AccountStatus.ACTIVE
            account.sync_error = None
        except MailAuthError as exc:
            account.status = AccountStatus.TOKEN_EXPIRED
            account.sync_error = exc.message
            raise
        except Exception as exc:
            account.status = AccountStatus.ERROR
            account.sync_error = str(exc)[:500]
            raise
        finally:
            await self.session.flush()
        return account

    async def _ensure_one_default(self, account: EmailAccount) -> None:
        """Solo una cuenta puede ser la predeterminada."""
        if not account.is_default:
            # Si no hay ninguna marcada, esta pasa a serlo.
            count = await self.session.execute(
                select(func.count()).select_from(EmailAccount).where(EmailAccount.is_default)
            )
            if (count.scalar_one() or 0) == 0:
                account.is_default = True
            else:
                return

        others = await self.session.execute(
            select(EmailAccount).where(
                EmailAccount.id != account.id, EmailAccount.is_default.is_(True)
            )
        )
        for other in others.scalars().all():
            other.is_default = False

        settings = (await self.session.execute(select(AppSettings).limit(1))).scalar_one_or_none()
        if settings is not None:
            settings.default_account_id = account.id
        await self.session.flush()


class TemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TemplateRepository(session)

    async def get_or_404(self, template_id: uuid.UUID) -> EmailTemplate:
        template = await self.repo.get(template_id)
        if template is None:
            raise NotFoundError.for_entity("template", template_id)
        return template

    async def create(self, data: dict) -> EmailTemplate:
        data = self._prepare(data)
        template = EmailTemplate(**data)
        try:
            await self.repo.add(template)
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Ya existe una plantilla llamada '{data.get('name')}'.",
                code="TEMPLATE_NAME_TAKEN",
            ) from exc
        return template

    async def update(self, template_id: uuid.UUID, data: dict) -> EmailTemplate:
        template = await self.get_or_404(template_id)
        merged = {
            "subject": data.get("subject", template.subject),
            "body_text": data.get("body_text", template.body_text),
            "body_html": data.get("body_html", template.body_html),
        }
        prepared = self._prepare({**data, **merged})
        # Solo se escriben los campos que llegaron, más las variables
        # recalculadas.
        for key in data:
            setattr(template, key, prepared.get(key, data[key]))
        template.variables_used = prepared["variables_used"]
        await self.session.flush()
        return template

    async def delete(self, template_id: uuid.UUID) -> None:
        await self.repo.delete(await self.get_or_404(template_id))

    def build_list_query(self, *, category: str | None = None) -> Select[tuple[EmailTemplate]]:
        stmt = select(EmailTemplate).order_by(EmailTemplate.category, EmailTemplate.name)
        if category:
            stmt = stmt.where(EmailTemplate.category == category)
        return stmt

    @staticmethod
    def _prepare(data: dict) -> dict:
        """Valida las variables y añade la de baja si falta.

        El enlace de baja es obligatorio: si el usuario no lo pone en el
        cuerpo, se anexa al final en vez de rechazar la plantilla.
        """
        subject = data.get("subject", "")
        body_text = data.get("body_text", "")
        body_html = data.get("body_html")

        used = validate_template(subject, body_text, body_html)

        if REQUIRED_VARIABLE not in used:
            # El pie legal lo inyecta el envío, pero se deja constancia de que
            # esta plantilla depende de él.
            used = [*used, REQUIRED_VARIABLE]

        return {**data, "variables_used": used}


class SuppressionService:
    """Lista de no contactar (§14 del diseño)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SuppressionRepository(session)

    async def add(
        self,
        *,
        email: str | None = None,
        domain: str | None = None,
        reason: str,
        notes: str | None = None,
        source_email_id: uuid.UUID | None = None,
    ) -> SuppressionEntry:
        if not email and not domain:
            raise ValidationError(
                "Hay que indicar un correo o un dominio.", code="SUPPRESSION_TARGET_REQUIRED"
            )

        conditions = []
        if email:
            conditions.append(func.lower(SuppressionEntry.email) == email.lower())
        if domain:
            conditions.append(func.lower(SuppressionEntry.domain) == domain.lower())

        existing = await self.session.execute(
            select(SuppressionEntry).where(or_(*conditions)).limit(1)
        )
        found = existing.scalars().first()
        if found is not None:
            return found

        entry = SuppressionEntry(
            email=email.lower() if email else None,
            domain=domain.lower() if domain else None,
            reason=reason,
            notes=notes,
            source_email_id=source_email_id,
        )
        await self.repo.add(entry)
        logger.info("suppression_added", email=email, domain=domain, reason=reason)
        return entry

    async def remove(self, entry_id: uuid.UUID) -> None:
        entry = await self.repo.get(entry_id)
        if entry is None:
            raise NotFoundError.for_entity("suppression", entry_id)
        await self.repo.delete(entry)

    def build_list_query(self, *, q: str | None = None) -> Select[tuple[SuppressionEntry]]:
        stmt = select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc())
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(SuppressionEntry.email.ilike(pattern), SuppressionEntry.domain.ilike(pattern))
            )
        return stmt


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> AppSettings:
        result = await self.session.execute(select(AppSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = AppSettings(id=1, sender_name="")
            self.session.add(settings)
            await self.session.flush()
        return settings

    async def update(self, data: dict) -> AppSettings:
        settings = await self.get()

        # Arrancar la rampa de warm-up en cuanto se activa, si no tenía fecha.
        # La fecha es local: la rampa cuenta días de trabajo del usuario.
        if data.get("warmup_enabled") and settings.warmup_started_on is None:
            settings.warmup_started_on = local_now(settings).date()

        for key, value in data.items():
            setattr(settings, key, value)
        await self.session.flush()
        return settings
