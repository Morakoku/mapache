"""Página de gestión de la Recepcionista IA (Torre de Control).

Se sirve en /torre-control/recepcionista. Configura la recepcionista de cada
cliente y permite probar el cerebro con un mensaje real.
"""

RECEPTIONIST_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recepcionista IA · VEYRA</title>
<style>
:root{--bg:#0a0a0a;--card:#111;--border:#1a1a1a;--fg:#e0e0e0;--muted:#9aa0a6;--accent:#ff6b35;--green:#00c853;--red:#ff1744;--yellow:#ffd600;--blue:#2979ff}
    :focus-visible{outline:3px solid var(--accent);outline-offset:2px}
    ::selection{background:var(--accent);color:#0a0a0a}
    html{caret-color:var(--accent)}
    ::-webkit-scrollbar{width:10px;height:10px}
    ::-webkit-scrollbar-track{background:#0a0a0a}
    ::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:6px}
    ::-webkit-scrollbar-thumb:hover{background:#3a3a3a}
    @media (prefers-reduced-motion: reduce){*,*::before,*::after{transition-duration:.01ms !important;animation-duration:.01ms !important;animation-iteration-count:1 !important}}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',monospace;background:var(--bg);color:var(--fg);min-height:100vh;padding:20px}
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:22px;flex-wrap:wrap;gap:12px}
.header h1{font-size:21px;font-weight:700}
.header h1 .logo{color:var(--accent)}
.header a.home{color:var(--muted);font-size:12px;text-decoration:none;border:1px solid var(--border);padding:8px 12px;border-radius:8px}
.header a.home:hover{border-color:var(--accent);color:var(--fg)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:14px}
label{display:block;font-size:11px;color:var(--muted);margin:10px 0 4px;text-transform:uppercase;letter-spacing:.5px}
input,textarea,select{width:100%;padding:10px;background:#0d0d0d;border:1px solid var(--border);border-radius:8px;color:var(--fg);font-size:13px;outline:none}
input:focus,textarea:focus{border-color:var(--accent)}
textarea{min-height:60px;resize:vertical}
.btn{margin-top:14px;padding:11px 18px;border:none;border-radius:8px;background:var(--accent);color:#000;font-weight:800;font-size:12px;text-transform:uppercase;cursor:pointer}
.btn.sec{background:0 0;border:1px solid var(--border);color:var(--fg)}
.btn:hover{opacity:.9}
.item{border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px}
.item .name{font-weight:700;font-size:14px}
.item .meta{font-size:11px;color:var(--muted);margin-top:3px;line-height:1.6}
.pill{display:inline-block;font-size:10px;font-weight:800;padding:3px 8px;border-radius:20px;text-transform:uppercase}
.pill.on{background:rgba(0,200,83,.15);color:var(--green);border:1px solid var(--green)}
.pill.off{background:rgba(255,23,68,.15);color:var(--red);border:1px solid var(--red)}
.reply{margin-top:10px;background:#0d0d0d;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:13px;white-space:pre-wrap}
.reply .esc{color:var(--yellow)}
.gate{position:fixed;inset:0;background:rgba(5,5,5,.96);display:flex;align-items:center;justify-content:center;z-index:50;padding:20px}
.gate .box{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:26px;width:min(370px,100%)}
.gate .err{color:var(--red);font-size:12px;margin-top:10px;min-height:16px}
.foot{margin-top:18px;color:var(--muted);font-size:11px}
</style>
</head>
<body>

<div class="gate" id="gate" style="display:none">
  <div class="box">
    <h1 style="font-size:18px;margin-bottom:6px">Acceso privado</h1>
    <p style="color:var(--muted);font-size:12px;margin-bottom:14px">Recepcionista IA. Token de operador.</p>
    <input id="gateInput" type="password" placeholder="Token" aria-label="Token de operador">
    <button class="btn" type="button" id="gateBtn">Entrar</button>
    <div class="err" id="gateErr"></div>
  </div>
</div>

<div class="header">
<h1><span class="logo">V</span> Recepcionista IA para WhatsApp</h1>
<a class="home" href="/torre-control">Volver a la Torre</a>
</div>

<div class="grid">
  <div class="card">
    <h2>Configurar una recepcionista</h2>
    <label for="f-client">Nombre del cliente</label>
    <input id="f-client" placeholder="Ej. Clinica Sonrisas">
    <label for="f-company">Empresa (como la llamas en el chat)</label>
    <input id="f-company" placeholder="Clinica Sonrisas">
    <label for="f-wa">WhatsApp atendido</label>
    <input id="f-wa" placeholder="+57 300 000 0000">
    <label for="f-services">Servicios (separados por coma)</label>
    <input id="f-services" placeholder="Ortodoncia, Blanqueamiento, Implantes">
    <label for="f-hours">Horario</label>
    <input id="f-hours" placeholder="lunes a viernes de 8 a.m. a 6 p.m.">
    <label for="f-price">Que puede decir sobre precios</label>
    <textarea id="f-price" placeholder="La valoracion inicial es gratuita; los tratamientos se cotizan tras el diagnostico."></textarea>
    <label for="f-faqs">Preguntas frecuentes (una por linea: pregunta | respuesta)</label>
    <textarea id="f-faqs" placeholder="Aceptan tarjetas? | Si, aceptamos tarjetas y cuotas."></textarea>
    <label for="f-esc">Telefono para escalar a un humano</label>
    <input id="f-esc" placeholder="+57 300 000 0000">
    <label for="f-transport">Transporte de envio</label>
    <select id="f-transport">
      <option value="log">Solo registrar (seguro, no envia)</option>
      <option value="hermes">Enviar por el bot de Veyra (piloto)</option>
    </select>
    <label for="f-provider">Conexion de WhatsApp</label>
    <select id="f-provider">
      <option value="log">Sin conexion (solo probar)</option>
      <option value="cloud">WhatsApp Cloud API (oficial, no depende de nosotros)</option>
      <option value="hermes">Bot de Veyra (piloto, requiere nuestro equipo encendido)</option>
    </select>
    <label for="f-pid">Numero del cliente en Meta (phone_number_id) - solo Cloud API</label>
    <input id="f-pid" placeholder="Ej. 123456789012345">
    <label for="f-tokenenv">Nombre de la variable con SU token - solo Cloud API</label>
    <input id="f-tokenenv" placeholder="Ej. WA_TOKEN_CLINICA_SONRISAS">
    <button class="btn" type="button" id="save">Guardar recepcionista</button>
    <div class="err" id="saveMsg" style="margin-top:8px"></div>
  </div>

  <div class="card">
    <h2>Recepcionistas configuradas</h2>
    <div id="list"><div class="meta" style="color:var(--muted);font-size:12px">Cargando...</div></div>
  </div>
</div>

<div class="foot">La recepcionista responde con la informacion del cliente, agenda y escala a un humano. Con <b>WhatsApp Cloud API</b> atiende el numero del cliente sin depender de ningun equipo nuestro encendido.</div>

<script>
let TOKEN = (new URLSearchParams(location.search).get('token') || sessionStorage.getItem('veyra_ops_token') || '').trim();
if (new URLSearchParams(location.search).get('token')) { try { sessionStorage.setItem('veyra_ops_token', TOKEN); } catch(_){} }
const $ = (id) => document.getElementById(id);
function showGate(msg){ $('gate').style.display='flex'; $('gateErr').textContent = msg||''; }
function hideGate(){ $('gate').style.display='none'; }
function headers(extra){ return Object.assign({ 'X-Veyra-Token': TOKEN }, extra||{}); }

async function api(path, opts){
  const r = await fetch('/torre-control' + path, Object.assign({ headers: headers(opts && opts.body ? {'Content-Type':'application/json'} : {}) }, opts||{}));
  if (r.status === 401) { TOKEN=''; sessionStorage.removeItem('veyra_ops_token'); showGate('Token incorrecto.'); return null; }
  return r.json().catch(() => ({}));
}

function parseFaqs(txt){
  return (txt||'').split('\\n').map((l)=>l.trim()).filter(Boolean).map((l)=>{
    const i = l.indexOf('|');
    return i>0 ? { pregunta: l.slice(0,i).trim(), respuesta: l.slice(i+1).trim() } : { pregunta: l, respuesta: '' };
  }).filter((f)=>f.pregunta);
}

async function load(){
  if(!TOKEN){ showGate(''); return; }
  const data = await api('/receptionists');
  if(!data) return;
  hideGate();
  const items = data.items || [];
  const host = $('list');
  if(!items.length){ host.innerHTML = '<div style="color:#666;font-size:12px">Todavia no hay ninguna configurada.</div>'; return; }
  host.innerHTML = items.map((it)=>`
    <div class="item">
      <div class="name">${it.company || it.client_name} <span class="pill ${it.active?'on':'off'}">${it.active?'Activa':'Pausada'}</span></div>
      <div class="meta">Cliente: ${it.client_name}${it.whatsapp_number?' · '+it.whatsapp_number:''}<br>Horario: ${it.hours||'-'}<br>Escala a: ${it.escalation_phone||'-'}</div>
      <input placeholder="Escribe un mensaje de prueba..." data-test="${it.id}" style="margin-top:8px">
      <button class="btn sec" type="button" data-run="${it.id}">Probar respuesta</button>
      <div class="reply" id="r-${it.id}" style="display:none"></div>
    </div>`).join('');
  host.querySelectorAll('[data-run]').forEach((b)=>b.addEventListener('click', ()=>probar(b.getAttribute('data-run'))));
}

async function probar(id){
  const msg = document.querySelector('[data-test="'+id+'"]').value.trim();
  if(!msg) return;
  const box = $('r-'+id);
  box.style.display='block'; box.textContent='Pensando...';
  const data = await api('/receptionists/'+id+'/reply', { method:'POST', body: JSON.stringify({ message: msg }) });
  if(!data){ box.textContent='Sin acceso.'; return; }
  box.innerHTML = data.reply + (data.escalate ? '\\n\\n<span class="esc">-> ESCALA A HUMANO: '+data.reason+'</span>' : '');
}

async function guardar(){
  const body = {
    client_name: $('f-client').value.trim(),
    company: $('f-company').value.trim(),
    whatsapp_number: $('f-wa').value.trim(),
    services: $('f-services').value.split(',').map((s)=>({nombre:s.trim()})).filter((s)=>s.nombre),
    hours: $('f-hours').value.trim(),
    price_policy: $('f-price').value.trim(),
    faqs: parseFaqs($('f-faqs').value),
    escalation_phone: $('f-esc').value.trim(),
    transport: $('f-transport').value,
    provider: $('f-provider').value,
    phone_number_id: $('f-pid').value.trim(),
    access_token_env: $('f-tokenenv').value.trim(),
  };
  if(!body.client_name){ $('saveMsg').textContent='Falta el nombre del cliente.'; return; }
  const data = await api('/receptionists', { method:'POST', body: JSON.stringify(body) });
  if(!data) return;
  if(data.status === 'OK'){
    $('saveMsg').style.color='var(--green)'; $('saveMsg').textContent='Guardada.';
    ['f-client','f-company','f-wa','f-services','f-hours','f-price','f-faqs','f-esc','f-pid','f-tokenenv'].forEach((k)=>$(k).value='');
    load();
  } else {
    $('saveMsg').style.color='var(--red)'; $('saveMsg').textContent = data.detail || 'No se pudo guardar.';
  }
}

$('gateBtn').addEventListener('click', ()=>{ TOKEN=$('gateInput').value.trim(); sessionStorage.setItem('veyra_ops_token', TOKEN); load(); });
$('gateInput').addEventListener('keydown', (e)=>{ if(e.key==='Enter') $('gateBtn').click(); });
$('save').addEventListener('click', guardar);
load();
</script>
</body>
</html>
"""
