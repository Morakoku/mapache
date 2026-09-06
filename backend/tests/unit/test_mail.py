"""Fase 4 — renderizado, construcción del MIME y guardrails.

Las barreras de §14 se prueban aquí una a una: son lo que separa "correo
comercial" de "spam", y un fallo silencioso en ellas no lo detecta nadie hasta
que el dominio está quemado.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.core.exceptions import ValidationError
from app.mail import builder, renderer
from app.mail.base import OutboundMessage
from app.mail.guardrails import effective_daily_limit, is_within_window
from app.models.settings import AppSettings, warmup_limit

# ------------------------------------------------------------------ renderer


def test_render_sustituye_variables() -> None:
    result = renderer.render(
        subject="Hola {{company_name}}",
        body_text="Vi que {{company_name}} está en {{city}}.",
        context={"company_name": "Café Bogotá", "city": "Bogotá"},
    )
    assert result.subject == "Hola Café Bogotá"
    assert result.body_text == "Vi que Café Bogotá está en Bogotá."
    assert result.missing == []


def test_variable_sin_valor_no_deja_el_andamiaje_visible() -> None:
    """Mejor una frase coja que un `{{city}}` delante del prospecto."""
    result = renderer.render(
        subject="Hola {{company_name}}",
        body_text="Trabajáis en {{city}}, ¿verdad?",
        context={"company_name": "Café", "city": None},
    )
    assert "{{" not in result.body_text
    assert result.missing == ["city"]


def test_asunto_colapsa_espacios_de_variables_vacias() -> None:
    result = renderer.render(
        subject="Propuesta para  {{company_name}}  hoy",
        body_text="x",
        context={"company_name": ""},
    )
    assert result.subject == "Propuesta para hoy"


def test_plantilla_con_variable_desconocida_falla_al_guardar() -> None:
    with pytest.raises(ValidationError) as exc:
        renderer.validate_template("Hola {{compnay_name}}", "cuerpo")
    assert exc.value.code == "UNKNOWN_TEMPLATE_VARIABLE"


def test_first_name_cae_al_nombre_completo_si_no_hay_pila() -> None:
    context = renderer.build_context(
        company_name="Café", sender_name="Daniel", contact_name="María Fernanda Ruiz"
    )
    assert context["first_name"] == "María"


def test_signal_summary_es_legible() -> None:
    context = renderer.build_context(
        company_name="Café",
        sender_name="Daniel",
        signals=["NO_WEBSITE", "NO_ONLINE_ORDERING"],
    )
    assert " y " in context["signal_summary"]


# ------------------------------------------------------------------ builder


def test_links_iguales_comparten_token() -> None:
    """Dos botones al mismo sitio son el mismo interés, no dos clicks."""
    text, html, links = builder.rewrite_links(
        body_text="Mira https://ejemplo.co/precios y también https://ejemplo.co/precios",
        body_html='<a href="https://ejemplo.co/precios">a</a><a href="https://ejemplo.co/otra">b</a>',
        base_url="https://crm.test",
    )
    assert len(links) == 2
    assert "https://ejemplo.co/precios" not in text
    assert html is not None and "https://ejemplo.co/otra" not in html


def test_link_de_baja_no_se_rastrea() -> None:
    """Rastrear la baja sería medir a quien pidió que lo dejaras en paz."""
    unsubscribe = "https://crm.test/tracking/unsubscribe/abc"
    text, _, links = builder.rewrite_links(
        body_text=f"Baja: {unsubscribe}",
        body_html=None,
        base_url="https://crm.test",
        skip_urls={unsubscribe},
    )
    assert unsubscribe in text
    assert links == []


def test_pixel_entra_antes_del_cierre_del_body() -> None:
    html = builder.inject_pixel("<html><body><p>hola</p></body></html>", "https://crm.test/p.gif")
    assert html.index("crm.test/p.gif") < html.index("</body>")


def test_pixel_crea_html_minimo_si_no_habia() -> None:
    html = builder.inject_pixel(None, "https://crm.test/p.gif")
    assert "<img" in html


def test_footer_lleva_identidad_direccion_y_baja() -> None:
    text, html = builder.build_footer(
        sender_name="Daniel Ruiz",
        sender_address="Calle 10 #43-20, Medellín",
        unsubscribe_url="https://crm.test/u/1",
    )
    for fragment in ("Daniel Ruiz", "Medellín", "https://crm.test/u/1"):
        assert fragment in text
        assert fragment in html


def test_mime_lleva_unsubscribe_de_un_clic() -> None:
    """RFC 8058: sin estas dos cabeceras, Gmail penaliza al remitente."""
    mime = builder.build_mime(
        OutboundMessage(
            from_email="yo@midominio.co",
            from_name="Daniel",
            to_email="prospecto@empresa.co",
            subject="Hola",
            body_text="cuerpo",
            unsubscribe_url="https://crm.test/u/1",
        ),
        sending_domain="midominio.co",
    )
    assert mime["List-Unsubscribe"] == "<https://crm.test/u/1>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mime["Auto-Submitted"] == "no"
    assert "@midominio.co" in mime["Message-ID"]


def test_mime_declara_al_remitente_real() -> None:
    """No se oculta la identidad del remitente: va en From, tal cual."""
    mime = builder.build_mime(
        OutboundMessage(
            from_email="yo@midominio.co",
            from_name="Daniel Ruiz",
            to_email="prospecto@empresa.co",
            subject="Hola",
            body_text="cuerpo",
        ),
        sending_domain="midominio.co",
    )
    assert "yo@midominio.co" in mime["From"]
    assert "Daniel Ruiz" in mime["From"]


def test_unsubscribe_no_se_codifica_en_palabras_rfc2047() -> None:
    """El plegado por defecto rompería la baja de un clic.

    Una URL larga sin espacios se parte en `=?utf-8?q?=3Chttp...`, y ni Gmail
    ni Outlook reconocen ahí un `List-Unsubscribe`.
    """
    url = "https://crm.midominio.co/tracking/unsubscribe/8168b081-4a76-4e02-be84-68cce56e02ab"
    mime = builder.build_mime(
        OutboundMessage(
            from_email="yo@midominio.co",
            from_name="Daniel",
            to_email="prospecto@empresa.co",
            subject="Hola",
            body_text="cuerpo",
            unsubscribe_url=url,
        ),
        sending_domain="midominio.co",
    )
    raw = mime.as_string()
    assert f"List-Unsubscribe: <{url}>" in raw
    assert "=?utf-8?q?=3C" not in raw


def test_asunto_con_acentos_sigue_codificado() -> None:
    """Desactivar el plegado no debe dejar UTF-8 crudo en la cabecera."""
    mime = builder.build_mime(
        OutboundMessage(
            from_email="yo@midominio.co",
            from_name="Daniel",
            to_email="prospecto@empresa.co",
            subject="Una idea para tu página",
            body_text="cuerpo",
        ),
        sending_domain="midominio.co",
    )
    raw = mime.as_string()
    assert "=?utf-8?" in raw
    assert "página" not in raw.split("\n\n", 1)[0]


def test_references_encadena_sin_duplicar() -> None:
    assert builder.build_references("<a@x>", "<b@x>") == "<a@x> <b@x>"
    assert builder.build_references("<a@x> <b@x>", "<b@x>") == "<a@x> <b@x>"
    assert builder.build_references(None, None) is None


def test_references_se_recorta_para_no_pasar_los_998_caracteres() -> None:
    previous = " ".join(f"<msg{i}@x.co>" for i in range(30))
    chained = builder.build_references(previous, "<nuevo@x.co>")
    assert chained is not None
    ids = chained.split()
    assert len(ids) == 10
    assert ids[0] == "<msg0@x.co>"
    assert ids[-1] == "<nuevo@x.co>"
    assert len(f"References: {chained}") < 998


# ------------------------------------------------------------------ guardrails


def _settings(**kw: object) -> AppSettings:
    defaults: dict[str, object] = {
        "id": 1,
        "sender_name": "Daniel",
        "daily_send_limit": 100,
        "hourly_send_limit": 20,
        "min_seconds_between": 45,
        "send_window_start": time(8, 0),
        "send_window_end": time(18, 0),
        "skip_weekends": True,
        "warmup_enabled": False,
        "warmup_started_on": None,
        "automations_paused": False,
    }
    return AppSettings(**{**defaults, **kw})  # type: ignore[arg-type]


def test_curva_de_warmup() -> None:
    assert warmup_limit(1) == 20
    assert warmup_limit(3) == 20
    assert warmup_limit(4) == 35
    assert warmup_limit(14) == 50
    assert warmup_limit(21) == 75
    assert warmup_limit(22) == 100


def test_warmup_recorta_el_limite_diario() -> None:
    settings = _settings(warmup_enabled=True, warmup_started_on=date(2026, 7, 20))
    # Día 1 de la rampa.
    assert effective_daily_limit(settings, date(2026, 7, 20)) == 20
    # Día 8.
    assert effective_daily_limit(settings, date(2026, 7, 27)) == 50
    # Pasada la rampa manda el límite configurado.
    assert effective_daily_limit(settings, date(2026, 9, 1)) == 100


def test_warmup_nunca_supera_el_limite_configurado() -> None:
    settings = _settings(
        daily_send_limit=30, warmup_enabled=True, warmup_started_on=date(2026, 1, 1)
    )
    assert effective_daily_limit(settings, date(2026, 12, 31)) == 30


def test_ventana_horaria_y_fin_de_semana() -> None:
    settings = _settings()
    lunes_10 = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    lunes_22 = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
    sabado_10 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

    assert is_within_window(settings, lunes_10) is True
    assert is_within_window(settings, lunes_22) is False
    assert is_within_window(settings, sabado_10) is False
    assert is_within_window(_settings(skip_weekends=False), sabado_10) is True
