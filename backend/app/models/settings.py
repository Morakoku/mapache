"""Configuración de la aplicación en base de datos (fila única).

Lo que puede cambiar sin desplegar vive aquí: identidad del remitente,
límites de envío, proveedor de descubrimiento, modelo de IA. Lo que es
infraestructura (URL de la base, clave de cifrado) sigue en el entorno.
"""

from __future__ import annotations

import json
import uuid
from datetime import time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AIProvider, SerpProvider
from app.models.base import Base, TimestampMixin, pg_enum

# Curva de warm-up hasta los 100 correos diarios (§14 del diseño). Arrancar en
# 100 con un dominio nuevo es la forma más rápida de acabar en spam de forma
# permanente.
WARMUP_CURVE: tuple[tuple[int, int], ...] = (
    (3, 20),
    (7, 35),
    (14, 50),
    (21, 75),
)
WARMUP_TARGET = 100

# Pesos por defecto del prospect score (Módulo 8). Suman 1.0.
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "fit": 0.30,
    "opportunity": 0.25,
    "contactability": 0.20,
    "data_quality": 0.10,
    "intent": 0.10,
    "timing": 0.05,
}
DEFAULT_SCORE_WEIGHTS_JSON = json.dumps(DEFAULT_SCORE_WEIGHTS)


def warmup_limit(day: int) -> int:
    """Límite del día N de la rampa (día 1 = primer día de envíos)."""
    for last_day, limit in WARMUP_CURVE:
        if day <= last_day:
            return limit
    return WARMUP_TARGET


class AppSettings(Base, TimestampMixin):
    __tablename__ = "app_settings"

    # Fila única forzada por CHECK: la configuración global no admite
    # duplicados y así no hace falta lógica defensiva al leerla.
    # `autoincrement=False`: SQLAlchemy convierte por defecto una PK entera en
    # SERIAL, y una secuencia en una tabla de una sola fila no tiene sentido.
    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False, server_default="1"
    )

    # --- identidad del remitente ---
    sender_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("email_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    reply_to: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    tracking_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Obligatoria en el pie del correo por normativa.
    address_of_sender: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- localización (Colombia por defecto) ---
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="CO")
    phone_region: Mapped[str] = mapped_column(String(2), nullable=False, server_default="CO")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="COP")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, server_default="es-CO")
    timezone: Mapped[str] = mapped_column(
        String(60), nullable=False, server_default="America/Bogota"
    )

    # --- límites de envío ---
    daily_send_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    hourly_send_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    min_seconds_between: Mapped[int] = mapped_column(Integer, nullable=False, server_default="45")
    send_window_start: Mapped[time] = mapped_column(Time, nullable=False, server_default="08:00")
    send_window_end: Mapped[time] = mapped_column(Time, nullable=False, server_default="18:00")
    skip_weekends: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    warmup_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    warmup_started_on: Mapped[Any | None] = mapped_column(Date, nullable=True)

    # --- IA ---
    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    ai_provider: Mapped[AIProvider] = mapped_column(
        pg_enum(AIProvider, "ai_provider"),
        nullable=False,
        server_default=AIProvider.ANTHROPIC.value,
    )
    # Cifrada con Fernet, igual que las contraseñas SMTP: un dump de Postgres
    # no puede llevarse la clave de nadie. Nunca sale por la API.
    ai_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str] = mapped_column(
        String(60), nullable=False, server_default="claude-opus-5"
    )
    ai_tone: Mapped[str] = mapped_column(String(20), nullable=False, server_default="usted")

    # --- descubrimiento ---
    discovery_provider: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="google_maps_scraper"
    )
    fallback_discovery_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scraper_concurrency: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="2"
    )
    scraper_delay_ms_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1200"
    )
    scraper_delay_ms_max: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3500"
    )
    scraper_headless: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    google_places_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Buscar perfiles de Instagram y LinkedIn se hace contra el índice de un
    # buscador, por su API oficial: las redes prohíben el rastreo directo pero
    # dejan que Google y Bing las indexen.
    serp_provider: Mapped[SerpProvider] = mapped_column(
        pg_enum(SerpProvider, "serp_provider"),
        default=SerpProvider.GOOGLE_CSE,
        server_default=SerpProvider.GOOGLE_CSE.value,
        nullable=False,
    )
    serp_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Solo lo usa Google Custom Search: Brave busca en toda la web sin motor.
    serp_engine_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    apify_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- scoring ---
    # String plano, no `text()`: dentro de `text()` los `:` de un literal JSON
    # se interpretan como marcadores de bind param y SQLAlchemy los sustituye
    # por NULL, produciendo un default que Postgres rechaza.
    score_weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=DEFAULT_SCORE_WEIGHTS_JSON,
    )

    # Interruptor global. Para todas las automatizaciones sin desconfigurar
    # nada: es el botón de pánico del Módulo 18.
    automations_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)
