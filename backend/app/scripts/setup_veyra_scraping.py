# Configuracion inicial de scraping para Veyra Soluciones
# Ejecutar: python -m app.scripts.setup_veyra_scraping

"""Configura las busquedas iniciales de scraping para Colombia y Venezuela.

Uso:
    python -m app.scripts.setup_veyra_scraping --google-places-key <tu-api-key>

Sin API key: muestra las ciudades configuradas (solo lectura).
"""

from __future__ import annotations

# ---------------------------------------------------------------- ciudades
# Ciudades principales de Colombia y Venezuela para prospección

CIUDADES_COLOMBIA = [
    # Principales
    {"nombre": "Bogotá", "lat": 4.7110, "lng": -74.0721},
    {"nombre": "Medellín", "lat": 6.2442, "lng": -75.5812},
    {"nombre": "Cali", "lat": 3.4516, "lng": -76.5320},
    {"nombre": "Barranquilla", "lat": 10.9685, "lng": -74.7813},
    {"nombre": "Cartagena", "lat": 10.3910, "lng": -75.4794},
    # Secundarias
    {"nombre": "Bucaramanga", "lat": 7.1254, "lng": -73.1198},
    {"nombre": "Pereira", "lat": 4.8133, "lng": -75.6961},
    {"nombre": "Santa Marta", "lat": 11.2408, "lng": -74.1990},
    {"nombre": "Cúcuta", "lat": 7.8939, "lng": -72.5078},
    {"nombre": "Villavicencio", "lat": 4.1420, "lng": -73.6266},
    {"nombre": "Pasto", "lat": 1.2136, "lng": -77.2811},
    {"nombre": "Montería", "lat": 8.7472, "lng": -75.8814},
    {"nombre": "Neiva", "lat": 2.9273, "lng": -75.2819},
    {"nombre": "Armenia", "lat": 4.5339, "lng": -75.6811},
    {"nombre": "Sincelejo", "lat": 9.3047, "lng": -75.3978},
    {"nombre": "Popayán", "lat": 2.4452, "lng": -76.6147},
    {"nombre": "Valledupar", "lat": 10.4734, "lng": -73.2532},
    {"nombre": "Ibagué", "lat": 4.4389, "lng": -75.2322},
    {"nombre": "Tunja", "lat": 5.5353, "lng": -73.3619},
    {"nombre": "Florencia", "lat": 1.6150, "lng": -75.6064},
]

CIUDADES_VENEZUELA = [
    # Principales
    {"nombre": "Caracas", "lat": 10.4806, "lng": -66.9036},
    {"nombre": "Maracaibo", "lat": 10.6666, "lng": -71.6124},
    {"nombre": "Valencia", "lat": 10.1579, "lng": -67.9972},
    {"nombre": "Barquisimeto", "lat": 10.0647, "lng": -69.3570},
    {"nombre": "Maracay", "lat": 10.2469, "lng": -67.5958},
    # Secundarias
    {"nombre": "Ciudad Guayana", "lat": 8.3514, "lng": -62.6436},
    {"nombre": "Barcelona", "lat": 10.1167, "lng": -64.7000},
    {"nombre": "Maturín", "lat": 9.7457, "lng": -63.1830},
    {"nombre": "Puerto La Cruz", "lat": 10.2167, "lng": -64.6333},
    {"nombre": "San Cristóbal", "lat": 7.7667, "lng": -72.2167},
    {"nombre": "Cumaná", "lat": 10.4500, "lng": -64.1667},
    {"nombre": "Mérida", "lat": 8.5897, "lng": -71.1561},
    {"nombre": "Coro", "lat": 11.4094, "lng": -69.6686},
    {"nombre": "Puerto Ayacucho", "lat": 5.6639, "lng": -67.6236},
    {"nombre": "Valera", "lat": 9.3167, "lng": -70.6167},
    {"nombre": "Carúpano", "lat": 10.6667, "lng": -63.2500},
    {"nombre": "Los Teques", "lat": 10.3417, "lng": -67.0417},
    {"nombre": "Guanare", "lat": 9.0417, "lng": -69.7500},
]


def generar_consultas(busquedas_por_ciudad: list[dict[str, str]] | None = None) -> list[dict]:
    """Genera las consultas de busqueda para cada ciudad.
    
    Args:
        busquedas_por_ciudad: Lista de diccionarios con 'business_type' para cada ciudad.
        Si es None, usa las busquedas por defecto de Veyra.
    """
    if busquedas_por_ciudad is None:
        # Tipos de negocio que segun el "Business MRI" tienen friccion operativa
        busquedas_por_ciudad = [
            {"business_type": "odontología"},
            {"business_type": "clínica"},
            {"business_type": "laboratorio"},
            {"business_type": "farmacia"},
            {"business_type": "consultorio"},
            {"business_type": "bufete de abogados"},
            {"business_type": "agencia inmobiliaria"},
            {"business_type": "concesionario"},
            {"business_type": "constructora"},
            {"business_type": "academia"},
            {"business_type": "restaurante"},
            {"business_type": "hotel"},
        ]
    
    consultas = []
    ciudades = CIUDADES_COLOMBIA + CIUDADES_VENEZUELA
    
    for ciudad in ciudades:
        for busqueda in busquedas_por_ciudad:
            consulta = {
                "text_query": f"{busqueda['business_type']} in {ciudad['nombre']}",
                "latitude": ciudad["lat"],
                "longitude": ciudad["lng"],
                "radius_km": 25,
                "limit": 20,
                "country": "Colombia" if ciudad in CIUDADES_COLOMBIA else "Venezuela",
            }
            consultas.append(consulta)
    
    return consultas


def mostrar_resumen() -> None:
    """Muestra el resumen de ciudades configuradas."""
    print("=" * 60)
    print("CONFIGURACIÓN SCRAPING - VEYRA SOLUCIONES")
    print("=" * 60)
    print()
    print("🇨🇴 COLOMBIA ({} ciudades):".format(len(CIUDADES_COLOMBIA)))
    for c in CIUDADES_COLOMBIA:
        print(f"  - {c['nombre']}")
    print()
    print("🇻🇪 VENEZUELA ({} ciudades):".format(len(CIUDADES_VENEZUELA)))
    for c in CIUDADES_VENEZUELA:
        print(f"  - {c['nombre']}")
    print()
    total_consultas = len(CIUDADES_COLOMBIA + CIUDADES_VENEZUELA) * 12
    print(f"Total: {len(CIUDADES_COLOMBIA + CIUDADES_VENEZUELA)} ciudades × 12 busquedas = {total_consultas} consultas")
    print()
    print("=" * 60)
    print("TIPOS DE NEGOCIO POR DEFECTO:")
    print("=" * 60)
    tipos = [
        "odontología", "clínica", "laboratorio", "farmacia",
        "consultorio", "bufete de abogados", "agencia inmobiliaria",
        "concesionario", "constructora", "academia", "restaurante", "hotel"
    ]
    for i, tipo in enumerate(tipos, 1):
        print(f"  {i:2d}. {tipo}")
    print()
    print("Para ejecutar el scraping:")
    print("  python -m app.scripts.setup_veyra_scraping --google-plaves-key <tu-api-key>")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Configuracion scraping Veyra")
    parser.add_argument("--google-places-key", help="API key de Google Places (opcional)")
    args = parser.parse_args()
    
    mostrar_resumen()
    
    if args.google_places_key:
        print("\n✅ API key proporcionada. Configurando busquedas...")
        # Aqui iria la logica de insertar en la base de datos
        # Por ahora solo mostramos el resumen
        consultas = generar_consultas()
        print(f"Se generaron {len(consultas)} consultas de busqueda.")
        print("Ejecutar desde el worker de discovery para iniciar el scraping.")
    else:
        print("\n⚠️ Sin API key. Muestra de configuracion.")
        print("Para scraping real, necesitas una Google Places API key.")
