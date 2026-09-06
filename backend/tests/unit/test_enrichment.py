"""Tests del enriquecimiento: extracción, verificación y señales."""

from __future__ import annotations

import pytest

from app.core.enums import VerificationStatus
from app.enrichment.email_verifier import (
    check_syntax,
    is_disposable,
    is_free_provider,
    verify_email,
)
from app.enrichment.extractors import (
    ExtractedContact,
    extract_all,
    extract_emails,
    extract_from_jsonld,
    extract_jsonld,
    extract_page_signals,
    extract_phones,
    extract_socials,
    is_role_email,
)
from app.enrichment.signal_detector import (
    LOW_RATING,
    LOW_REVIEWS,
    NO_CONTACT_FORM,
    NO_EMAIL,
    NO_SSL,
    NO_WEBSITE,
    NO_WHATSAPP,
    NOT_RESPONSIVE,
    OUTDATED_SITE,
    SITE_UNREACHABLE,
    SLOW_SITE,
    SOCIAL_ONLY,
    CompanyFacts,
    detect_signals,
)
from app.enrichment.website_crawler import CrawlResult, WebsiteCrawler

HTML_RESTAURANTE = """
<html><head>
  <title>Restaurante El Sabor - Comida italiana en Medellín</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Cocina italiana en El Poblado desde 2015.">
  <script type="application/ld+json">
  {"@type": "Restaurant", "name": "Restaurante El Sabor",
   "email": "reservas@elsabor.com", "telephone": "+57 604 444 1234",
   "sameAs": ["https://www.instagram.com/elsabor", "https://facebook.com/elsabor"]}
  </script>
</head><body>
  <a href="mailto:contacto@elsabor.com">Escríbenos</a>
  <a href="tel:+573001234567">Llámanos</a>
  <a href="https://wa.me/573001234567">WhatsApp</a>
  <a href="/contacto">Contacto</a>
  <form><input type="email" name="email"><textarea name="mensaje"></textarea></form>
  <footer>© 2019 El Sabor. Web por agencia@otrodominio.com</footer>
</body></html>
"""


# ------------------------------------------------------------------ emails


def test_extract_emails_finds_mailto_and_text() -> None:
    emails = extract_emails(HTML_RESTAURANTE, site_domain="elsabor.com")

    assert "contacto@elsabor.com" in emails
    assert "agencia@otrodominio.com" in emails


def test_extract_emails_prioritises_own_domain() -> None:
    """El email del propio negocio vale más que el de la agencia que le hizo
    la web, y ambos suelen convivir en el footer."""
    emails = extract_emails(HTML_RESTAURANTE, site_domain="elsabor.com")

    own = [e for e in emails if e.endswith("@elsabor.com")]
    assert emails.index(own[0]) < emails.index("agencia@otrodominio.com")


def test_extract_emails_filters_junk() -> None:
    html = """<p>logo@2x.png</p><p>info@example.com</p><p>real@empresa.co</p>"""
    emails = extract_emails(html)

    assert emails == ["real@empresa.co"]


def test_extract_emails_deduplicates() -> None:
    html = '<a href="mailto:a@b.com">a@b.com</a><p>A@B.COM</p>'
    assert extract_emails(html) == ["a@b.com"]


@pytest.mark.parametrize(
    ("email", "esperado"),
    [
        ("info@empresa.com", True),
        ("contacto@empresa.com", True),
        ("ventas@empresa.com", True),
        ("atencion.alcliente@empresa.com", True),
        ("juan.perez@empresa.com", False),
        ("maria@empresa.com", False),
    ],
)
def test_is_role_email(email: str, esperado: bool) -> None:
    assert is_role_email(email) is esperado


# ------------------------------------------------------------------ teléfonos y redes


def test_extract_phones_normalises_to_e164() -> None:
    phones = extract_phones(HTML_RESTAURANTE)
    assert "+573001234567" in phones


def test_extract_socials() -> None:
    socials = extract_socials(HTML_RESTAURANTE)

    assert socials["whatsapp"] == "https://wa.me/573001234567"
    assert "instagram" not in socials, "instagram solo está en el JSON-LD, no en un <a>"


# ------------------------------------------------------------------ JSON-LD


def test_jsonld_is_the_best_source_when_present() -> None:
    """Cuando existe, es el propio negocio declarando sus datos."""
    contact = extract_from_jsonld(extract_jsonld(HTML_RESTAURANTE))

    assert "reservas@elsabor.com" in contact.emails
    assert "+576044441234" in contact.phones
    assert contact.company_name == "Restaurante El Sabor"
    assert contact.socials["instagram"] == "https://www.instagram.com/elsabor"


def test_jsonld_ignores_non_business_blocks() -> None:
    html = """<script type="application/ld+json">
    {"@type": "BreadcrumbList", "email": "no@deberia.salir"}</script>"""

    assert extract_from_jsonld(extract_jsonld(html)).emails == []


def test_jsonld_survives_malformed_json() -> None:
    html = '<script type="application/ld+json">{roto,,}</script>'
    assert extract_jsonld(html) == []


# ------------------------------------------------------------------ señales de página


def test_page_signals() -> None:
    signals = extract_page_signals(HTML_RESTAURANTE)

    assert signals.has_viewport_meta is True
    assert signals.has_contact_form is True
    assert signals.copyright_year == 2019


def test_page_without_viewport_is_not_responsive() -> None:
    sin_viewport = extract_page_signals("<html><head></head><body></body></html>")
    assert sin_viewport.has_viewport_meta is False


def test_extract_all_merges_every_source() -> None:
    contact = extract_all(HTML_RESTAURANTE, "https://elsabor.com")

    assert "contacto@elsabor.com" in contact.emails
    assert "reservas@elsabor.com" in contact.emails, "el JSON-LD también aporta"
    assert contact.socials["whatsapp"]
    assert contact.socials["instagram"]


# ------------------------------------------------------------------ verificación de email


@pytest.mark.parametrize(
    ("email", "esperado"),
    [
        ("juan@empresa.com", True),
        ("juan.perez+tag@sub.empresa.co", True),
        ("sin-arroba.com", False),
        ("@empresa.com", False),
        ("juan@", False),
        ("", False),
    ],
)
def test_check_syntax(email: str, esperado: bool) -> None:
    assert check_syntax(email) is esperado


def test_disposable_and_free_detection() -> None:
    assert is_disposable("x@mailinator.com") is True
    assert is_disposable("x@empresa.com") is False
    assert is_free_provider("x@gmail.com") is True
    assert is_free_provider("x@empresa.com") is False


async def test_verify_rejects_invalid_syntax_without_dns() -> None:
    status, confidence = await verify_email("no-es-un-email")

    assert status is VerificationStatus.INVALID
    assert confidence == 0


async def test_verify_rejects_disposable() -> None:
    status, _ = await verify_email("prueba@mailinator.com")
    assert status is VerificationStatus.INVALID


async def test_verify_never_claims_more_than_mx_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """No hacemos SMTP probing, así que `VERIFIED` no se alcanza nunca por
    esta vía: afirmarlo sería mentirle al usuario."""

    async def fake_mx(domain: str, timeout_s: float = 5.0) -> bool:
        return True

    monkeypatch.setattr("app.enrichment.email_verifier.has_mx_record", fake_mx)

    status, confidence = await verify_email("juan.perez@empresa.com")

    assert status is VerificationStatus.MX_OK
    assert confidence == 80


async def test_role_and_free_emails_score_lower(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_mx(domain: str, timeout_s: float = 5.0) -> bool:
        return True

    monkeypatch.setattr("app.enrichment.email_verifier.has_mx_record", fake_mx)

    _, personal = await verify_email("juan@empresa.com")
    _, generico = await verify_email("info@empresa.com")
    _, gratuito = await verify_email("juan@gmail.com")

    assert personal > generico, "un email nominal vale más que uno de rol"
    assert personal > gratuito, "dominio propio vale más que Gmail"


async def test_domain_without_mx_stays_syntax_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_mx(domain: str, timeout_s: float = 5.0) -> bool:
        return False

    monkeypatch.setattr("app.enrichment.email_verifier.has_mx_record", fake_mx)

    status, confidence = await verify_email("juan@dominio-inexistente.com")

    assert status is VerificationStatus.SYNTAX_OK
    assert confidence < 50


# ------------------------------------------------------------------ señales


def test_no_website_is_the_headline_signal() -> None:
    signals = {s.key for s in detect_signals(CompanyFacts())}
    assert NO_WEBSITE in signals


def test_instagram_as_website_means_no_website() -> None:
    """Un negocio cuyo 'sitio web' es su Instagram es justo el prospecto que
    busca quien vende desarrollo web."""
    signals = {
        s.key for s in detect_signals(CompanyFacts(website="https://instagram.com/mirestaurante"))
    }

    assert SOCIAL_ONLY in signals
    assert NO_WEBSITE in signals


def test_technical_signals_from_crawl() -> None:
    crawl = CrawlResult(
        url="http://viejo.com",
        reachable=True,
        uses_https=False,
        response_ms=4200,
        contact=ExtractedContact(
            has_viewport_meta=False, has_contact_form=False, copyright_year=2016
        ),
    )

    signals = {s.key: s for s in detect_signals(CompanyFacts(website="http://viejo.com"), crawl)}

    assert NO_SSL in signals
    assert NOT_RESPONSIVE in signals
    assert SLOW_SITE in signals
    assert signals[SLOW_SITE].value == {"response_ms": 4200}
    assert OUTDATED_SITE in signals
    assert signals[OUTDATED_SITE].value == {"copyright_year": 2016}
    assert NO_CONTACT_FORM in signals


def test_modern_site_raises_no_technical_signals() -> None:
    crawl = CrawlResult(
        url="https://moderno.com",
        reachable=True,
        uses_https=True,
        response_ms=350,
        contact=ExtractedContact(
            has_viewport_meta=True, has_contact_form=True, copyright_year=2026
        ),
    )

    signals = {s.key for s in detect_signals(CompanyFacts(website="https://moderno.com"), crawl)}

    assert NO_SSL not in signals
    assert NOT_RESPONSIVE not in signals
    assert SLOW_SITE not in signals
    assert OUTDATED_SITE not in signals


def test_unreachable_site_signal() -> None:
    crawl = CrawlResult(url="https://caido.com", reachable=False, error="timeout")
    signals = {s.key for s in detect_signals(CompanyFacts(website="https://caido.com"), crawl)}

    assert SITE_UNREACHABLE in signals


def test_robots_blocked_is_not_an_unreachable_site() -> None:
    """No poder rastrear no significa que el sitio esté caído. Marcarlo como
    caído sería una señal falsa que acabaría en un correo equivocado."""
    crawl = CrawlResult(url="https://x.com", reachable=False, robots_blocked=True)
    signals = {s.key for s in detect_signals(CompanyFacts(website="https://x.com"), crawl)}

    assert SITE_UNREACHABLE not in signals


def test_reputation_signals() -> None:
    signals = {s.key for s in detect_signals(CompanyFacts(rating=3.2, reviews_count=5))}

    assert LOW_RATING in signals
    assert LOW_REVIEWS in signals


def test_contactability_signals() -> None:
    signals = {s.key for s in detect_signals(CompanyFacts(socials={"instagram": "x"}))}

    assert NO_EMAIL in signals
    assert NO_WHATSAPP in signals


# ------------------------------------------------------------------ crawler


def test_contact_links_only_internal_and_relevant() -> None:
    html = """
    <a href="/contacto">Contacto</a>
    <a href="/nosotros">Nosotros</a>
    <a href="/blog/receta">Blog</a>
    <a href="https://otrositio.com/contacto">Externo</a>
    <a href="mailto:x@y.com">Mail</a>
    """
    links = WebsiteCrawler._contact_links(html, "https://elsabor.com")

    assert "https://elsabor.com/contacto" in links
    assert "https://elsabor.com/nosotros" in links
    assert all("otrositio.com" not in link for link in links), "no salir del dominio"
    assert all("/blog/" not in link for link in links)


def test_user_agent_is_ascii_only() -> None:
    """Regresión: una tilde en el User-Agent hace que httpx lance
    UnicodeEncodeError al construir el cliente, y ningún crawl llega a
    ejecutarse. Falló contra sitios reales antes de detectarse."""
    from app.enrichment.website_crawler import USER_AGENT

    USER_AGENT.encode("ascii")  # no debe lanzar


def test_extract_emails_unescapes_embedded_json() -> None:
    """Regresión: los sitios llevan JSON incrustado donde `>` viaja como
    `\\u003e`. Sin desescapar, el email se captura como
    `u003ejustocolombia@getjusto.com`. Detectado con datos reales de un
    restaurante de El Poblado durante la verificación de la Fase 2."""
    html = (
        '<script>{"html":"\\u003ca href=\\"mailto:justocolombia@getjusto.com\\"'
        '\\u003ejustocolombia@getjusto.com\\u003c/a\\u003e"}</script>'
    )

    emails = extract_emails(html)

    assert "justocolombia@getjusto.com" in emails
    assert not any(e.startswith("u003e") for e in emails), "no debe colarse el escape"
