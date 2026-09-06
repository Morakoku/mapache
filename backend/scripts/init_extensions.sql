-- Extensiones que necesita el CRM. Se ejecuta una sola vez, al crear el
-- volumen de datos del contenedor.

-- Búsqueda difusa por nombre de empresa (dedupe nivel 3, §4.3).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Emails case-insensitive sin LOWER() por todas partes.
CREATE EXTENSION IF NOT EXISTS citext;

-- Distancia geográfica para el filtro por radio de las búsquedas.
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;

-- Sin normalización de tildes, "Bogota" y "Bogotá" son ciudades distintas.
CREATE EXTENSION IF NOT EXISTS unaccent;
