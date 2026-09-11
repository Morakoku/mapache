"""Sección Veyra de la Torre de Control: fichas/soluciones en PDF.

Sirve una galería (`/torre-control/productos`) y los PDFs alojados en
`app/static/productos/`. Contenido orientado al CLIENTE (sin estado interno).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.routers.torre_control import _chequeo_token_ok

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "productos"
PUBLIC_BASE = "https://mapache-kappa.vercel.app"
FROM_EMAIL = "edwin@veyrasoluciones.com"
FROM_NAME = "Veyra Soluciones"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Fichas publicables. `estado` aquí es de cara al cliente; el estado interno real
# vive en docs/operations/08_CATALOGO_CAPACIDADES.md.
FICHAS = [
    {
        "archivo": "13_Opciones_WhatsApp_Cliente.pdf",
        "nombre": "Recepcionista IA · cómo conectarla",
        "grupo": "Producto en marcha",
        "resumen": "Las dos formas de poner tu Recepcionista IA en WhatsApp, con pros y contras.",
    },
    {
        "archivo": "14_Agenda_IA.pdf",
        "nombre": "Agenda IA",
        "grupo": "Soluciones a medida",
        "resumen": "Tus citas se agendan solas: propone horarios, confirma y recuerda.",
    },
    {
        "archivo": "15_Veyra_Ops.pdf",
        "nombre": "Veyra Ops",
        "grupo": "Soluciones a medida",
        "resumen": "El software que tu operación necesita (pedidos, inventario, agenda…), hecho a tu medida.",
    },
    {
        "archivo": "16_Veyra_Copiloto.pdf",
        "nombre": "Veyra Copiloto",
        "grupo": "Soluciones a medida",
        "resumen": "Un asistente para tu equipo que conoce tu negocio y responde con tu información.",
    },
    {
        "archivo": "17_Veyra_Puente.pdf",
        "nombre": "Veyra Puente",
        "grupo": "Soluciones a medida",
        "resumen": "Tus conversaciones de WhatsApp, dentro de tu CRM. Nada se pierde.",
    },
    {
        "archivo": "18_Veyra_Radar.pdf",
        "nombre": "Veyra Radar",
        "grupo": "Soluciones a medida",
        "resumen": "Los números de tu negocio, claros y al día, en un solo tablero.",
    },
    {
        "archivo": "19_Veyra_Conecta.pdf",
        "nombre": "Veyra Conecta",
        "grupo": "Soluciones a medida",
        "resumen": "Que tus herramientas (hojas de cálculo, facturación, ERP) trabajen juntas.",
    },
]

_ARCHIVOS_VALIDOS = {f["archivo"] for f in FICHAS}


def _card(f: dict) -> str:
    return f"""
    <div class="item">
      <div class="grp">{f['grupo']}</div>
      <h3>{f['nombre']}</h3>
      <p>{f['resumen']}</p>
      <div class="acts">
        <a class="view" href="/torre-control/productos/{f['archivo']}" target="_blank" rel="noopener">Ver PDF</a>
        <a class="dl" href="/torre-control/productos/{f['archivo']}" download>Descargar</a>
        <button class="mini" type="button" onclick="enviarCorreo('{f['archivo']}','{f['nombre']}')">Correo</button>
        <button class="mini" type="button" onclick="enviarWhatsApp('{f['archivo']}','{f['nombre']}')">WhatsApp</button>
      </div>
    </div>"""


@router.get("/productos", response_class=HTMLResponse)
async def productos_index() -> HTMLResponse:
    """Galería de soluciones/fichas de Veyra."""
    items = "".join(_card(f) for f in FICHAS)
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Soluciones Veyra — Torre de Control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@700;800;900&display=swap" rel="stylesheet">
<style>
  :root{{--red:#FF0000;--bg:#0A0A0A;--card:#131313;--border:#262626;--fg:#F5F5F5;--muted:#9AA0A6}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter',sans-serif;background:var(--bg);color:var(--fg);line-height:1.6}}
  .wrap{{max-width:1080px;margin:0 auto;padding:44px 28px}}
  .brand{{display:flex;align-items:center;gap:10px;font-family:'Outfit';font-weight:900;letter-spacing:.14em;font-size:12px;text-transform:uppercase;color:var(--muted)}}
  .brand .dot{{width:9px;height:9px;border-radius:50%;background:var(--red)}}
  h1{{font-family:'Outfit';font-weight:900;font-size:40px;letter-spacing:-.02em;margin:26px 0 6px}}
  h1 em{{font-style:normal;color:var(--red)}}
  .sub{{color:var(--muted);font-size:15px;max-width:640px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px;margin-top:34px}}
  .item{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px}}
  .grp{{font-family:'Outfit';font-weight:800;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--red);margin-bottom:10px}}
  .item h3{{font-family:'Outfit';font-weight:800;font-size:19px;margin-bottom:6px}}
  .item p{{color:var(--muted);font-size:13.5px;min-height:58px}}
  .acts{{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}}
  .acts a,.acts button{{text-decoration:none;font-size:12.5px;font-weight:700;padding:9px 13px;border-radius:8px;font-family:'Outfit';letter-spacing:.03em;cursor:pointer}}
  .acts .view{{background:var(--red);color:#fff}}
  .acts .dl{{border:1px solid var(--border);color:var(--fg);background:0 0}}
  .acts .mini{{border:1px solid var(--border);color:var(--muted);background:0 0}}
  .acts .mini:hover{{color:var(--fg);border-color:var(--red)}}
  .back{{display:inline-block;margin-top:34px;color:var(--muted);text-decoration:none;font-size:13px}}
  .back:hover{{color:var(--fg)}}
  .acts a:focus-visible,.acts button:focus-visible,.back:focus-visible{{outline:2px solid var(--red);outline-offset:2px}}
</style></head>
<body><div class="wrap">
  <div class="brand"><span class="dot"></span> VEYRA · TORRE DE CONTROL</div>
  <h1>Soluciones y documentos <em>Veyra</em></h1>
  <p class="sub">Fichas listas para mostrar o enviar al cliente. Ábrelas o descárgalas; cada una explica qué resuelve y las dos formas de implementarlo con pros y contras.</p>
  <div class="grid">{items}</div>
  <a class="back" href="/torre-control">&larr; Volver a la Torre</a>
</div>
<script>
function _tok(){{ return sessionStorage.getItem('veyra_ops_token') || ''; }}
function enviarCorreo(archivo, nombre){{
  var to = prompt('Correo del destinatario:');
  if(!to) return;
  fetch('/torre-control/productos/' + archivo + '/enviar-email', {{
    method:'POST',
    headers:{{'Content-Type':'application/json','X-Veyra-Token':_tok()}},
    body: JSON.stringify({{to: to}})
  }}).then(function(r){{return r.json().then(function(d){{return {{s:r.status,d:d}};}});}})
    .then(function(x){{
      if(x.s===200) alert('Ficha enviada a ' + to);
      else alert('No se pudo enviar: ' + (x.d.detail || x.s));
    }}).catch(function(e){{ alert('Error: ' + e.message); }});
}}
function enviarWhatsApp(archivo, nombre){{
  var tel = prompt('Número con indicativo (ej. 57..., 58...):');
  if(!tel) return;
  var num = tel.replace(/[^0-9]/g,'');
  var link = location.origin + '/torre-control/productos/' + archivo;
  var txt = 'Hola, te comparto ' + nombre + ' de Veyra: ' + link;
  window.open('https://wa.me/' + num + '?text=' + encodeURIComponent(txt), '_blank');
}}
</script>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/productos/{archivo}")
async def producto_pdf(archivo: str) -> FileResponse:
    """Devuelve un PDF de la sección (solo desde la lista permitida)."""
    if archivo not in _ARCHIVOS_VALIDOS:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    ruta = STATIC_DIR / archivo
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Documento no disponible.")
    return FileResponse(path=str(ruta), media_type="application/pdf", filename=archivo)


class EnviarEmailIn(BaseModel):
    to: str
    nombre: str | None = None
    mensaje: str | None = None


def _email_html(ficha: dict, mensaje: str | None) -> str:
    link = f"{PUBLIC_BASE}/torre-control/productos/{ficha['archivo']}"
    extra = (
        f"<p style='font-size:15px;color:#0A0A0A'>{mensaje}</p>" if mensaje else ""
    )
    return f"""<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;background:#F8FAFC;margin:0;padding:24px">
  <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #E2E8F0;border-radius:12px;overflow:hidden">
    <div style="background:#0A0A0A;padding:20px 24px">
      <span style="color:#fff;font-weight:800;letter-spacing:.14em;font-size:12px">VEYRA SOLUCIONES</span>
    </div>
    <div style="padding:26px 24px">
      <h1 style="font-size:20px;color:#0A0A0A;margin:0 0 6px">{ficha['nombre']}</h1>
      <p style="font-size:15px;color:#334155;margin:0 0 16px">{ficha['resumen']}</p>
      {extra}
      <p style="font-size:14px;color:#334155;margin:16px 0">Te dejo la ficha completa (PDF) adjunta. También puedes verla aquí:</p>
      <p style="margin:0 0 18px"><a href="{link}" style="color:#FF0000;font-weight:700;text-decoration:none">{link}</a></p>
      <p style="font-size:13px;color:#64748B;margin:0">¿Agendamos 15 minutos para verlo sobre tu operación? Responde este correo y lo cuadramos.</p>
    </div>
    <div style="border-top:1px solid #E2E8F0;padding:14px 24px;font-size:11px;color:#94A3B8">VEYRA · RESONANCIA EMPRESARIAL · MÉTODO ASTF™ · veyrasoluciones.com</div>
  </div></body></html>"""


async def _resend_enviar(to: str, ficha: dict, ruta: Path, mensaje: str | None) -> bool:
    """Envía la ficha por correo (Resend) con el PDF adjunto."""
    import httpx

    from app.core.config import get_settings

    key = (get_settings().resend_api_key_value or "").strip()
    if not key:
        return False
    contenido = base64.b64encode(ruta.read_bytes()).decode("ascii")
    payload = {
        "from": f"{FROM_NAME} <{FROM_EMAIL}>",
        "to": [to],
        "subject": f"Veyra — {ficha['nombre']}",
        "html": _email_html(ficha, mensaje),
        "attachments": [{"filename": ficha["archivo"], "content": contenido}],
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@router.post("/productos/{archivo}/enviar-email")
async def enviar_email(archivo: str, body: EnviarEmailIn, request: Request) -> dict:
    """Envía una ficha al destinatario (adjunta el PDF). Requiere token de operador."""
    if not _chequeo_token_ok(request):
        raise HTTPException(status_code=401, detail="Token de operador requerido.")
    if archivo not in _ARCHIVOS_VALIDOS:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    destino = (body.to or "").strip()
    if not _EMAIL_RE.match(destino):
        raise HTTPException(status_code=422, detail="Correo inválido.")
    ficha = next(f for f in FICHAS if f["archivo"] == archivo)
    ruta = STATIC_DIR / archivo
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Documento no disponible.")
    ok = await _resend_enviar(destino, ficha, ruta, (body.mensaje or None))
    if not ok:
        raise HTTPException(status_code=502, detail="No se pudo enviar el correo.")
    return {"status": "OK", "to": destino, "ficha": ficha["nombre"]}
