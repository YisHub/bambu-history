import json
import os
import socket
import sys
from datetime import datetime
import requests

# ── CONFIG ────────────────────────────────────────────────────────────────────
EMAIL      = os.environ["BAMBU_EMAIL"]
PASSWORD   = os.environ["BAMBU_PASSWORD"]
DEVICE_ID  = os.getenv("BAMBU_DEVICE_ID", "")
LIMIT      = int(os.getenv("LIMIT", "100"))
SAVE_JSON  = os.getenv("SAVE_JSON", "1") == "1"
OUTPUT_DIR = "/output"
TOKEN_FILE = f"{OUTPUT_DIR}/.bambu_token"
JSON_FILE  = f"{OUTPUT_DIR}/historial.json"
HTML_FILE  = f"{OUTPUT_DIR}/historial.html"
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://api.bambulab.com"
STATUS = {0: "Desconocido", 1: "En progreso", 2: "Completado", 3: "Fallido", 4: "Cancelado"}


# ── TOKEN ─────────────────────────────────────────────────────────────────────

def save_token(token: str):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token, "saved_at": datetime.now().isoformat()}, f)
    print("  Token guardado en disco.")

def load_token() -> str | None:
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f).get("token")

def test_token(token: str) -> bool:
    try:
        r = requests.get(
            f"{BASE_URL}/v1/user-service/my/tasks",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1}, timeout=10,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False

def get_token() -> str:
    saved = load_token()
    if saved:
        print("Token guardado encontrado, verificando...", end=" ")
        if test_token(saved):
            print("válido.\n")
            return saved
        print("expirado, re-autenticando...")

    token = do_login()
    save_token(token)
    print()
    return token


# ── AUTH ──────────────────────────────────────────────────────────────────────

def do_login() -> str:
    print("Iniciando sesión en Bambu Cloud...")
    r = requests.post(
        f"{BASE_URL}/v1/user-service/user/login",
        json={"account": EMAIL, "password": PASSWORD}, timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("accessToken")

    if not token and data.get("loginType") == "verifyCode":
        print(f"Verificación requerida. Enviando código a {EMAIL}...")
        requests.post(
            f"{BASE_URL}/v1/user-service/user/sendemail/code",
            json={"email": EMAIL, "type": "codeLogin"}, timeout=15,
        ).raise_for_status()
        print("Código enviado. Revisá tu email.\n")
        code = input("Código de 6 dígitos: ").strip()
        r3 = requests.post(
            f"{BASE_URL}/v1/user-service/user/login",
            json={"account": EMAIL, "code": code}, timeout=15,
        )
        r3.raise_for_status()
        token = r3.json().get("accessToken")

    if not token:
        print("Error: no se pudo obtener el token.", data)
        sys.exit(1)

    print("Sesión iniciada OK")
    return token


# ── DATOS ─────────────────────────────────────────────────────────────────────

def get_tasks(token: str) -> list:
    """
    Pagina con offset — el parámetro 'after' del API de Bambu está roto
    y devuelve siempre la primera página sin importar el valor que se le pase.
    """
    headers = {"Authorization": f"Bearer {token}"}
    tasks  = []
    offset = 0

    while len(tasks) < LIMIT:
        page_size = min(LIMIT - len(tasks), 50)
        params = {"limit": page_size, "offset": offset}
        if DEVICE_ID:
            params["deviceId"] = DEVICE_ID

        r = requests.get(
            f"{BASE_URL}/v1/user-service/my/tasks",
            headers=headers, params=params, timeout=15,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])

        if not hits:
            break

        tasks.extend(hits)
        offset += len(hits)

        if len(hits) < page_size:
            break

    return tasks


# ── HTML ──────────────────────────────────────────────────────────────────────

def generate_html(tasks: list) -> str:
    tasks_json = json.dumps(tasks, ensure_ascii=False)
    n = len(tasks)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Bambu Print History</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f0f0f;color:#e0e0e0;font-family:system-ui,sans-serif}}

/* ── Header ── */
header{{
  position:sticky;top:0;z-index:200;
  background:#1a1a1a;border-bottom:1px solid #2a2a2a;
  padding:11px 22px;display:flex;align-items:center;gap:10px;flex-wrap:wrap
}}
header h1{{font-size:1rem;font-weight:600;color:#fff;flex:1;white-space:nowrap}}
.btn{{padding:6px 13px;border-radius:6px;border:none;cursor:pointer;
      font-size:.8rem;font-weight:500;transition:background .15s}}
.btn-ghost{{background:#252525;color:#bbb}}.btn-ghost:hover{{background:#333}}
.btn-stats{{background:#1a2a1a;color:#4ade80}}.btn-stats:hover{{background:#223322}}
.btn-stats.active{{background:#0d3d1a;color:#4ade80;outline:1px solid #4ade8055}}

/* ── Panel de Estadísticas Globales ── */
#stats-global{{
  display:none;background:#111;border-bottom:1px solid #252525;padding:22px 24px
}}
#stats-global.open{{display:block}}

.sg-title{{font-size:.7rem;color:#555;text-transform:uppercase;
           letter-spacing:.07em;margin-bottom:14px}}
.overview-grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
  gap:10px;margin-bottom:22px
}}
.ov-card{{
  background:#181818;border:1px solid #252525;border-radius:8px;
  padding:12px 14px
}}
.ov-label{{font-size:.68rem;color:#555;text-transform:uppercase;
           letter-spacing:.05em;margin-bottom:5px}}
.ov-value{{font-size:1.05rem;font-weight:600;color:#e0e0e0}}
.ov-sub{{font-size:.72rem;color:#666;margin-top:2px}}

.charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
@media(max-width:700px){{.charts-row{{grid-template-columns:1fr}}}}

.chart-box{{background:#161616;border:1px solid #222;border-radius:8px;padding:16px}}
.chart-box h3{{font-size:.72rem;color:#666;text-transform:uppercase;
              letter-spacing:.06em;margin-bottom:14px}}
.chart-row{{display:flex;align-items:center;gap:8px;margin-bottom:9px;font-size:.78rem}}
.chart-label{{width:90px;color:#aaa;overflow:hidden;text-overflow:ellipsis;
              white-space:nowrap;flex-shrink:0}}
.chart-bar-wrap{{flex:1;background:#222;border-radius:3px;height:6px}}
.chart-bar{{height:6px;border-radius:3px;background:#00aaff;transition:width .4s}}
.chart-val{{width:70px;text-align:right;color:#666;flex-shrink:0}}
.color-label-dot{{width:12px;height:12px;border-radius:50%;flex-shrink:0}}

/* ── Filter bar ── */
#filter-bar{{
  background:#141414;border-bottom:1px solid #202020;
  padding:9px 22px;display:flex;gap:18px;flex-wrap:wrap;align-items:center
}}
.filter-group{{display:flex;align-items:center;gap:7px;flex-wrap:wrap}}
.filter-label{{font-size:.68rem;color:#444;text-transform:uppercase;
              letter-spacing:.05em;white-space:nowrap}}
.filter-pill{{
  padding:3px 11px;border-radius:20px;border:1px solid #2a2a2a;
  background:#1a1a1a;color:#888;cursor:pointer;font-size:.76rem;transition:all .15s
}}
.filter-pill:hover{{border-color:#444;color:#ccc}}
.filter-pill.active{{background:#00aaff18;border-color:#00aaff;color:#00aaff}}
.color-dot{{
  width:20px;height:20px;border-radius:50%;cursor:pointer;
  border:2px solid #2a2a2a;transition:transform .15s,border-color .15s;flex-shrink:0
}}
.color-dot:hover{{transform:scale(1.2)}}
.color-dot.active{{border-color:#fff;transform:scale(1.25)}}

/* ── Barra de selección ── */
#sel-bar{{
  background:#131313;border-bottom:1px solid #1e1e1e;
  padding:9px 22px;display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start
}}
.stat{{display:flex;flex-direction:column;gap:2px}}
.stat-label{{color:#444;font-size:.67rem;text-transform:uppercase;letter-spacing:.05em}}
.stat-value{{color:#fff;font-size:1rem;font-weight:600}}
#sel-bar.empty .stat-value{{color:#2a2a2a}}
.filter-note{{font-size:.7rem;color:#00aaff88;align-self:flex-end;padding-bottom:2px}}

/* ── Breakdown (selección) ── */
#breakdown{{display:none;background:#0e0e0e;border-bottom:1px solid #1e1e1e;padding:14px 24px}}
#breakdown.visible{{display:flex;gap:30px;flex-wrap:wrap}}
.bd-section h3{{font-size:.68rem;color:#444;text-transform:uppercase;
               letter-spacing:.06em;margin-bottom:10px}}
.bd-row{{display:flex;align-items:center;gap:8px;font-size:.78rem;margin-bottom:6px}}
.bd-dot{{width:11px;height:11px;border-radius:50%;flex-shrink:0}}
.bd-name{{color:#aaa;min-width:55px}}
.bd-bar-wrap{{width:75px;background:#1e1e1e;border-radius:3px;height:4px}}
.bd-bar{{height:4px;border-radius:3px;background:#00aaff}}
.bd-val{{color:#666;font-size:.73rem;white-space:nowrap}}

/* ── Grid ── */
#grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));
  gap:13px;padding:18px
}}
.card{{
  background:#181818;border-radius:9px;overflow:hidden;
  border:2px solid transparent;cursor:pointer;
  transition:border-color .15s,transform .1s;user-select:none
}}
.card:hover{{transform:translateY(-2px);border-color:#2a2a2a}}
.card.selected{{border-color:#00aaff;background:#0f1c29}}
.card.hidden{{display:none}}
.card-img-wrap{{position:relative}}
.card-img{{width:100%;aspect-ratio:1;object-fit:cover;background:#111;display:block}}
.card-img-placeholder{{
  width:100%;aspect-ratio:1;background:#111;
  display:flex;align-items:center;justify-content:center;font-size:2rem;color:#222
}}
.check-icon{{
  position:absolute;top:7px;right:7px;width:20px;height:20px;border-radius:50%;
  background:#00aaff;color:#000;display:none;align-items:center;
  justify-content:center;font-size:12px;font-weight:bold
}}
.card.selected .check-icon{{display:flex}}
.card-body{{padding:10px}}
.card-title{{
  font-size:.81rem;font-weight:500;color:#ccc;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:6px
}}
.card-meta{{display:flex;flex-direction:column;gap:3px;font-size:.74rem;color:#666}}
.card-meta span{{display:flex;align-items:center;gap:5px}}
.filament-dots{{display:flex;gap:4px;flex-wrap:wrap;margin-top:7px}}
.fdot{{
  display:flex;align-items:center;gap:3px;background:#202020;
  border-radius:20px;padding:2px 6px 2px 3px;font-size:.68rem;color:#888
}}
.fdot-c{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.badge{{display:inline-block;padding:2px 7px;border-radius:20px;font-size:.66rem;font-weight:600}}
.badge-2{{background:#0d3d1a;color:#4ade80}}
.badge-3{{background:#3d1010;color:#f87171}}
.badge-1{{background:#1a2a3d;color:#60a5fa}}
.badge-0,.badge-4{{background:#222;color:#555}}
</style>
</head>
<body>

<!-- ── Header ── -->
<header>
  <h1>🖨️ Bambu Print History</h1>
  <button class="btn btn-stats" id="btn-stats" onclick="toggleStats()">📊 Estadísticas</button>
  <button class="btn btn-ghost" onclick="selectVisible()">Sel. visibles</button>
  <button class="btn btn-ghost" onclick="clearAll()">Limpiar</button>
  <span id="hdr-count" style="color:#444;font-size:.8rem">{n} impresiones</span>
</header>

<!-- ── Panel estadísticas globales ── -->
<div id="stats-global">
  <div class="sg-title">Estadísticas globales — {n} impresiones</div>
  <div class="overview-grid" id="overview-grid"></div>
  <div class="charts-row">
    <div class="chart-box">
      <h3>Filamento por tipo</h3>
      <div id="chart-type"></div>
    </div>
    <div class="chart-box">
      <h3>Top colores</h3>
      <div id="chart-color"></div>
    </div>
  </div>
</div>

<!-- ── Filtros ── -->
<div id="filter-bar">
  <div class="filter-group">
    <span class="filter-label">Tipo</span>
    <span id="fp-all" class="filter-pill active" onclick="setFilFilter(null)">Todos</span>
  </div>
  <div class="filter-group">
    <span class="filter-label">Color</span>
    <span id="cp-all" class="filter-pill active" onclick="setColFilter(null)">Todos</span>
  </div>
</div>

<!-- ── Barra de selección ── -->
<div id="sel-bar" class="empty">
  <div class="stat"><span class="stat-label">Seleccionadas</span><span class="stat-value" id="s-count">—</span></div>
  <div class="stat"><span class="stat-label">Tiempo total</span><span class="stat-value" id="s-time">—</span></div>
  <div class="stat"><span class="stat-label">Promedio</span><span class="stat-value" id="s-avg">—</span></div>
  <div class="stat">
    <span class="stat-label" id="s-grams-label">Filamento total</span>
    <span class="stat-value" id="s-grams">—</span>
  </div>
  <div class="stat"><span class="stat-label">Completadas</span><span class="stat-value" id="s-ok">—</span></div>
  <span class="filter-note" id="filter-note" style="display:none">⚑ Solo filamento filtrado</span>
</div>

<!-- ── Breakdown por selección ── -->
<div id="breakdown">
  <div class="bd-section"><h3>Por tipo</h3><div id="bd-type"></div></div>
  <div class="bd-section"><h3>Por color</h3><div id="bd-color"></div></div>
</div>

<!-- ── Grid de cards ── -->
<div id="grid"></div>

<script>
const tasks = {tasks_json};
const STATUS = {{0:"Desconocido",1:"En progreso",2:"Completado",3:"Fallido",4:"Cancelado"}};

// ── Utils ────────────────────────────────────────────────────────────────────
function parseColor(c) {{
  if (!c) return null;
  return '#' + c.toString().replace('#','').slice(0,6).toUpperCase();
}}
function fmtDuration(s) {{
  if (!s) return "—";
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return h + "h " + String(m).padStart(2,"0") + "m";
}}
function fmtDate(iso) {{
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("es-AR") + " " + d.toLocaleTimeString("es-AR",{{hour:"2-digit",minute:"2-digit"}});
}}
function fmtGrams(g) {{
  if (g === 0) return "0g";
  return g >= 1000 ? (g/1000).toFixed(2)+" kg" : g.toFixed(1)+"g";
}}
function luminance(hex) {{
  const r=parseInt(hex.slice(1,3),16)/255, g=parseInt(hex.slice(3,5),16)/255, b=parseInt(hex.slice(5,7),16)/255;
  return 0.299*r+0.587*g+0.114*b;
}}

// ── FIX: gramos respetando el filtro activo ──────────────────────────────────
function getFilteredGrams(task) {{
  if (!activeFil && !activeCol) return task.weight || 0;
  return (task.amsDetailMapping || [])
    .filter(a => {{
      const typeOk = !activeFil || a.filamentType === activeFil;
      const colOk  = !activeCol || parseColor(a.sourceColor) === activeCol;
      return typeOk && colOk;
    }})
    .reduce((sum, a) => sum + (a.weight || 0), 0);
}}

// ── Estadísticas globales ────────────────────────────────────────────────────
function buildGlobalStats() {{
  const byType  = new Map();
  const byColor = new Map();
  let totalSecs = 0, totalGrams = 0, completed = 0, failed = 0;

  tasks.forEach(t => {{
    totalSecs  += t.costTime || 0;
    totalGrams += t.weight   || 0;
    if (t.status === 2) completed++;
    if (t.status === 3) failed++;
    (t.amsDetailMapping || []).forEach(a => {{
      const type = a.filamentType || 'Desconocido';
      const hex  = parseColor(a.sourceColor) || '#555555';
      const w    = a.weight || 0;
      const pt   = byType.get(type)  || {{g:0, count:0}};
      pt.g += w; pt.count++; byType.set(type, pt);
      const pc   = byColor.get(hex)  || {{g:0, count:0, hex, type}};
      pc.g += w; pc.count++; byColor.set(hex, pc);
    }});
  }});

  const successPct = tasks.length ? Math.round(completed/tasks.length*100) : 0;
  const topType    = [...byType.entries()].sort((a,b)=>b[1].g-a[1].g)[0];
  const topColor   = [...byColor.entries()].sort((a,b)=>b[1].g-a[1].g)[0];

  // Overview cards
  const cards = [
    {{label:"Total impresiones", value: tasks.length, sub:"en este historial"}},
    {{label:"Tiempo total",      value: fmtDuration(totalSecs), sub: "de impresión"}},
    {{label:"Filamento total",   value: fmtGrams(totalGrams), sub:"en todos los trabajos"}},
    {{label:"Tasa de éxito",     value: successPct+"%", sub: completed+" completadas"}},
    {{label:"Fallos",            value: failed, sub: Math.round(failed/tasks.length*100)+"% del total"}},
    {{label:"Tipo más usado",    value: topType ? topType[0] : "—",
                                 sub: topType ? fmtGrams(topType[1].g) : ""}},
    {{label:"Color más usado",   value: topColor ? fmtGrams(topColor[1].g) : "—",
                                 sub: topColor ? topColor[1].type : "",
                                 dot: topColor ? topColor[0] : null}},
    {{label:"Colores distintos", value: byColor.size, sub: byType.size+" tipos de filamento"}},
  ];

  document.getElementById('overview-grid').innerHTML = cards.map(c => `
    <div class="ov-card">
      <div class="ov-label">${{c.label}}</div>
      <div class="ov-value" style="display:flex;align-items:center;gap:6px">
        ${{c.dot ? `<span style="width:14px;height:14px;border-radius:50%;background:${{c.dot}};flex-shrink:0"></span>` : ''}}
        ${{c.value}}
      </div>
      ${{c.sub ? `<div class="ov-sub">${{c.sub}}</div>` : ''}}
    </div>`).join('');

  // Chart tipos
  const sortedTypes  = [...byType.entries()].sort((a,b)=>b[1].g-a[1].g);
  const maxTypeG     = sortedTypes[0]?.[1].g || 1;
  document.getElementById('chart-type').innerHTML = sortedTypes.map(([type, v]) => `
    <div class="chart-row">
      <span class="chart-label" title="${{type}}">${{type}}</span>
      <div class="chart-bar-wrap">
        <div class="chart-bar" style="width:${{Math.round(v.g/maxTypeG*100)}}%"></div>
      </div>
      <span class="chart-val">${{fmtGrams(v.g)}}</span>
    </div>`).join('');

  // Chart colores (top 15)
  const sortedColors = [...byColor.entries()].sort((a,b)=>b[1].g-a[1].g).slice(0,15);
  const maxColorG    = sortedColors[0]?.[1].g || 1;
  document.getElementById('chart-color').innerHTML = sortedColors.map(([hex, v]) => `
    <div class="chart-row">
      <span class="color-label-dot" style="background:${{hex}};border:1px solid #333"></span>
      <span class="chart-label" title="${{v.type}} ${{hex}}">${{v.type}} <span style="color:#444;font-size:.65rem">${{hex}}</span></span>
      <div class="chart-bar-wrap">
        <div class="chart-bar" style="width:${{Math.round(v.g/maxColorG*100)}}%;background:${{hex}}"></div>
      </div>
      <span class="chart-val">${{fmtGrams(v.g)}}</span>
    </div>`).join('');
}}

function toggleStats() {{
  const panel = document.getElementById('stats-global');
  const btn   = document.getElementById('btn-stats');
  const open  = panel.classList.toggle('open');
  btn.classList.toggle('active', open);
}}

// ── Filtros ──────────────────────────────────────────────────────────────────
let activeFil = null, activeCol = null;

function buildFilters() {{
  const byType  = new Map();
  const byColor = new Map();
  tasks.forEach(t => {{
    (t.amsDetailMapping || []).forEach(a => {{
      if (a.filamentType) byType.set(a.filamentType, true);
      const hex = parseColor(a.sourceColor);
      if (hex) byColor.set(hex, a.filamentType || '');
    }});
  }});

  const fg = document.querySelector('#filter-bar .filter-group:nth-child(1)');
  byType.forEach((_, type) => {{
    const p = document.createElement('span');
    p.className = 'filter-pill'; p.textContent = type;
    p.id = 'fp-'+type; p.onclick = () => setFilFilter(type);
    fg.appendChild(p);
  }});

  const cg = document.querySelector('#filter-bar .filter-group:nth-child(2)');
  byColor.forEach((type, hex) => {{
    const d = document.createElement('div');
    d.className = 'color-dot'; d.style.background = hex;
    d.title = type + ' ' + hex; d.id = 'cp-'+hex.slice(1);
    d.onclick = () => setColFilter(hex);
    cg.appendChild(d);
  }});
}}

function setFilFilter(type) {{
  activeFil = type;
  document.querySelectorAll('#filter-bar .filter-pill').forEach(p => p.classList.remove('active'));
  document.getElementById(type ? 'fp-'+type : 'fp-all').classList.add('active');
  applyFilters();
}}
function setColFilter(hex) {{
  activeCol = hex;
  document.querySelectorAll('.color-dot').forEach(d => d.classList.remove('active'));
  if (hex) document.getElementById('cp-'+hex.slice(1)).classList.add('active');
  else     document.getElementById('cp-all').classList.add('active');
  applyFilters();
}}

function taskVisible(t) {{
  const ams = t.amsDetailMapping || [];
  if (activeFil && !ams.some(a => a.filamentType === activeFil)) return false;
  if (activeCol && !ams.some(a => parseColor(a.sourceColor) === activeCol)) return false;
  return true;
}}

function applyFilters() {{
  let visible = 0;
  tasks.forEach((t, i) => {{
    const show = taskVisible(t);
    const card = document.querySelector(`[data-idx="${{i}}"]`);
    card.classList.toggle('hidden', !show);
    if (!show && selected.has(i)) {{ selected.delete(i); card.classList.remove('selected'); }}
    if (show) visible++;
  }});
  document.getElementById('hdr-count').textContent = visible + ' impresiones';

  // Actualizar label del grams en sel-bar
  const note  = document.getElementById('filter-note');
  const label = document.getElementById('s-grams-label');
  if (activeFil || activeCol) {{
    const parts = [activeFil, activeCol].filter(Boolean);
    label.textContent = 'Filamento (' + parts.join(' + ') + ')';
    note.style.display = 'inline';
  }} else {{
    label.textContent = 'Filamento total';
    note.style.display = 'none';
  }}
  updateStats();
}}

// ── Selección ────────────────────────────────────────────────────────────────
const selected = new Set();

// ── Render cards ─────────────────────────────────────────────────────────────
function renderCards() {{
  const grid = document.getElementById('grid');
  tasks.forEach((t, i) => {{
    const card = document.createElement('div');
    card.className = 'card'; card.dataset.idx = i;

    // Use card reference directly — avoids querySelector failures
    card.addEventListener('click', function() {{
      if (selected.has(i)) {{
        selected.delete(i);
        card.classList.remove('selected');
      }} else {{
        selected.add(i);
        card.classList.add('selected');
      }}
      updateStats();
    }});

    const imgHtml = t.cover
      ? `<img class="card-img" src="${{t.cover}}" alt="" loading="lazy"
             onerror="this.parentNode.innerHTML='<div class=card-img-placeholder>🖨️</div>'">`
      : `<div class="card-img-placeholder">🖨️</div>`;

    const ams = t.amsDetailMapping || [];
    const dots = ams.length
      ? '<div class="filament-dots">' + ams.map(a => {{
          const hex = parseColor(a.sourceColor) || '#555';
          const safeType = (a.filamentType||'?').replace(/</g,'&lt;');
          return `<span class="fdot">
            <span class="fdot-c" style="background:${{hex}}"></span>
            ${{safeType}} · ${{a.weight ? a.weight.toFixed(0)+'g' : '—'}}
          </span>`;
        }}).join('') + '</div>'
      : '';

    const s = t.status ?? 0;
    const safeTitle = (t.title||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
    card.innerHTML = `
      <div class="card-img-wrap">
        ${{imgHtml}}<div class="check-icon">✓</div>
      </div>
      <div class="card-body">
        <div class="card-title" title="${{safeTitle}}">${{safeTitle||'Sin nombre'}}</div>
        <div class="card-meta">
          <span><span class="badge badge-${{s}}">${{STATUS[s]||s}}</span></span>
          <span>📅 ${{fmtDate(t.startTime)}}</span>
          <span>⏱ ${{fmtDuration(t.costTime)}}</span>
        </div>
        ${{dots}}
      </div>`;
    grid.appendChild(card);
  }});
}}
function selectVisible() {{
  tasks.forEach((t, i) => {{ if (taskVisible(t)) selected.add(i); }});
  document.querySelectorAll('.card:not(.hidden)').forEach(c => c.classList.add('selected'));
  updateStats();
}}
function clearAll() {{
  selected.clear();
  document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
  updateStats();
}}

function updateStats() {{
  try {{ _updateStats(); }} catch(e) {{ console.error('updateStats error:', e); }}
}}
function _updateStats() {{
  const bar = document.getElementById('sel-bar');
  const bd  = document.getElementById('breakdown');

  if (selected.size === 0) {{
    bar.classList.add('empty');
    ['s-count','s-time','s-avg','s-grams','s-ok'].forEach(id =>
      document.getElementById(id).textContent = '—');
    bd.classList.remove('visible');
    return;
  }}
  bar.classList.remove('empty');

  const sel       = [...selected].map(i => tasks[i]);
  const totalSecs = sel.reduce((a, t) => a + (t.costTime || 0), 0);
  const completed = sel.filter(t => t.status === 2).length;

  // ── FIX: usa gramos del filamento filtrado, no el total de la tarea ──────
  const totalGrams = sel.reduce((sum, t) => sum + getFilteredGrams(t), 0);

  document.getElementById('s-count').textContent = selected.size;
  document.getElementById('s-time').textContent  = fmtDuration(totalSecs);
  document.getElementById('s-avg').textContent   = fmtDuration(Math.round(totalSecs / sel.length));
  document.getElementById('s-grams').textContent = fmtGrams(totalGrams);
  document.getElementById('s-ok').textContent    = completed + '/' + sel.length;

  // ── Breakdown ────────────────────────────────────────────────────────────
  const btType  = new Map();
  const btColor = new Map();
  sel.forEach(t => {{
    (t.amsDetailMapping || []).forEach(a => {{
      const type = a.filamentType || 'Desconocido';
      const hex  = parseColor(a.sourceColor) || '#555555';
      const w    = a.weight || 0;
      const pt   = btType.get(type)  || {{g:0}};  pt.g += w;  btType.set(type, pt);
      const pc   = btColor.get(hex)  || {{g:0, hex, type}}; pc.g += w; btColor.set(hex, pc);
    }});
  }});

  const maxTG = Math.max(...[...btType.values()].map(v=>v.g),  1);
  const maxCG = Math.max(...[...btColor.values()].map(v=>v.g), 1);

  document.getElementById('bd-type').innerHTML = [...btType.entries()]
    .sort((a,b)=>b[1].g-a[1].g).map(([type,v]) => `
      <div class="bd-row">
        <span class="bd-name">${{type}}</span>
        <div class="bd-bar-wrap"><div class="bd-bar" style="width:${{Math.round(v.g/maxTG*100)}}%"></div></div>
        <span class="bd-val">${{fmtGrams(v.g)}}</span>
      </div>`).join('');

  document.getElementById('bd-color').innerHTML = [...btColor.entries()]
    .sort((a,b)=>b[1].g-a[1].g).map(([hex,v]) => `
      <div class="bd-row">
        <span class="bd-dot" style="background:${{hex}}"></span>
        <span class="bd-name" style="color:#888">${{v.type||'?'}}</span>
        <div class="bd-bar-wrap"><div class="bd-bar" style="width:${{Math.round(v.g/maxCG*100)}}%;background:${{hex}}"></div></div>
        <span class="bd-val">${{fmtGrams(v.g)}}</span>
      </div>`).join('');

  bd.classList.add('visible');
}}

// ── Init ─────────────────────────────────────────────────────────────────────
buildGlobalStats();
buildFilters();
renderCards();
</script>
</body>
</html>"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    token = get_token()

    print(f"Obteniendo historial (máx. {LIMIT} trabajos)...")
    tasks = get_tasks(token)
    print(f"{len(tasks)} trabajos encontrados\n")

    if not tasks:
        print("Sin resultados.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if SAVE_JSON:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        print(f"JSON  → {JSON_FILE}")

    html = generate_html(tasks)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML  → {HTML_FILE}")

    port = os.getenv("VIEWER_PORT", "8765")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "localhost"
    finally:
        s.close()
    print(f"\nVisor: http://{ip}:{port}/historial.html")


if __name__ == "__main__":
    main()
