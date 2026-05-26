import json
import os
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
    headers = {"Authorization": f"Bearer {token}"}
    tasks, after = [], None

    while len(tasks) < LIMIT:
        params = {"limit": min(LIMIT - len(tasks), 50)}
        if DEVICE_ID:
            params["deviceId"] = DEVICE_ID
        if after:
            params["after"] = after

        r = requests.get(
            f"{BASE_URL}/v1/user-service/my/tasks",
            headers=headers, params=params, timeout=15,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        tasks.extend(hits)
        if len(hits) < 50:
            break
        after = hits[-1]["id"]

    return tasks


# ── HTML ──────────────────────────────────────────────────────────────────────

def generate_html(tasks: list) -> str:
    tasks_json = json.dumps(tasks, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bambu Print History</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f0f0f; color: #e0e0e0; font-family: system-ui, sans-serif; }}

  /* ── Header ── */
  header {{
    position: sticky; top: 0; z-index: 100;
    background: #1a1a1a; border-bottom: 1px solid #2a2a2a;
    padding: 12px 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }}
  header h1 {{ font-size: 1.05rem; font-weight: 600; color: #fff; flex: 1; }}
  .btn {{
    padding: 6px 14px; border-radius: 6px; border: none; cursor: pointer;
    font-size: 0.82rem; font-weight: 500; transition: background .15s;
  }}
  .btn-ghost {{ background: #2a2a2a; color: #ccc; }}
  .btn-ghost:hover {{ background: #383838; }}

  /* ── Filtros ── */
  #filter-bar {{
    background: #161616; border-bottom: 1px solid #242424;
    padding: 10px 24px; display: flex; gap: 20px; flex-wrap: wrap; align-items: center;
  }}
  .filter-group {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .filter-label {{ font-size: 0.72rem; color: #555; text-transform: uppercase; letter-spacing: .05em; white-space: nowrap; }}
  .filter-pill {{
    padding: 4px 12px; border-radius: 20px; border: 1px solid #333;
    background: #1e1e1e; color: #aaa; cursor: pointer; font-size: 0.78rem;
    transition: all .15s; white-space: nowrap;
  }}
  .filter-pill:hover {{ border-color: #555; color: #ddd; }}
  .filter-pill.active {{ background: #00aaff22; border-color: #00aaff; color: #00aaff; }}
  .color-dot {{
    width: 22px; height: 22px; border-radius: 50%; cursor: pointer;
    border: 2px solid #333; transition: transform .15s, border-color .15s;
    flex-shrink: 0;
  }}
  .color-dot:hover {{ transform: scale(1.15); }}
  .color-dot.active {{ border-color: #fff; transform: scale(1.2); }}

  /* ── Stats bar ── */
  #stats-bar {{
    background: #141414; border-bottom: 1px solid #222;
    padding: 10px 24px; display: flex; gap: 28px; flex-wrap: wrap; align-items: flex-start;
  }}
  .stat {{ display: flex; flex-direction: column; gap: 2px; }}
  .stat-label {{ color: #555; font-size: 0.7rem; text-transform: uppercase; letter-spacing: .05em; }}
  .stat-value {{ color: #fff; font-size: 1.05rem; font-weight: 600; }}
  #stats-bar.empty .stat-value {{ color: #333; }}

  /* ── Breakdown ── */
  #breakdown {{
    display: none; background: #111; border-bottom: 1px solid #222;
    padding: 14px 24px;
  }}
  #breakdown.visible {{ display: flex; gap: 32px; flex-wrap: wrap; }}
  .breakdown-section h3 {{
    font-size: 0.7rem; color: #555; text-transform: uppercase;
    letter-spacing: .06em; margin-bottom: 10px;
  }}
  .breakdown-row {{
    display: flex; align-items: center; gap: 10px;
    font-size: 0.8rem; margin-bottom: 6px;
  }}
  .breakdown-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  .breakdown-name {{ color: #bbb; min-width: 60px; }}
  .breakdown-bar-wrap {{ width: 80px; background: #222; border-radius: 3px; height: 4px; }}
  .breakdown-bar {{ height: 4px; border-radius: 3px; background: #00aaff; }}
  .breakdown-val {{ color: #888; font-size: 0.75rem; white-space: nowrap; }}

  /* ── Grid ── */
  #grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 14px; padding: 20px;
  }}
  .card {{
    background: #1a1a1a; border-radius: 10px; overflow: hidden;
    border: 2px solid transparent; cursor: pointer;
    transition: border-color .15s, transform .1s, opacity .15s;
    user-select: none;
  }}
  .card:hover {{ transform: translateY(-2px); border-color: #333; }}
  .card.selected {{ border-color: #00aaff; background: #111d26; }}
  .card.hidden {{ display: none; }}
  .card-img-wrap {{ position: relative; }}
  .card-img {{
    width: 100%; aspect-ratio: 1; object-fit: cover;
    background: #111; display: block;
  }}
  .card-img-placeholder {{
    width: 100%; aspect-ratio: 1; background: #111;
    display: flex; align-items: center; justify-content: center;
    font-size: 2.5rem; color: #2a2a2a;
  }}
  .check-icon {{
    position: absolute; top: 8px; right: 8px;
    width: 22px; height: 22px; border-radius: 50%;
    background: #00aaff; color: #000;
    display: none; align-items: center; justify-content: center;
    font-size: 13px; font-weight: bold;
  }}
  .card.selected .check-icon {{ display: flex; }}
  .card-body {{ padding: 11px; }}
  .card-title {{
    font-size: 0.83rem; font-weight: 500; color: #ddd;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    margin-bottom: 7px;
  }}
  .card-meta {{ display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: #777; }}
  .card-meta span {{ display: flex; align-items: center; gap: 6px; }}
  .filament-dots {{ display: flex; gap: 5px; flex-wrap: wrap; margin-top: 8px; }}
  .fdot {{
    display: flex; align-items: center; gap: 4px;
    background: #242424; border-radius: 20px; padding: 2px 7px 2px 4px;
    font-size: 0.7rem; color: #999;
  }}
  .fdot-circle {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}

  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 20px;
    font-size: 0.68rem; font-weight: 600;
  }}
  .badge-2 {{ background: #0d3d1a; color: #4ade80; }}
  .badge-3 {{ background: #3d1010; color: #f87171; }}
  .badge-1 {{ background: #1a2a3d; color: #60a5fa; }}
  .badge-0, .badge-4 {{ background: #252525; color: #666; }}
</style>
</head>
<body>

<header>
  <h1>🖨️ Bambu Print History</h1>
  <button class="btn btn-ghost" onclick="selectVisible()">Sel. visibles</button>
  <button class="btn btn-ghost" onclick="clearAll()">Limpiar</button>
  <span id="hdr-count" style="color:#555; font-size:.82rem">{len(tasks)} impresiones</span>
</header>

<div id="filter-bar">
  <div class="filter-group">
    <span class="filter-label">Filamento</span>
    <span id="fil-all" class="filter-pill active" onclick="setFilFilter(null)">Todos</span>
    <!-- generado por JS -->
  </div>
  <div class="filter-group">
    <span class="filter-label">Color</span>
    <span id="col-all" class="filter-pill active" onclick="setColFilter(null)">Todos</span>
    <!-- generado por JS -->
  </div>
</div>

<div id="stats-bar" class="empty">
  <div class="stat"><span class="stat-label">Seleccionadas</span><span class="stat-value" id="s-count">—</span></div>
  <div class="stat"><span class="stat-label">Tiempo total</span><span class="stat-value" id="s-time">—</span></div>
  <div class="stat"><span class="stat-label">Promedio</span><span class="stat-value" id="s-avg">—</span></div>
  <div class="stat"><span class="stat-label">Filamento total</span><span class="stat-value" id="s-grams">—</span></div>
  <div class="stat"><span class="stat-label">Completadas</span><span class="stat-value" id="s-ok">—</span></div>
</div>

<div id="breakdown">
  <div class="breakdown-section">
    <h3>Por tipo de filamento</h3>
    <div id="bd-type"></div>
  </div>
  <div class="breakdown-section">
    <h3>Por color</h3>
    <div id="bd-color"></div>
  </div>
</div>

<div id="grid"></div>

<script>
const tasks = {tasks_json};
const STATUS = {{0:"Desconocido",1:"En progreso",2:"Completado",3:"Fallido",4:"Cancelado"}};

// ── Utilidades ──────────────────────────────────────────────────────────────
function parseColor(c) {{
  if (!c) return null;
  const s = c.toString().replace('#','');
  return '#' + s.slice(0,6);
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
  return g >= 1000 ? (g/1000).toFixed(2)+" kg" : g.toFixed(1)+" g";
}}
function luminance(hex) {{
  const r = parseInt(hex.slice(1,3),16)/255;
  const g = parseInt(hex.slice(3,5),16)/255;
  const b = parseInt(hex.slice(5,7),16)/255;
  return 0.299*r + 0.587*g + 0.114*b;
}}

// ── Índices de filamentos y colores ─────────────────────────────────────────
const filTypes = new Map();   // type → total grams
const colMap   = new Map();   // hex → {{hex, type, grams}}

tasks.forEach(t => {{
  (t.amsDetailMapping || []).forEach(a => {{
    if (a.filamentType) {{
      filTypes.set(a.filamentType, (filTypes.get(a.filamentType)||0) + (a.weight||0));
    }}
    const hex = parseColor(a.sourceColor);
    if (hex) {{
      const prev = colMap.get(hex) || {{hex, type: a.filamentType||"", grams:0}};
      prev.grams += (a.weight||0);
      colMap.set(hex, prev);
    }}
  }});
}});

// ── Filtros ──────────────────────────────────────────────────────────────────
let activeFil = null, activeCol = null;

function buildFilters() {{
  const fg = document.querySelector('#filter-bar .filter-group:nth-child(1)');
  filTypes.forEach((_, type) => {{
    const pill = document.createElement('span');
    pill.className = 'filter-pill';
    pill.textContent = type;
    pill.onclick = () => setFilFilter(type);
    pill.id = 'fp-' + type;
    fg.appendChild(pill);
  }});
  const cg = document.querySelector('#filter-bar .filter-group:nth-child(2)');
  colMap.forEach((info, hex) => {{
    const dot = document.createElement('div');
    dot.className = 'color-dot';
    dot.style.background = hex;
    dot.title = (info.type || '') + ' · ' + fmtGrams(info.grams);
    dot.onclick = () => setColFilter(hex);
    dot.id = 'cp-' + hex.slice(1);
    cg.appendChild(dot);
  }});
}}

function setFilFilter(type) {{
  activeFil = type;
  document.querySelectorAll('#filter-bar .filter-pill').forEach(p => p.classList.remove('active'));
  document.getElementById(type ? 'fp-'+type : 'fil-all').classList.add('active');
  applyFilters();
}}
function setColFilter(hex) {{
  activeCol = hex;
  document.querySelectorAll('.color-dot').forEach(d => d.classList.remove('active'));
  if (hex) document.getElementById('cp-'+hex.slice(1)).classList.add('active');
  else document.getElementById('col-all').classList.add('active');
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
    const card = document.querySelector(`[data-idx="${{i}}"]`);
    const show = taskVisible(t);
    card.classList.toggle('hidden', !show);
    if (!show && selected.has(i)) {{ selected.delete(i); card.classList.remove('selected'); }}
    if (show) visible++;
  }});
  document.getElementById('hdr-count').textContent = visible + ' impresiones';
  updateStats();
}}

// ── Render cards ─────────────────────────────────────────────────────────────
function render() {{
  const grid = document.getElementById("grid");
  tasks.forEach((t, i) => {{
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.idx = i;
    card.onclick = () => toggle(i);

    const imgHtml = t.cover
      ? `<img class="card-img" src="${{t.cover}}" alt="" loading="lazy"
            onerror="this.parentNode.innerHTML='<div class=card-img-placeholder>🖨️</div>'">`
      : `<div class="card-img-placeholder">🖨️</div>`;

    const ams = t.amsDetailMapping || [];
    const dotsHtml = ams.length
      ? '<div class="filament-dots">' + ams.map(a => {{
          const hex = parseColor(a.sourceColor) || '#555';
          const fg  = luminance(hex) > 0.5 ? '#111' : '#eee';
          return `<span class="fdot">
            <span class="fdot-circle" style="background:${{hex}}"></span>
            <span>${{a.filamentType||'?'}} · ${{a.weight ? a.weight.toFixed(0)+'g' : '—'}}</span>
          </span>`;
        }}).join('') + '</div>'
      : '';

    const s = t.status ?? 0;
    card.innerHTML = `
      <div class="card-img-wrap">
        ${{imgHtml}}
        <div class="check-icon">✓</div>
      </div>
      <div class="card-body">
        <div class="card-title" title="${{t.title||'Sin nombre'}}">${{t.title||'Sin nombre'}}</div>
        <div class="card-meta">
          <span><span class="badge badge-${{s}}">${{STATUS[s]||s}}</span></span>
          <span>📅 ${{fmtDate(t.startTime)}}</span>
          <span>⏱ ${{fmtDuration(t.costTime)}}</span>
        </div>
        ${{dotsHtml}}
      </div>`;
    grid.appendChild(card);
  }});
}}

// ── Selección y stats ────────────────────────────────────────────────────────
const selected = new Set();

function toggle(i) {{
  if (selected.has(i)) selected.delete(i);
  else selected.add(i);
  document.querySelector(`[data-idx="${{i}}"]`).classList.toggle("selected", selected.has(i));
  updateStats();
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
  const bar = document.getElementById('stats-bar');
  const bd  = document.getElementById('breakdown');
  if (selected.size === 0) {{
    bar.classList.add('empty');
    ['s-count','s-time','s-avg','s-grams','s-ok'].forEach(id => document.getElementById(id).textContent = '—');
    bd.classList.remove('visible');
    return;
  }}
  bar.classList.remove('empty');

  const sel = [...selected].map(i => tasks[i]);
  const totalSecs  = sel.reduce((a,t) => a+(t.costTime||0), 0);
  const totalGrams = sel.reduce((a,t) => a+(t.weight||0), 0);
  const completed  = sel.filter(t => t.status===2).length;

  document.getElementById('s-count').textContent = selected.size;
  document.getElementById('s-time').textContent  = fmtDuration(totalSecs);
  document.getElementById('s-avg').textContent   = fmtDuration(Math.round(totalSecs/sel.length));
  document.getElementById('s-grams').textContent = fmtGrams(totalGrams);
  document.getElementById('s-ok').textContent    = completed+'/'+sel.length;

  // ── Breakdown ──────────────────────────────────────────────────────────
  const byType  = new Map();
  const byColor = new Map();

  sel.forEach(t => {{
    (t.amsDetailMapping||[]).forEach(a => {{
      const type = a.filamentType || 'Desconocido';
      const hex  = parseColor(a.sourceColor) || '#555';
      const w    = a.weight || 0;
      byType.set(type,  (byType.get(type)  || {{g:0,n:0}}).g  + w);
      // re-set properly
      const pt = byType.get(type) || {{g:0,n:0}};
      pt.g += w; pt.n++;
      byType.set(type, pt);
      const pc = byColor.get(hex) || {{g:0, hex, type}};
      pc.g += w;
      byColor.set(hex, pc);
    }});
    if (!(t.amsDetailMapping||[]).length) {{
      const pt = byType.get('Sin datos') || {{g:0,n:0}};
      pt.n++; byType.set('Sin datos', pt);
    }}
  }});

  // quitar la doble suma del primer set
  const byTypeFinal = new Map();
  sel.forEach(t => {{
    (t.amsDetailMapping||[]).forEach(a => {{
      const type = a.filamentType || 'Desconocido';
      const prev = byTypeFinal.get(type) || {{g:0}};
      prev.g += (a.weight||0);
      byTypeFinal.set(type, prev);
    }});
  }});
  const byColorFinal = new Map();
  sel.forEach(t => {{
    (t.amsDetailMapping||[]).forEach(a => {{
      const hex = parseColor(a.sourceColor)||'#555';
      const prev = byColorFinal.get(hex) || {{g:0, hex, type: a.filamentType||''}};
      prev.g += (a.weight||0);
      byColorFinal.set(hex, prev);
    }});
  }});

  const maxTypeG  = Math.max(...[...byTypeFinal.values()].map(v=>v.g), 1);
  const maxColorG = Math.max(...[...byColorFinal.values()].map(v=>v.g), 1);

  document.getElementById('bd-type').innerHTML = [...byTypeFinal.entries()]
    .sort((a,b)=>b[1].g-a[1].g)
    .map(([type,v]) => `
      <div class="breakdown-row">
        <span class="breakdown-name">${{type}}</span>
        <div class="breakdown-bar-wrap"><div class="breakdown-bar" style="width:${{Math.round(v.g/maxTypeG*100)}}%"></div></div>
        <span class="breakdown-val">${{fmtGrams(v.g)}}</span>
      </div>`).join('');

  document.getElementById('bd-color').innerHTML = [...byColorFinal.entries()]
    .sort((a,b)=>b[1].g-a[1].g)
    .map(([hex,v]) => `
      <div class="breakdown-row">
        <span class="breakdown-dot" style="background:${{hex}}"></span>
        <span class="breakdown-name" style="color:#999">${{v.type||'?'}}</span>
        <div class="breakdown-bar-wrap"><div class="breakdown-bar" style="width:${{Math.round(v.g/maxColorG*100)}}%; background:${{hex}}"></div></div>
        <span class="breakdown-val">${{fmtGrams(v.g)}}</span>
      </div>`).join('');

  bd.classList.add('visible');
}}

buildFilters();
render();
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
    print("\nAbrí el HTML en tu navegador:")
    print(f"  output\\historial.html")


if __name__ == "__main__":
    main()
