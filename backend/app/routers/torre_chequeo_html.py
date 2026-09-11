"""Página del kanban de solicitudes del Chequeo Express (Torre de Control).

Se sirve en /torre-control/chequeo y consume /torre-control/chequeo/data.
Estilo consistente con el dashboard de la Torre (tema oscuro).
"""

CHEQUEO_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chequeo Express - Solicitudes | VEYRA</title>
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
.header{display:flex;align-items:center;justify-content:space-between;padding:16px 0;border-bottom:1px solid var(--border);margin-bottom:22px;flex-wrap:wrap;gap:14px}
.header h1{font-size:22px;font-weight:700}
.header h1 .logo{color:var(--accent)}
.header a.home{color:var(--muted);font-size:12px;text-decoration:none;border:1px solid var(--border);padding:8px 12px;border-radius:8px}
.header a.home:hover{border-color:var(--accent);color:var(--fg)}
.btn{background:0 0;border:1px solid var(--border);color:var(--fg);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}
.btn:hover{border-color:var(--accent)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:22px}
.metric{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}
.metric .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.metric .v{font-size:26px;font-weight:700;margin-top:4px}
.board{display:flex;gap:14px;overflow-x:auto;padding-bottom:16px;align-items:flex-start}
.col{flex:0 0 260px;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;min-height:120px}
.col h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:10px;display:flex;justify-content:space-between}
.col h2 span{color:var(--fg)}
.card{border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:10px;background:#0d0d0d}
.card:hover{border-color:var(--accent)}
.card .name{font-weight:700;font-size:14px;margin-bottom:6px}
.chip{display:inline-block;font-size:10px;font-weight:700;padding:3px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.chip.fragil{background:rgba(255,23,68,.15);color:var(--red);border:1px solid var(--red)}
.chip.construccion{background:rgba(255,214,0,.15);color:var(--yellow);border:1px solid var(--yellow)}
.chip.solido{background:rgba(0,200,83,.15);color:var(--green);border:1px solid var(--green)}
.perfil{font-size:12px;font-weight:700;margin-bottom:6px;line-height:1.3}
.meta{font-size:11px;color:var(--muted);line-height:1.6}
.meta strong{color:var(--fg)}
.actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.actions a,.actions button{font-size:10px;font-weight:700;text-transform:uppercase;text-decoration:none;padding:6px 9px;border-radius:6px;border:1px solid var(--border);color:var(--fg);background:0 0;cursor:pointer}
.actions a.wa{background:var(--green);color:#000;border-color:var(--green)}
.actions a.report{border-color:var(--blue);color:var(--blue)}
.actions button.move{border-color:var(--accent);color:var(--accent)}
.actions a:hover,.actions button:hover{opacity:.85}
.empty{color:var(--muted);font-size:12px;padding:10px 0}
.foot{margin-top:20px;color:var(--muted);font-size:11px}
.gate{position:fixed;inset:0;background:rgba(5,5,5,.96);display:flex;align-items:center;justify-content:center;z-index:50;padding:20px}
.gate .box{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:28px;width:min(380px,100%)}
.gate h2{font-size:16px;margin-bottom:6px}
.gate p{color:var(--muted);font-size:12px;margin-bottom:16px}
.gate input{width:100%;padding:13px 14px;border:1px solid var(--border);border-radius:8px;background:#0d0d0d;color:var(--fg);font-size:15px;outline:none}
.gate input:focus{border-color:var(--accent)}
.gate button{margin-top:12px;width:100%;padding:13px;border:none;border-radius:8px;background:var(--accent);color:#000;font-weight:800;font-size:13px;text-transform:uppercase;cursor:pointer}
.gate .err{color:var(--red);font-size:12px;margin-top:10px;min-height:16px}
</style>
</head>
<body>

<div class="gate" id="gate" style="display:none">
  <div class="box">
    <h2>Acceso privado</h2>
    <p>Solicitudes del Chequeo Express. Ingresa el token de operador.</p>
    <input id="gateInput" type="password" placeholder="Token de operador" autocomplete="current-password" aria-label="Token de operador">
    <button type="button" id="gateBtn">Entrar</button>
    <div class="err" id="gateErr"></div>
  </div>
</div>
<div class="header">
<h1><span class="logo">V</span> Solicitudes del Chequeo Express</h1>
<div style="display:flex;gap:10px;align-items:center">
<button class="btn" onclick="load()">Refrescar</button>
<a class="home" href="/torre-control">Volver a la Torre</a>
</div>
</div>

<div class="metrics" id="metrics"></div>
<div class="board" id="board"></div>
<div class="foot" id="foot">Cargando…</div>

<script>
const STAGES = [
  {k:'nuevo', label:'Nuevo'},
  {k:'contactado', label:'Contactado'},
  {k:'agendado', label:'Agendado'},
  {k:'llamada', label:'Videollamada'},
  {k:'informe', label:'Informe enviado'},
  {k:'ganado', label:'Ganado'},
  {k:'perdido', label:'Perdido'}
];
const SITE = 'https://veyrasoluciones.com';
let DATA = [];
let TOKEN = (new URLSearchParams(location.search).get('token') || sessionStorage.getItem('veyra_ops_token') || '').trim();
if (new URLSearchParams(location.search).get('token')) { try { sessionStorage.setItem('veyra_ops_token', TOKEN); } catch(_){} }

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function showGate(msg){
  document.getElementById('gate').style.display='flex';
  document.getElementById('gateErr').textContent = msg || '';
  const i = document.getElementById('gateInput');
  if (i) i.focus();
}
function hideGate(){ document.getElementById('gate').style.display='none'; }

function authHeaders(){ return TOKEN ? {'X-Veyra-Token': TOKEN} : {}; }

function submitGate(){
  const v = (document.getElementById('gateInput').value || '').trim();
  if(!v){ document.getElementById('gateErr').textContent='Escribe el token.'; return; }
  TOKEN = v;
  sessionStorage.setItem('veyra_ops_token', v);
  load();
}

function waMessage(it){
  return 'Hola ' + (it.name||'') + ', te escribo de Veyra por tu Chequeo Express. Tu diagnóstico: ' +
    (it.perfil||'') + ' (nivel ' + (it.score||'') + '/100). Tu informe: ' + SITE + '/chequeo-reporte?token=' + (it.token||'') +
    ' ¿Revisamos 15 minutos esta semana?';
}

function cardHtml(it){
  const lvl = (it.level||'').toLowerCase().replace(/ó/g,'o');
  const chipClass = lvl.indexOf('fragil')>=0?'fragil':lvl.indexOf('construccion')>=0?'construccion':'solido';
  const idx = STAGES.findIndex(s=>s.k===(it.stage||'nuevo'));
  const prev = idx>0?STAGES[idx-1].k:'';
  const next = idx>=0&&idx<STAGES.length-1?STAGES[idx+1].k:'';
  return '<div class="card">'
    + '<div class="name">'+esc(it.name||'(sin nombre)')+'</div>'
    + '<span class="chip '+chipClass+'">'+(esc(it.level)||'')+' '+esc(it.score)+'/100</span>'
    + '<div class="perfil">'+esc(it.perfil||'')+'</div>'
    + '<div class="meta">Fuga: <strong>'+esc(it.fuga||'—')+'</strong><br>'
    + 'WhatsApp: <strong>'+esc(it.phone_display||it.phone||'—')+'</strong><br>'
    + 'Recibido: '+esc((it.created_at||'').slice(0,16).replace('T',' '))+'<br>'
    + (it.whatsapp_sent_at?'Bot envió: '+esc((it.whatsapp_sent_at||'').slice(0,16).replace('T',' ')):'Bot: pendiente')+'</div>'
    + '<div class="actions">'
    + '<a class="wa" href="https://wa.me/'+esc(it.phone)+'?text='+encodeURIComponent(waMessage(it))+'" target="_blank" rel="noopener">WhatsApp</a>'
    + (it.token?'<a class="report" href="'+SITE+'/chequeo-reporte?token='+esc(it.token)+'" target="_blank" rel="noopener">Informe</a>':'')
    + (prev?'<button class="move" onclick="move(\\''+esc(it.intake_id)+'\\',\\''+prev+'\\')">← '+STAGES[idx-1].label+'</button>':'')
    + (next?'<button class="move" onclick="move(\\''+esc(it.intake_id)+'\\',\\''+next+'\\')">'+STAGES[idx+1].label+' →</button>':'')
    + '</div></div>';
}

function render(){
  const counts = {};
  STAGES.forEach(s=>counts[s.k]=0);
  DATA.forEach(it=>{const k=it.stage||'nuevo'; if(counts[k]===undefined)counts[k]=0; counts[k]++});
  document.getElementById('metrics').innerHTML =
    '<div class="metric"><div class="k">Solicitudes</div><div class="v">'+DATA.length+'</div></div>'
    + '<div class="metric"><div class="k">Nuevas</div><div class="v">'+(counts['nuevo']||0)+'</div></div>'
    + '<div class="metric"><div class="k">Contactadas</div><div class="v">'+(counts['contactado']||0)+'</div></div>'
    + '<div class="metric"><div class="k">Agendadas</div><div class="v">'+(counts['agendado']||0)+'</div></div>'
    + '<div class="metric"><div class="k">Ganadas</div><div class="v">'+(counts['ganado']||0)+'</div></div>';

  document.getElementById('board').innerHTML = STAGES.map(s=>{
    const items = DATA.filter(it=>(it.stage||'nuevo')===s.k);
    return '<div class="col"><h2>'+s.label+' <span>'+items.length+'</span></h2>'
      + (items.length?items.map(cardHtml).join(''):'<div class="empty">Sin solicitudes</div>')
      + '</div>';
  }).join('');
  document.getElementById('foot').textContent = 'Actualizado ' + new Date().toLocaleTimeString('es-CO') + ' · arrastra con los botones de flecha para mover de etapa';
}

async function move(id, stage){
  try{
    const r = await fetch('/torre-control/chequeo/'+encodeURIComponent(id)+'/stage', {
      method:'POST', headers: Object.assign({'Content-Type':'application/json'}, authHeaders()), body: JSON.stringify({stage})
    });
    if(r.status===401){ TOKEN=''; sessionStorage.removeItem('veyra_ops_token'); showGate('Token incorrecto.'); return; }
    if(!r.ok) throw new Error('HTTP '+r.status);
    await load();
  }catch(e){ alert('No se pudo mover: '+e.message); }
}

async function load(){
  if(!TOKEN){ showGate(''); return; }
  try{
    const r = await fetch('/torre-control/chequeo/data', {cache:'no-store', headers: authHeaders()});
    if(r.status===401){ TOKEN=''; sessionStorage.removeItem('veyra_ops_token'); showGate('Token incorrecto.'); return; }
    if(!r.ok) throw new Error('HTTP '+r.status);
    const j = await r.json();
    DATA = j.items || [];
    render();
    hideGate();
  }catch(e){
    document.getElementById('foot').textContent = 'Error cargando: '+e.message;
  }
}

document.getElementById('gateBtn').addEventListener('click', submitGate);
document.getElementById('gateInput').addEventListener('keydown', function(e){ if(e.key==='Enter') submitGate(); });

load();
setInterval(function(){ if(TOKEN) load(); }, 15000);
</script>
</body>
</html>
"""
