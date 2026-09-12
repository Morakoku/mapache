# Operaciones Veyra en la VPS — despliegue

Stack unificado (todas las herramientas en una sola VPS, una sola entrada con HTTPS).

| Subdominio | Servicio |
|---|---|
| `home.DOMINIO` | Homepage — panel de inicio + estado de todo |
| `crm.DOMINIO` | Appsmith — UI CRM/ops |
| `metricas.DOMINIO` | Metabase — BI |
| `flujos.DOMINIO` | n8n — automatizaciones visuales |
| `admin.DOMINIO` | Portainer — administrar contenedores |

---

## 1. Crear la VPS (Alibaba Cloud)
- Producto: **ECS** o **Simple Application Server**.
- **Imagen:** Ubuntu 22.04/24.04 LTS.
- **Specs recomendadas:** 4 vCPU / **8 GB RAM** / 60 GB disco (Appsmith+Metabase+n8n+worker).
- **Región:** US (Virginia) o México (Querétaro) — menor latencia a CO/VE.
- **Security Group:** abrir solo **22 (SSH)**, **80** y **443**. Nada más.

## 2. DNS (Alibaba Cloud DNS)
Crear registros **A** apuntando a la **IP pública** de la VPS:
```
home        A  <IP_VPS>
crm         A  <IP_VPS>
metricas    A  <IP_VPS>
flujos      A  <IP_VPS>
admin       A  <IP_VPS>
```
Caddy obtiene los certificados (Let's Encrypt) automáticamente al primer arranque.

## 3. Preparar el servidor
```bash
ssh root@<IP_VPS>
# usuario no-root + docker (el instalador oficial de Docker)
adduser ops && usermod -aG sudo,docker ops
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin git
```

## 4. Desplegar
```bash
git clone <repo-mapache> /opt/veyra && cd /opt/veyra/ops/deploy
cp .env.example .env      # completar DOMAIN + secretos
docker compose up -d
```
Primer arranque (una vez cada uno, desde el navegador):
- `https://crm.DOMINIO` → crear admin de Appsmith y **datasource Postgres → schema `crm`**.
- `https://metricas.DOMINIO` → crear admin de Metabase y conectar la base (schema `crm`).
- `https://flujos.DOMINIO` → login con `N8N_USER`/`N8N_PASSWORD`.
- `https://admin.DOMINIO` → crear admin de Portainer (timeout de 5 min en el primer arranque).

## 5. Actualizar
```bash
cd /opt/veyra && git pull && cd ops/deploy && docker compose up -d --build
```

## 6. Backups (obligatorio)
- **Supabase:** backups automáticos del plan + `pg_dump` diario a un volumen/objeto.
- **Volúmenes locales** (Appsmith, Metabase, n8n, Portainer): backup semanal del directorio de volúmenes de Docker.
- n8n: exportar los flujos (JSON) al repo `ops/` (así queda versionado).

## Notas
- **WhatsApp:** en Linux no existe `hermes.exe`. El transporte debe ser **WhatsApp Cloud API** (webhook ya implementado).
- **Hermes** (agentes/Kanban) lo migra la sesión de Guaki a la misma VPS; se enlaza desde `home`.
- **Base de datos:** hoy **Supabase** (gratis). Para pasar a Postgres local, se añade un servicio `postgres` y se migra el schema `crm` (pendiente de decisión).
