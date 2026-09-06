"""Proveedor alternativo: Google Places API (New).

Es el plan B del scraper. Cuando Google cambie el DOM y el scraper caiga un
martes cualquiera, se cambia `discovery_provider` en Configuración y la
prospección sigue funcionando esa misma tarde — esa es toda la razón de que
exista el Protocol.

Tiene coste por petición y no devuelve email (ningún proveedor lo hace: el
email sale del enriquecimiento del sitio web).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.enums import SourceType
from app.core.logging import get_logger
from app.scrapers.base import (
    DiscoveryProvider,
    ProviderError,
    ProviderHealth,
    RawPlace,
    SearchQuery,
)

logger = get_logger(__name__)

_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
_PAGE_SIZE = 20

# La API cobra por campo solicitado, así que se piden solo los que se usan.
_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.primaryTypeDisplayName",
        "places.types",
        "places.businessStatus",
        "places.regularOpeningHours",
        "places.editorialSummary",
        "places.priceLevel",
        "nextPageToken",
    ]
)

_PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}

_COMPONENT_MAP = {
    "locality": "city",
    "administrative_area_level_1": "region",
    "country": "country",
    "postal_code": "postal_code",
}


class GooglePlacesApiProvider(DiscoveryProvider):
    name = "google_places_api"
    source_type = SourceType.GOOGLE_PLACES_API

    def __init__(self, api_key: str, timeout_s: float = 20.0) -> None:
        if not api_key:
            raise ProviderError(
                "Falta la clave de Google Places. Configúrala en Ajustes antes de "
                "usar este proveedor."
            )
        self._api_key = api_key
        self._timeout = timeout_s

    async def search(self, query: SearchQuery) -> AsyncIterator[RawPlace]:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body: dict[str, Any] = {
            "textQuery": query.text_query,
            "languageCode": query.language,
            "maxResultCount": min(_PAGE_SIZE, query.limit),
        }
        if query.latitude is not None and query.longitude is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": query.latitude, "longitude": query.longitude},
                    "radius": min(query.radius_km * 1000, 50_000),  # tope de la API
                }
            }

        emitted = 0
        page_token: str | None = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while emitted < query.limit:
                if page_token:
                    body["pageToken"] = page_token

                response = await client.post(_ENDPOINT, headers=headers, json=body)
                if response.status_code != 200:
                    raise ProviderError(
                        f"Places API respondió {response.status_code}: {response.text[:200]}"
                    )

                payload = response.json()
                places = payload.get("places", [])
                if not places:
                    break

                for raw in places:
                    if emitted >= query.limit:
                        break
                    place = self._to_raw_place(raw, position=emitted)
                    if place.is_usable and query.matches_filters(place):
                        emitted += 1
                        yield place

                page_token = payload.get("nextPageToken")
                if not page_token:
                    break

        logger.info("places_api_finished", emitted=emitted, query=query.text_query)

    def _to_raw_place(self, raw: dict[str, Any], position: int) -> RawPlace:
        components = {}
        for component in raw.get("addressComponents", []):
            for type_name in component.get("types", []):
                key = _COMPONENT_MAP.get(type_name)
                if key and key not in components:
                    components[key] = component.get("longText")

        location = raw.get("location", {})
        return RawPlace(
            name=(raw.get("displayName") or {}).get("text", ""),
            source=self.source_type,
            external_id=raw.get("id"),
            place_id=raw.get("id"),
            description=(raw.get("editorialSummary") or {}).get("text"),
            category=(raw.get("primaryTypeDisplayName") or {}).get("text"),
            categories=raw.get("types", []),
            address=raw.get("formattedAddress"),
            city=components.get("city"),
            region=components.get("region"),
            country=components.get("country"),
            postal_code=components.get("postal_code"),
            phone=raw.get("internationalPhoneNumber") or raw.get("nationalPhoneNumber"),
            website=raw.get("websiteUri"),
            maps_url=raw.get("googleMapsUri"),
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            rating=raw.get("rating"),
            reviews_count=raw.get("userRatingCount"),
            price_level=_PRICE_LEVELS.get(raw.get("priceLevel", "")),
            opening_hours=self._parse_hours(raw.get("regularOpeningHours")),
            is_permanently_closed=raw.get("businessStatus") == "CLOSED_PERMANENTLY",
            position=position,
            raw=raw,
        )

    @staticmethod
    def _parse_hours(hours: dict[str, Any] | None) -> dict[str, Any] | None:
        """`periods` de la API -> el mismo formato que produce el scraper.

        Que ambos proveedores emitan la misma forma es lo que permite
        cambiarlos sin tocar nada aguas abajo.
        """
        if not hours or "periods" not in hours:
            return None

        days = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
        result: dict[str, list[list[str]]] = {}
        for period in hours["periods"]:
            open_info = period.get("open", {})
            close_info = period.get("close", {})
            day_index = open_info.get("day")
            if day_index is None or not 0 <= day_index < 7:
                continue
            start = f"{open_info.get('hour', 0):02d}:{open_info.get('minute', 0):02d}"
            end = f"{close_info.get('hour', 23):02d}:{close_info.get('minute', 59):02d}"
            result.setdefault(days[day_index], []).append([start, end])

        return result or None

    async def healthcheck(self) -> ProviderHealth:
        query = SearchQuery(business_type="Éxito", city="Medellín", limit=1)
        try:
            places = [p async for p in self.search(query)]
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(provider=self.name, healthy=False, message=str(exc))

        return ProviderHealth(
            provider=self.name,
            healthy=bool(places),
            checked_fields={"results": bool(places)},
        )
