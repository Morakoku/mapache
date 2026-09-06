"""Canonicalización de URLs y dominios.

`https://www.Empresa.com/inicio?utm_source=maps#top` y `empresa.com` son el
mismo sitio. Sin canonicalizar, el dedupe por dominio no sirve de nada y el
crawler visita la misma web varias veces.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

_SCHEME_LIKE_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:(?P<rest>.*)$", flags=re.IGNORECASE)

# Extractor sin acceso a red: usa la lista de sufijos empaquetada. Evita una
# descarga sorpresa en el primer arranque y hace los tests deterministas.
_extract = tldextract.TLDExtract(suffix_list_urls=())

# Parámetros de tracking que no cambian el contenido de la página.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "_ga",
    }
)

# Dominios de redes sociales y agregadores: no son el sitio web propio de la
# empresa, así que no cuentan como "tiene web".
SOCIAL_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "youtube.com",
        "wa.me",
        "whatsapp.com",
        "api.whatsapp.com",
        "t.me",
        "pinterest.com",
        "linktr.ee",
        "beacons.ai",
        "bento.me",
        "bio.link",
        "taplink.cc",
        "solo.to",
        "campsite.bio",
        "google.com",
        "sites.google.com",
        "business.site",
        "negocio.site",
        "wixsite.com",
        "blogspot.com",
        "tripadvisor.com",
        "rappi.com",
        "ubereats.com",
        "didi-food.com",
    }
)


def normalize_url(raw: str | None) -> str | None:
    """URL absoluta, sin fragmento y sin parámetros de tracking."""
    if not raw or not raw.strip():
        return None

    candidate = raw.strip()
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif "://" not in candidate:
        # Distinguir `mailto:a@b.com` (esquema que no queremos) de
        # `empresa.com:8080` (host con puerto). Tras los dos puntos, un
        # puerto siempre empieza por dígito; un esquema, no.
        scheme_match = _SCHEME_LIKE_RE.match(candidate)
        if scheme_match and not scheme_match.group("rest")[:1].isdigit():
            return None
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None

    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None

    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
    )
    path = parts.path.rstrip("/") or "/"

    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def extract_domain(raw: str | None) -> str | None:
    """Dominio registrable, sin `www`. `https://www.a.empresa.com.co/x` -> `empresa.com.co`.

    Se usa el dominio registrable (no el host completo) para que el subdominio
    no genere falsos negativos en el dedupe.
    """
    if not raw:
        return None

    candidate = raw.strip()
    if "://" not in candidate and not candidate.startswith("//"):
        candidate = f"https://{candidate}"

    result = _extract(candidate)
    if not result.domain or not result.suffix:
        return None

    return f"{result.domain}.{result.suffix}".lower()


# Directorios de empresas. No son redes sociales, pero tampoco son la web del
# negocio: son una ficha que alguien creó dentro del sitio de otro. Tratarlas
# como web propia tiene dos consecuencias malas: la señal «sin web» no salta
# —justo la que más vale para vender desarrollo web— y al rastrear la ficha se
# importan las redes del directorio como si fueran las de la empresa.
DIRECTORY_DOMAINS = frozenset(
    {
        "aiyellow.com",
        "paginasamarillas.com",
        "paginasamarillas.com.co",
        "paginasblancas.com.co",
        "cylex.com.co",
        "cylex-colombia.com",
        "opendi.co",
        "opendi.com.co",
        "infoisinfo.com.co",
        "yellow.place",
        "yelp.com",
        "foursquare.com",
        "guiacolombia.com.co",
        "quehubo.com.co",
        "pedidosya.com",
        "pedidosya.com.co",
        "domicilios.com",
        "degusta.com.co",
        "queresto.com",
        "menudino.com",
        "doctoralia.co",
        "doctoralia.com.co",
        "computrabajo.com.co",
        "einforma.co",
        "emis.com",
        "dateas.com",
        "rues.org.co",
    }
)


def _matches_domain_set(domain: str, candidates: frozenset[str]) -> bool:
    if domain in candidates:
        return True
    # `business.site` y similares aparecen como subdominio del negocio.
    return any(domain.endswith(f".{candidate}") for candidate in candidates)


def is_social_url(raw: str | None) -> bool:
    """¿Apunta a una red social o agregador en vez de a un sitio propio?"""
    domain = extract_domain(raw)
    if not domain:
        return False
    return _matches_domain_set(domain, SOCIAL_DOMAINS)


def is_directory_url(raw: str | None) -> bool:
    """¿Es una ficha en un directorio de empresas?"""
    domain = extract_domain(raw)
    if not domain:
        return False
    return _matches_domain_set(domain, DIRECTORY_DOMAINS)


def is_own_website(raw: str | None) -> bool:
    """¿Es la web del negocio, y no un perfil ajeno donde aparece listado?"""
    if not raw:
        return False
    return not is_social_url(raw) and not is_directory_url(raw)


# Segmentos de ruta que no son el nombre de nadie. `linkedin.com/company/x`
# y `tiktok.com/@x` llevan al mismo sitio conceptual: el perfil es `x`.
_HANDLE_NOISE = frozenset(
    {
        "company",
        "in",
        "pub",
        "school",
        "showcase",
        "p",
        "profile.php",
        "pages",
        "channel",
        "c",
        "user",
        "@",
    }
)


# Botones de «compartir esta página». Viven en el dominio de la red y pasan
# cualquier detector de plataforma, pero no son el perfil de nadie: el enlace
# de Twitter de una panadería que en realidad dice «tuitea esta web» es un dato
# falso, y en el CRM un dato falso es peor que ningún dato.
_SHARE_SEGMENTS = frozenset(
    {
        "share",
        "sharer",
        "sharer.php",
        "share.php",
        "sharearticle",
        "share-offsite",
        "share_channel",
        "shareopenpage",
        "submit",
        "create",
        "compose",
        "intent",
        "dialog",
        "sendto",
        "cxt",
    }
)

# Parámetros con los que un botón de compartir dice qué página comparte.
_SHARE_PARAMS = frozenset({"url", "u", "text", "link", "description", "mini", "title", "body"})


def is_share_url(raw: str | None) -> bool:
    """¿Es un botón de «compartir» en vez de un perfil?

    Se mira la ruta y los parámetros, no solo el dominio:
    `linkedin.com/shareArticle?url=…` y `wa.me/?text=…` son widgets de la web
    que se está leyendo, no cuentas del negocio.
    """
    if not raw:
        return False

    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return False

    segments = {segment.lower() for segment in parts.path.split("/") if segment}
    if segments & _SHARE_SEGMENTS:
        return True

    keys = {key.lower() for key, _ in parse_qsl(parts.query)}
    if not keys:
        return False

    # `wa.me/?text=…` no lleva número: comparte, no escribe a nadie. Un perfil
    # legítimo puede traer `?hl=es` o `?igshid=…`, así que solo cuenta cuando
    # el parámetro es de los que describen la página compartida.
    if not segments and keys & _SHARE_PARAMS:
        return True
    return bool(keys & {"url", "u", "link"})


_REDIRECT_HOSTS = frozenset({"google.com", "www.google.com", "l.facebook.com", "lm.facebook.com"})
_REDIRECT_PARAMS = ("q", "url", "u")


def unwrap_redirect(raw: str | None) -> str | None:
    """Saca la URL real de un enlace de redirección.

    Google no enlaza directo desde algunas fichas: pone
    `google.com/url?q=https%3A%2F%2Finstagram.com%2Fx`. Sin deshacerlo, el
    detector de plataforma ve `google.com` y descarta el perfil.
    """
    if not raw:
        return None

    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return raw

    if parts.netloc.lower() not in _REDIRECT_HOSTS:
        return raw

    query = dict(parse_qsl(parts.query))
    for key in _REDIRECT_PARAMS:
        target = query.get(key)
        # Solo se sigue a un destino absoluto: `?q=/maps/place/...` es
        # navegación interna de Google, no un enlace saliente.
        if target and "://" in target:
            return target
    return raw


def social_handle(raw: str | None) -> str | None:
    """El nombre de usuario dentro de una URL social.

    `instagram.com/lafinca/` -> `lafinca`; `linkedin.com/company/la-finca` ->
    `la-finca`. Sirve para enseñar `@lafinca` en vez de una URL de 80
    caracteres en una tabla densa.
    """
    if not raw:
        return None

    candidate = raw.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        path = urlsplit(candidate).path
    except ValueError:
        return None

    for segment in path.strip("/").split("/"):
        limpio = segment.lstrip("@").strip()
        if not limpio or limpio.lower() in _HANDLE_NOISE:
            continue
        return limpio[:120]

    return None


def detect_social_platform(raw: str | None) -> str | None:
    """Plataforma de una URL social, o None si no lo es."""
    domain = extract_domain(raw)
    if not domain:
        return None

    mapping = {
        "instagram.com": "instagram",
        "facebook.com": "facebook",
        "linkedin.com": "linkedin",
        "twitter.com": "x",
        "x.com": "x",
        "tiktok.com": "tiktok",
        "youtube.com": "youtube",
        "wa.me": "whatsapp",
        "whatsapp.com": "whatsapp",
        "api.whatsapp.com": "whatsapp",
        "t.me": "telegram",
        "pinterest.com": "pinterest",
    }
    return mapping.get(domain)


def same_domain(a: str | None, b: str | None) -> bool:
    domain_a, domain_b = extract_domain(a), extract_domain(b)
    return bool(domain_a) and domain_a == domain_b
