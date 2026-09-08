"""Scrapling: segunda opción para enriquecimiento de datos.

Scrapling es un framework Python de scraping con anti-detección (StealthyFetcher).
Se usa como alternativa al google-maps-scraper para enriquecer datos:
- Extraer emails de sitios web
- Extraer redes sociales
- Extraer descripciones

Uso:
    python -m app.scripts.scrapers.scrapling.enrich --url https://example.com
"""

import asyncio
import re
from typing import Any

from scrapling.fetchers import StealthyFetcher


async def extract_emails(url: str) -> list[str]:
    """Extrae emails de una página web."""
    try:
        page = await StealthyFetcher().get(url)
        await asyncio.sleep(2)
        
        # Buscar emails en el texto
        text = page.text()
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        emails = list(set(re.findall(email_pattern, text)))
        
        return emails
    except Exception as e:
        return []


async def extract_social_links(url: str) -> dict[str, str]:
    """Extrae enlaces a redes sociales."""
    social_patterns = {
        "facebook": r'facebook\.com/[^"\s<>]+',
        "instagram": r'instagram\.com/[^"\s<>]+',
        "linkedin": r'linkedin\.com/[^"\s<>]+',
        "twitter": r'twitter\.com/[^"\s<>]+',
        "tiktok": r'tiktok\.com/[^"\s<>]+',
        "youtube": r'youtube\.com/[^"\s<>]+',
    }
    
    try:
        page = await StealthyFetcher().get(url)
        await asyncio.sleep(2)
        text = page.text()
        
        results = {}
        for platform, pattern in social_patterns.items():
            matches = re.findall(pattern, text)
            if matches:
                results[platform] = f"https://{matches[0]}"
        
        return results
    except Exception as e:
        return {}


async def extract_description(url: str) -> str:
    """Extrae la descripción del sitio (meta description o primer párrafo)."""
    try:
        page = await StealthyFetcher().get(url)
        await asyncio.sleep(2)
        
        # Buscar meta description
        meta = page.css_selector('meta[name="description"]')
        if meta:
            return meta[0].get("content", "")
        
        # Buscar primer párrafo
        paragraphs = page.css_selector('p')
        if paragraphs:
            return paragraphs[0].text()[:500]
        
        return ""
    except Exception as e:
        return ""


async def enrich_business(url: str) -> dict[str, Any]:
    """Enriquece los datos de un negocio scrapeando su sitio web.
    
    Args:
        url: URL del sitio web del negocio
        
    Returns:
        Dict con emails, redes sociales y descripción
    """
    if not url:
        return {"emails": [], "socials": {}, "description": ""}
    
    # Asegurar que la URL tenga esquema
    if not url.startswith("http"):
        url = f"https://{url}"
    
    emails, socials, description = await asyncio.gather(
        extract_emails(url),
        extract_social_links(url),
        extract_description(url),
    )
    
    return {
        "emails": emails[:5],  # Máximo 5 emails
        "socials": socials,
        "description": description[:1000],
    }


if __name__ == "__main__":
    import sys
    
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://veyrasoluciones.com"
    print(f"Enriqueciendo: {test_url}")
    
    result = asyncio.run(enrich_business(test_url))
    print(f"Emails: {result['emails']}")
    print(f"Redes: {result['socials']}")
    print(f"Descripción: {result['description'][:200]}...")
