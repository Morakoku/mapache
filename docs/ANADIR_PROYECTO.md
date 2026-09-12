# Cómo añadir un proyecto/marca

Convención única para crecer sin desorden. Un proyecto nuevo = una entrada aquí + cuatro pasos.

## 1. Regístralo
Añade una fila (marca, qué es, audiencia, canal, carpeta de contenido).

| Marca | Qué es | Carpeta de contenido |
|---|---|---|
| Mapache | CRM de prospección | `docs/content/mapache/` |
| Veyra | Consultoría (sitios + soluciones) | `docs/content/veyra/` |
| Guaki | Marketplace | `docs/content/guaki/` |
| Brenda | Academia de uñas | `docs/content/brenda/` |

## 2. Datos (tenant)
En la base (`schema crm`), todo lleva `owner_id`/tenant. Los registros del proyecto
se filtran por ese valor. No se crean bases separadas por marca.

## 3. UI (Appsmith)
- Crea una **página** por proyecto (o una tabla con filtro por tenant).
- Usa el datasource Postgres con schema `crm`.

## 4. Automatización (n8n)
- Un **flujo** por necesidad (leads, contenido, avisos).
- Exporta el JSON a `ops/n8n/<proyecto>/`.

## 5. Contenido (Content OS)
- Sigue `docs/CONTENT_OS.md` (research → ideas → copy → diseño → calendario → publicación).
- Cada pieza vive en la carpeta de la marca.

## Regla
Si algo no se puede operar desde **Homepage/Appsmith/Metabase/n8n**, se documenta por qué;
no se construye una interfaz nueva a mano.
