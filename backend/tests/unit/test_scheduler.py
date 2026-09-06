"""Fase 7 — cálculo de huecos de envío.

El planificador es lo que decide a qué hora sale cada seguimiento. Si se
equivoca, los correos salen de madrugada o en domingo, que es exactamente lo
que hace que un remitente parezca una máquina.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.core.exceptions import ValidationError
from app.models.settings import AppSettings
from app.services.sequence_svc import next_valid_slot


def _settings(**kw: object) -> AppSettings:
    defaults: dict[str, object] = {
        "id": 1,
        "sender_name": "Daniel",
        "timezone": "America/Bogota",  # UTC-5
        "send_window_start": time(9, 0),
        "send_window_end": time(18, 0),
        "skip_weekends": True,
        "daily_send_limit": 100,
        "hourly_send_limit": 20,
        "min_seconds_between": 45,
        "warmup_enabled": False,
        "automations_paused": False,
    }
    return AppSettings(**{**defaults, **kw})  # type: ignore[arg-type]


def _local(moment: datetime, settings: AppSettings) -> datetime:
    from app.mail.guardrails import local_now

    return local_now(settings, moment)


def test_dentro_de_la_ventana_no_se_mueve() -> None:
    settings = _settings()
    # Martes 15:00 en Bogotá = 20:00 UTC.
    deseado = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)
    assert next_valid_slot(deseado, settings=settings) == deseado


def test_antes_de_la_ventana_espera_a_la_apertura() -> None:
    settings = _settings()
    # Martes 06:00 en Bogotá.
    deseado = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    slot = next_valid_slot(deseado, settings=settings)
    assert _local(slot, settings).hour == 9
    assert _local(slot, settings).day == 28


def test_despues_de_la_ventana_pasa_al_dia_siguiente() -> None:
    settings = _settings()
    # Martes 23:00 en Bogotá (miércoles 04:00 UTC).
    deseado = datetime(2026, 7, 29, 4, 0, tzinfo=UTC)
    slot = next_valid_slot(deseado, settings=settings)
    local = _local(slot, settings)
    assert (local.day, local.hour) == (29, 9)


def test_el_fin_de_semana_se_salta() -> None:
    """Un correo comercial en sábado se lee como automatizado."""
    settings = _settings()
    # Sábado 1 de agosto de 2026, 10:00 en Bogotá.
    sabado = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
    local = _local(next_valid_slot(sabado, settings=settings), settings)
    assert local.weekday() == 0  # lunes
    assert local.hour == 9


def test_se_puede_permitir_el_fin_de_semana() -> None:
    settings = _settings()
    sabado = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
    slot = next_valid_slot(sabado, settings=settings, skip_weekends=False)
    assert slot == sabado


def test_la_ventana_del_paso_pisa_a_la_global() -> None:
    """Permite que el último recordatorio salga a otra hora que el primero."""
    settings = _settings()
    # Martes 07:30 en Bogotá; el paso abre a las 7:00, la global a las 9:00.
    deseado = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)
    slot = next_valid_slot(
        deseado, settings=settings, window_start=time(7, 0), window_end=time(12, 0)
    )
    assert slot == deseado


def test_nunca_adelanta_un_envio() -> None:
    """Adelantar lo que el usuario vio en el calendario sería mentirle."""
    settings = _settings()
    deseado = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)  # 06:00 local
    assert next_valid_slot(deseado, settings=settings) > deseado


def test_ventana_imposible_falla_en_vez_de_arrastrar() -> None:
    settings = _settings(send_window_start=time(9, 0), send_window_end=time(8, 0))
    with pytest.raises(ValidationError) as exc:
        next_valid_slot(datetime(2026, 7, 28, 20, 0, tzinfo=UTC), settings=settings)
    assert exc.value.code == "NO_VALID_SEND_SLOT"


def test_el_calculo_es_en_hora_local_no_utc() -> None:
    """Con la ventana evaluada en UTC, 08:00-18:00 en Colombia serían las
    03:00-13:00 reales."""
    bogota = _settings()
    madrid = _settings(timezone="Europe/Madrid")

    # 14:00 UTC = 09:00 en Bogotá (válido) y 16:00 en Madrid (válido también).
    momento = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
    assert next_valid_slot(momento, settings=bogota) == momento
    assert next_valid_slot(momento, settings=madrid) == momento

    # 07:00 UTC = 02:00 en Bogotá (fuera) y 09:00 en Madrid (justo dentro).
    temprano = datetime(2026, 7, 28, 7, 0, tzinfo=UTC)
    assert next_valid_slot(temprano, settings=bogota) > temprano
    assert next_valid_slot(temprano, settings=madrid) == temprano
