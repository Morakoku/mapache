"""Script de prueba: Scrapling con Google Maps.

Scrapling es un framework de scraping Python con anti-detección.
No requiere API key. Usa Playwright para navegador headless.
"""

import asyncio
from scrapling.fetchers import StealthyFetcher


async def scrape_google_maps():
    """Prueba de scraping de Google Maps con Scrapling."""
    
    # URL de búsqueda en Google Maps
    url = "https://www.google.com/maps/search/odontología+in+Bogotá"
    
    print(f"Scrapeando: {url}")
    
    # StealthyFetcher usa Playwright con anti-detección
    page = await StealthyFetcher().get(url)
    
    # Esperar que cargue el contenido
    await asyncio.sleep(3)
    
    # Extraer nombres de negocios (selectores de Google Maps)
    # Nota: Google Maps cambia selectores frecuentemente
    titles = page.css_selector('div[role="article"]')
    
    print(f"Encontrados: {len(titles)} elementos")
    
    for i, title in enumerate(titles[:5], 1):
        print(f"  {i}. {title.text()[:100]}")
    
    return titles


if __name__ == "__main__":
    asyncio.run(scrape_google_maps())
