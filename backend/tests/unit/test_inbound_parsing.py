"""Fase 6 — lectura de lo que llega al buzón.

Se parten mensajes MIME reales: una respuesta normal, un "estoy de vacaciones"
y un informe de no entrega. Distinguirlos es lo que decide si un prospecto
avanza de etapa, si se le vuelve a escribir o si su dirección se bloquea.
"""

from __future__ import annotations

import email

from app.mail.smtp import _to_inbound
from app.services.inbox_svc import _extract_failed_recipient, _strip_quoted

REPLY = """From: Camila Restrepo <gerente@lafinca.co>
To: Daniel Ruiz <daniel@midominio.co>
Subject: Re: Una idea para Restaurante La Finca
Date: Mon, 27 Jul 2026 10:15:00 -0500
Message-ID: <respuesta-1@lafinca.co>
In-Reply-To: <original-1@midominio.co>
References: <original-1@midominio.co>
Content-Type: text/plain; charset="utf-8"

Hola Daniel, sí me interesa. ¿Cuánto costaría?

El lun, 27 jul 2026 a las 9:00, Daniel Ruiz escribió:
> Hola Camila, vi La Finca en Medellín...
> ¿Te sirve que te lo cuente en dos líneas?
"""

OUT_OF_OFFICE = """From: Camila Restrepo <gerente@lafinca.co>
To: daniel@midominio.co
Subject: Fuera de la oficina: Una idea para Restaurante La Finca
Date: Mon, 27 Jul 2026 10:15:00 -0500
Message-ID: <ooo-1@lafinca.co>
In-Reply-To: <original-1@midominio.co>
Auto-Submitted: auto-replied
Content-Type: text/plain; charset="utf-8"

Estoy fuera hasta el 5 de agosto. Escribiré a mi regreso.
"""

HARD_BOUNCE = """From: Mail Delivery Subsystem <MAILER-DAEMON@correo.co>
To: daniel@midominio.co
Subject: Undelivered Mail Returned to Sender
Date: Mon, 27 Jul 2026 09:01:00 -0500
Message-ID: <dsn-1@correo.co>
X-Failed-Recipients: noexiste@lafinca.co
Content-Type: multipart/report; report-type=delivery-status; boundary="XX"

--XX
Content-Type: text/plain; charset="utf-8"

This is the mail system at host correo.co.

<noexiste@lafinca.co>: host mx.lafinca.co said: 550 5.1.1 User unknown

--XX--
"""

SOFT_BOUNCE = """From: postmaster@correo.co
To: daniel@midominio.co
Subject: Delivery delayed
Date: Mon, 27 Jul 2026 09:01:00 -0500
Message-ID: <dsn-2@correo.co>
Content-Type: multipart/report; report-type=delivery-status; boundary="YY"

--YY
Content-Type: text/plain; charset="utf-8"

<lleno@lafinca.co>: 452 4.2.2 Mailbox full, retrying

--YY--
"""


def _parse(raw: str, uid: str = "1"):  # type: ignore[no-untyped-def]
    return _to_inbound(email.message_from_bytes(raw.encode()), uid)


def test_respuesta_normal_no_es_automatica_ni_rebote() -> None:
    inbound = _parse(REPLY)
    assert inbound.from_email == "gerente@lafinca.co"
    assert inbound.in_reply_to == "<original-1@midominio.co>"
    assert inbound.is_auto_reply is False
    assert inbound.is_bounce is False
    assert "me interesa" in inbound.body_text


def test_fuera_de_la_oficina_se_marca_como_automatica() -> None:
    """Contarla como respuesta movería el prospecto a "Respondió" sin que
    nadie haya leído nada."""
    inbound = _parse(OUT_OF_OFFICE)
    assert inbound.is_auto_reply is True
    assert inbound.is_bounce is False


def test_rebote_duro_se_distingue_del_blando() -> None:
    duro = _parse(HARD_BOUNCE)
    assert duro.is_bounce is True
    assert duro.bounce_type == "hard"

    blando = _parse(SOFT_BOUNCE)
    assert blando.is_bounce is True
    assert blando.bounce_type == "soft"


def test_direccion_que_rebota_sale_de_la_cabecera() -> None:
    inbound = _parse(HARD_BOUNCE)
    assert _extract_failed_recipient(inbound) == "noexiste@lafinca.co"


def test_la_cita_del_mensaje_anterior_se_recorta() -> None:
    """Sin esto, el resumen de la bandeja mostraría nuestro propio correo."""
    limpio = _strip_quoted(_parse(REPLY).body_text)
    assert "me interesa" in limpio
    assert "escribió:" not in limpio
    assert ">" not in limpio


def test_recorte_no_vacia_un_mensaje_que_es_solo_cita() -> None:
    """Si alguien responde escribiendo dentro de la cita, mejor guardar de más
    que perder el mensaje."""
    solo_cita = "> Hola Camila, ¿te sirve?"
    assert _strip_quoted(solo_cita) == solo_cita
