# Scrapling - Segunda opción de scraping

## Estado: ✅ Integrado y probado

## Dependencias necesarias (no están en requirements.vercel.txt):
```
scrapling>=0.4.15
curl_cffi
playwright
patchright
msgspec
browserforge
```

## Uso principal

### Enriquecimiento de empresas
```python
from scripts.scrapers.scrapling.enrich import enrich_business

result = asyncio.run(enrich_business("https://empresa.com"))
# Retorna: {"emails": [...], "socials": {...}, "description": "..."}
```

### Scraping simple (StaticFetcher - sin navegador)
```python
from scrapling.fetchers import StaticFetcher
page = await StaticFetcher().get("https://example.com")
```

### Scraping con anti-detección (StealthyFetcher - con navegador)
```python
from scrapling.fetchers import StealthyFetcher
page = await StealthyFetcher().get("https://example.com")
```

## Cuándo usar cada herramienta

| Situación | Herramienta |
|-----------|-------------|
| Descubrir negocios masivamente | google-maps-scraper |
| Enriquecer con emails/sitios web estáticos | Scrapling (StaticFetcher) |
| Enriquecir sitios con anti-bot | Scrapling (StealthyFetcher) |
| Automatizar tareas de código | OpenHands |

## Limitaciones
- StealthyFetcher requiere navegador headless (no funciona en Vercel serverless)
- StaticFetcher es más ligero pero no maneja JavaScript
- Para Vercel: usar StaticFetcher en funciones locales o VPS
