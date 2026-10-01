import base64
import io
import json
import os
import socket
import sqlite3
import sys
import time
from datetime import datetime
import requests
from PIL import Image

# ── ENTORNO ───────────────────────────────────────────────────────────────────
# Con Docker las variables llegan por `env_file`. Corriendo a mano no hay nada
# que las cargue, así que se lee el .env del directorio actual. Lo que ya venga
# en el entorno gana: `LIMIT=5 python bambu_history.py` sigue mandando.

def load_env(ruta: str = ".env"):
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            clave = clave.strip()
            if clave not in os.environ:
                os.environ[clave] = valor.strip().strip("'\"")


load_env()

# Consolas de Windows: sin esto, los ─ y las flechas de los mensajes revientan
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── CONFIG ────────────────────────────────────────────────────────────────────
EMAIL      = os.getenv("BAMBU_EMAIL")
PASSWORD   = os.getenv("BAMBU_PASSWORD")

if not EMAIL or not PASSWORD:
    print("Error: faltan BAMBU_EMAIL y/o BAMBU_PASSWORD.")
    print("Ponelos en el .env (copiá .env.example) o exportalos en el entorno.")
    sys.exit(1)

DEVICE_ID  = os.getenv("BAMBU_DEVICE_ID", "")
LIMIT      = int(os.getenv("LIMIT", "100"))
PAGE_SIZE  = os.getenv("PAGE_SIZE", "auto").strip().lower()  # "auto" = llenar la pantalla
SAVE_JSON  = os.getenv("SAVE_JSON", "1") == "1"
# Dentro de Docker los volúmenes se montan en /output y /data; fuera, al lado
# del script. Se puede forzar cualquiera de los dos con OUTPUT_DIR / DATA_DIR.
IS_DOCKER  = os.path.exists("/.dockerenv") or os.getenv("AM_I_IN_A_DOCKER_CONTAINER", "") != ""
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output" if IS_DOCKER else "./output")
DATA_DIR   = os.getenv("DATA_DIR",   "/data"   if IS_DOCKER else "./data")
TOKEN_FILE = f"{DATA_DIR}/.bambu_token"
LEGACY_TOKEN_FILE = f"{OUTPUT_DIR}/.bambu_token"
JSON_FILE  = f"{OUTPUT_DIR}/historial.json"
HTML_FILE  = f"{OUTPUT_DIR}/historial.html"
PORTABLE_FILE = f"{OUTPUT_DIR}/historial-portable.html"
COVERS_DIR = f"{OUTPUT_DIR}/covers"
DB_FILE    = f"{DATA_DIR}/historial.db"          # acumulado histórico (no se sirve por HTTP)
COVER_QUALITY = int(os.getenv("COVER_QUALITY", "82"))  # calidad WebP de las miniaturas
# Esfuerzo del compresor WebP (0-6). Medido sobre estas miniaturas de 512x512:
# method=6 tarda 742 ms por imagen y method=4 tarda 21 ms — 35 veces más rápido
# para ahorrar 0,1 KB (1,4 %). El 6 no vale lo que cuesta.
COVER_METHOD  = int(os.getenv("COVER_METHOD", "4"))
# Las miniaturas se bajan de S3 una por una: es latencia pura, no CPU. En paralelo
# el tiempo de una corrida con muchas nuevas cae de minutos a segundos.
COVER_WORKERS = int(os.getenv("COVER_WORKERS", "8"))
PORTABLE      = os.getenv("PORTABLE", "1") == "1"  # archivo único que abre sin servidor
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "0"))
# ─────────────────────────────────────────────────────────────────────────────

if PAGE_SIZE != "auto" and not PAGE_SIZE.isdigit():
    print(f"PAGE_SIZE inválido ({PAGE_SIZE!r}): se usa 'auto'")
    PAGE_SIZE = "auto"

BASE_URL = "https://api.bambulab.com"
STATUS = {0: "Desconocido", 1: "En progreso", 2: "Completado", 3: "Fallido", 4: "Cancelado"}


# ── TOKEN ─────────────────────────────────────────────────────────────────────

def save_token(token: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump({"token": token, "saved_at": datetime.now().isoformat()}, f)
    print("  Token guardado en disco.")

def load_token() -> str | None:
    # Migración one-shot: si hay token viejo en /output, moverlo a /data
    if not os.path.exists(TOKEN_FILE) and os.path.exists(LEGACY_TOKEN_FILE):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.replace(LEGACY_TOKEN_FILE, TOKEN_FILE)
            print(f"  Token migrado: {LEGACY_TOKEN_FILE} → {TOKEN_FILE}")
        except OSError:
            # Si /data no está montado (p.ej. instalación vieja), seguir con el legacy
            return json.load(open(LEGACY_TOKEN_FILE)).get("token")
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
    if r.status_code != 200:
        # Recortado a propósito: el cuerpo de una respuesta de login puede traer
        # datos que no conviene dejar en los logs.
        print(f"  El servidor respondió {r.status_code}: {r.text[:200]}")
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
        if r3.status_code != 200:
            print(f"  El servidor respondió {r3.status_code}: {r3.text[:200]}")
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


# ── BASE DE DATOS ─────────────────────────────────────────────────────────────
# Bambu Cloud solo expone los últimos 90 días: lo que sale de esa ventana se
# pierde. La base acumula todo lo que se vio alguna vez y el visor se genera
# desde ella, no desde lo que devolvió el último fetch.

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    start_time TEXT,
    status     INTEGER,
    title      TEXT,
    cost_time  INTEGER,
    weight     REAL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_start ON tasks(start_time DESC);
"""


def db_connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(SCHEMA)
    return conn


def db_upsert(tasks: list) -> tuple:
    """Guarda los trabajos conservando `first_seen`. Devuelve (nuevos, ya conocidos)."""
    ahora = datetime.now().isoformat(timespec="seconds")
    conn = db_connect()
    try:
        previos = {r[0] for r in conn.execute("SELECT id FROM tasks")}
        nuevos = 0

        for t in tasks:
            tid = t.get("id")
            if tid is None:
                continue
            if tid not in previos:
                nuevos += 1
            conn.execute(
                """
                INSERT INTO tasks (id, start_time, status, title, cost_time, weight,
                                   first_seen, last_seen, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    start_time = excluded.start_time,
                    status     = excluded.status,
                    title      = excluded.title,
                    cost_time  = excluded.cost_time,
                    weight     = excluded.weight,
                    last_seen  = excluded.last_seen,
                    data       = excluded.data
                """,
                (tid, t.get("startTime"), t.get("status"), t.get("title"),
                 t.get("costTime"), t.get("weight"), ahora, ahora,
                 json.dumps(t, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()

    return nuevos, len(tasks) - nuevos


def db_all() -> list:
    """Todo el historial acumulado, del más reciente al más viejo."""
    conn = db_connect()
    try:
        filas = conn.execute("SELECT data FROM tasks ORDER BY start_time DESC").fetchall()
    finally:
        conn.close()
    return [json.loads(f[0]) for f in filas]


def db_seed_from_json():
    """
    Primera corrida con base vacía: importa el historial.json que ya existía, para
    no arrancar perdiendo lo que se había bajado antes de tener base.
    """
    conn = db_connect()
    try:
        (n,) = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
    finally:
        conn.close()

    if n or not os.path.exists(JSON_FILE):
        return

    try:
        with open(JSON_FILE, encoding="utf-8") as f:
            previos = json.load(f)
    except (OSError, ValueError) as e:
        print(f"No se pudo leer el historial.json previo: {e}")
        return

    if previos:
        db_upsert(previos)
        print(f"Base sembrada con {len(previos)} trabajos del historial.json previo")


# ── COVERS ────────────────────────────────────────────────────────────────────

def to_webp(raw: bytes, dest: str):
    """
    Escribe `raw` (PNG/JPEG) como WebP en `dest`. Pasa por un .tmp y verifica que
    el resultado se pueda abrir antes de dejarlo en su sitio, así un fallo a mitad
    de camino no deja una miniatura corrupta que después se toma por buena.
    """
    im = Image.open(io.BytesIO(raw))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")

    tmp = f"{dest}.tmp"
    im.save(tmp, "WEBP", quality=COVER_QUALITY, method=COVER_METHOD)
    with Image.open(tmp) as check:
        check.verify()
    os.replace(tmp, dest)


def cache_covers(tasks: list):
    """
    Descarga las miniaturas a disco y las guarda en WebP (~la mitad que el PNG
    original, sin diferencia visible). Las URLs de `cover` son enlaces prefirmados
    de S3 con X-Amz-Expires=1800 (30 min): si el HTML las incrusta directamente,
    dejan de cargar al caducar la firma. Guardándolas localmente y sirviéndolas
    por ruta relativa, las imágenes ya no dependen de la firma temporal.

    Un .png de una versión anterior se convierte en vez de volver a descargarlo:
    para los trabajos que ya salieron de la ventana de 90 días de la nube, ese
    archivo local es la única copia que queda.

    Muta cada task en sitio: si hay miniatura, `cover` pasa a ser
    "covers/<id>.webp" (ruta relativa servida desde /output). Si falla, deja la
    URL remota como fallback.
    """
    from concurrent.futures import ThreadPoolExecutor

    os.makedirs(COVERS_DIR, exist_ok=True)
    converted = cached = 0
    pendientes = []   # (task, id, ruta relativa, destino, url) — se bajan en paralelo

    # Primera pasada: lo que ya está en disco se resuelve sin tocar la red.
    for t in tasks:
        tid = t.get("id")
        if tid is None:
            continue

        rel    = f"covers/{tid}.webp"
        fpath  = f"{COVERS_DIR}/{tid}.webp"
        legacy = f"{COVERS_DIR}/{tid}.png"

        if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            t["cover"] = rel
            cached += 1
            continue

        if os.path.exists(legacy) and os.path.getsize(legacy) > 0:
            try:
                with open(legacy, "rb") as f:
                    to_webp(f.read(), fpath)
                os.remove(legacy)
                t["cover"] = rel
                converted += 1
                continue
            except (OSError, ValueError) as e:
                print(f"  [cover] no se pudo convertir {tid}: {e}")

        url = t.get("cover")
        if url and url.startswith("http"):
            pendientes.append((t, tid, rel, fpath, url))

    # Segunda pasada: descarga en paralelo. Cada hilo escribe su propio archivo y
    # muta su propia task, así que no hace falta candado.
    downloaded = failed = 0

    def bajar(item):
        t, tid, rel, fpath, url = item
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            to_webp(r.content, fpath)
            t["cover"] = rel
            return True
        except (requests.RequestException, OSError, ValueError) as e:
            print(f"  [cover] fallo {tid}: {e}")
            return False

    if pendientes:
        workers = max(1, min(COVER_WORKERS, len(pendientes)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ok in pool.map(bajar, pendientes):
                if ok: downloaded += 1
                else:  failed += 1

    print(f"Miniaturas → {downloaded} nuevas, {converted} convertidas a WebP, "
          f"{cached} en caché, {failed} fallidas")


# ── HTML ──────────────────────────────────────────────────────────────────────
# La plantilla es un string PLANO, no un f-string: el HTML lleva CSS y JS llenos
# de llaves y duplicarlas (`{{`) para el f-string era una fuente de errores
# silenciosos. Los huecos se rellenan con .replace() en generate_html().

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
__REFRESH_TAG__
<title>Bambu Print History</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --bg:#0B0D0B; --panel:#131613; --panel-2:#191D19; --side:#0E100E;
  --line:#242924; --line-soft:#1A1E1A;
  --tx:#E9EDE9; --tx-2:#C7CEC7; --tx-3:#8C948C; --tx-4:#5A625A; --tx-5:#3E463E;
  --ac:#00B84C; --ac-ink:#04140A; --ac-bg:#10231A; --ac-line:#1F3D28; --ac-tx:#6E8A76;
  --bad:#E5484D;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--tx);
  font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased
}
a{color:var(--ac);text-decoration:none}
a:hover{color:#7FD43B}
.num{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;font-variant-numeric:tabular-nums}
.rotulo{font-size:10px;font-weight:500;letter-spacing:.8px;text-transform:uppercase;color:var(--tx-4)}

/* ── Header ── */
header{
  position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:13px;
  height:56px;padding:0 20px;background:var(--panel);border-bottom:1px solid var(--line)
}
header h1{font-size:15px;font-weight:600;letter-spacing:-.2px;white-space:nowrap}
.spacer{flex-grow:1}
.btn{
  display:flex;align-items:center;gap:7px;height:32px;padding:0 13px;border-radius:6px;
  border:1px solid var(--line);background:var(--panel-2);color:var(--tx-3);
  font-family:inherit;font-size:13px;font-weight:500;cursor:pointer;transition:background .15s
}
.btn:hover{background:#222722}
.btn.active{background:var(--ac-bg);border-color:var(--ac-line);color:var(--ac)}
.btn-ac{background:var(--ac);border:none;color:var(--ac-ink);font-weight:600}
.btn-ac:hover{background:#00CE55}
.btn-ac:disabled{opacity:.35;cursor:default}
.btn-ac:disabled:hover{background:var(--ac)}

/* ── Layout ── */
#app{display:flex;align-items:flex-start}
#side{
  width:300px;flex-shrink:0;padding:18px;background:var(--side);
  border-right:1px solid var(--line);
  position:sticky;top:56px;max-height:calc(100vh - 56px);overflow-y:auto;
  display:flex;flex-direction:column;gap:16px
}
#main{flex-grow:1;min-width:0;padding:18px 20px;display:flex;flex-direction:column;gap:14px}

/* ── Panel de selección ── */
#sel-panel{
  display:flex;flex-direction:column;gap:12px;padding:16px;
  background:var(--ac-bg);border:1px solid var(--ac-line);border-radius:10px
}
.sel-head{display:flex;align-items:center;justify-content:space-between}
.sel-row{display:flex;align-items:center;gap:10px}
.sel-row .col{display:flex;flex-direction:column;gap:1px;flex-grow:1;min-width:0}
.sel-val{font-size:25px;font-weight:600;line-height:1.15;color:var(--ac)}
.sel-div{height:1px;background:#1A3122}
.sel-mats{display:flex;flex-direction:column;gap:7px}
.mat-row{display:flex;align-items:center;gap:8px;font-size:12px;color:#A9B4A9}
.mat-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.mat-name{flex-grow:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mat-val{color:var(--tx)}
.btn-clear{
  height:30px;border-radius:6px;border:1px solid var(--ac-line);background:transparent;
  color:var(--tx-3);font-family:inherit;font-size:12px;cursor:pointer
}
.btn-clear:hover{background:#0D1C14}
.sel-note{font-size:11px;color:var(--ac-tx)}

/* ── Filtros ── */
.fil-group{display:flex;flex-direction:column;gap:10px}
.pills{display:flex;gap:6px;flex-wrap:wrap}
.pill{
  padding:6px 13px;border-radius:20px;font-size:12px;cursor:pointer;
  background:var(--panel-2);border:1px solid var(--line);color:var(--tx-3);transition:all .15s
}
.pill:hover{background:#222722}
.pill.active{background:var(--ac);border-color:var(--ac);color:var(--ac-ink);font-weight:500}
.dots{display:flex;gap:8px;flex-wrap:wrap}
.dot{width:24px;height:24px;border-radius:50%;cursor:pointer;border:1px solid #343A34}
.dot.active{outline:2px solid var(--ac);outline-offset:2px}
.divider{height:1px;background:var(--line-soft)}
.hint{font-size:11px;color:#4E564E;text-wrap:pretty}

/* ── Cuerpo ── */
.main-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--tx-4)}
#hdr-count{font-size:13px;color:var(--tx-3)}
/* overflow-anchor:none → sin esto, al repaginar Chromium "compensa" el cambio
   de contenido moviendo el scroll (scroll anchoring), y el paginador se corre
   de abajo del dedo entre toque y toque. */
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;overflow-anchor:none}
.card{
  display:flex;flex-direction:column;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;overflow:hidden;
  cursor:pointer;user-select:none;transition:border-color .15s,transform .1s
}
.card:hover{transform:translateY(-2px);border-color:#333933}
.card.selected{background:var(--ac-bg);border:2px solid var(--ac)}
.card.failed{border-color:#2E2122}
.card-img-wrap{position:relative;display:block}
.card-img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:#0F110F}
.card-ph{
  width:100%;aspect-ratio:4/3;background:#0F110F;display:flex;
  align-items:center;justify-content:center;color:#222;font-size:28px
}
.badge{
  position:absolute;top:9px;left:9px;padding:3px 8px;border-radius:5px;
  font-size:10px;font-weight:600;letter-spacing:.3px
}
.badge-3{background:rgba(43,14,16,.9);color:var(--bad)}
.badge-4{background:rgba(11,13,11,.82);color:var(--tx-3)}
.check{
  position:absolute;top:8px;right:8px;width:21px;height:21px;border-radius:50%;
  background:var(--ac);display:none;align-items:center;justify-content:center
}
.card.selected .check{display:flex}
.card-body{display:flex;flex-direction:column;gap:7px;padding:11px}
.card-title{
  font-size:13px;font-weight:500;color:var(--tx-2);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis
}
.card.selected .card-title{color:var(--tx)}
.card-nums{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--tx-2)}
.card.failed .card-nums,.card.cancelled .card-nums{color:var(--tx-3)}
.card-sep{color:var(--tx-5)}
.card-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.card-sub{font-size:10px;color:var(--tx-4)}
/* Modo "llenar": el grid toma el alto disponible y las filas se lo reparten,
   así no queda hueco abajo. La portada se estira y recorta con object-fit. */
#grid.fill .card-img-wrap{flex:1 1 auto;min-height:0}
#grid.fill .card-img,#grid.fill .card-ph{height:100%;aspect-ratio:auto}

/* ── Paginador ── */
#pager{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.pg-info{font-size:12px;color:var(--tx-4)}
.pg-btns{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
/* Todos los slots miden igual (--pg-w, según los dígitos del total) y siempre
   hay la misma cantidad: así las flechas NO se mueven al cambiar de página. */
.pg-btn{
  min-width:var(--pg-w,32px);height:32px;padding:0 4px;border-radius:6px;border:1px solid var(--line);
  background:var(--panel-2);color:var(--tx-2);cursor:pointer;font-size:13px;font-family:inherit;
  display:inline-flex;align-items:center;justify-content:center
}
.pg-btn:hover:not(:disabled){background:#222722}
.pg-btn.active{background:var(--ac);border-color:var(--ac);color:var(--ac-ink);font-weight:600}
.pg-btn:disabled{opacity:.35;cursor:default}
.pg-gap{
  min-width:var(--pg-w,32px);height:32px;color:var(--tx-5);font-size:13px;
  display:inline-flex;align-items:center;justify-content:center
}

/* ── Vista de estadísticas ── */
#stats-view{display:none;padding:20px;flex-direction:column;gap:16px}
#stats-view.open{display:flex}
#app.hidden{display:none}
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
.ov-card{
  display:flex;flex-direction:column;gap:6px;padding:16px 18px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px
}
.ov-label{font-size:10px;font-weight:500;letter-spacing:.8px;text-transform:uppercase;color:var(--tx-4)}
.ov-value{font-size:26px;font-weight:600;line-height:1.1;font-family:"IBM Plex Mono",ui-monospace,monospace}
.ov-sub{font-size:12px;color:var(--tx-3)}
.charts-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.chart-box{
  display:flex;flex-direction:column;gap:12px;padding:18px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px
}
.chart-box h3{font-size:10px;font-weight:500;letter-spacing:.8px;text-transform:uppercase;color:var(--tx-4)}
.chart-row{display:flex;align-items:center;gap:10px}
.chart-label{
  width:112px;flex-shrink:0;font-size:12px;color:var(--tx-2);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis
}
.chart-bar-wrap{flex-grow:1;height:7px;border-radius:4px;background:#1E231E;overflow:hidden}
.chart-bar{height:7px;border-radius:4px;background:var(--ac)}
.chart-val{width:66px;text-align:right;font-size:12px;color:var(--tx-3);white-space:nowrap}
.color-label-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}

/* ── Aviso de copiado ── */
#toast{
  position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(14px);
  padding:9px 16px;border-radius:8px;background:var(--ac);color:var(--ac-ink);
  font-size:13px;font-weight:600;opacity:0;pointer-events:none;transition:opacity .18s,transform .18s;z-index:99
}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

@media(max-width:820px){
  #app{flex-direction:column}
  #side{
    width:100%;position:static;max-height:none;
    border-right:none;border-bottom:1px solid var(--line)
  }
  #main{padding:14px 16px;width:100%}
  #grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .charts-row{grid-template-columns:1fr}
  .sel-val{font-size:28px}
  .btn-ac{height:40px}
  #pager{--pg-w:42px}
  .pg-btn,.pg-gap{height:42px}
  .dot{width:28px;height:28px}
}
</style>
</head>
<body>

<header>
  <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="#00B84C" stroke-width="1.6"
       stroke-linecap="round" stroke-linejoin="round">
    <path d="M6 9V3h12v6"></path>
    <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
    <rect x="6" y="14" width="12" height="7" rx="1"></rect>
  </svg>
  <h1>Historial de impresión</h1>
  <div class="spacer"></div>
  <button class="btn" id="btn-stats" onclick="toggleStats()">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2"></path>
    </svg>
    <span id="btn-stats-tx">Estadísticas</span>
  </button>
</header>

<div id="app">

  <aside id="side">

    <div id="sel-panel">
      <div class="sel-head">
        <span class="rotulo" style="color:var(--ac-tx)">Selección</span>
        <span class="num sel-note" id="s-count">0 de __N__</span>
      </div>

      <div class="sel-row">
        <div class="col">
          <span class="rotulo" style="color:var(--ac-tx)">Horas</span>
          <span class="num sel-val" id="s-hours">0.00</span>
        </div>
        <button class="btn btn-ac" id="btn-copy-h" onclick="copiar('s-hours')" disabled>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="12" height="12" rx="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
          </svg>
          Copiar
        </button>
      </div>

      <div class="sel-div"></div>

      <div class="sel-row">
        <div class="col">
          <span class="rotulo" style="color:var(--ac-tx)" id="s-grams-label">Gramos</span>
          <span class="num sel-val" id="s-grams">0.0</span>
        </div>
        <button class="btn btn-ac" id="btn-copy-g" onclick="copiar('s-grams')" disabled>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="12" height="12" rx="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
          </svg>
          Copiar
        </button>
      </div>

      <div class="sel-div"></div>

      <div class="sel-mats" id="sel-mats">
        <span class="sel-note">Tocá las impresiones para sumarlas.</span>
      </div>

      <button class="btn-clear" onclick="clearAll()">Limpiar selección</button>
    </div>

    <div class="fil-group">
      <span class="rotulo">Material</span>
      <div class="pills" id="fil-pills">
        <span id="fp-all" class="pill active" onclick="setFilFilter(null)">Todos</span>
      </div>
    </div>

    <div class="fil-group">
      <span class="rotulo">Color</span>
      <div class="pills"><span id="cp-all" class="pill active" onclick="setColFilter(null)">Todos</span></div>
      <div class="dots" id="col-dots"></div>
    </div>

    <div class="divider"></div>

    <div class="fil-group">
      <button class="btn" style="justify-content:center" onclick="selectVisible()">Seleccionar visibles</button>
      <span class="hint" id="sel-vis-hint">Toma todo lo filtrado, no solo esta página.</span>
    </div>

  </aside>

  <main id="main">
    <div class="main-head">
      <span class="num" id="hdr-count">__N__ impresiones</span>
      <span class="card-sep">·</span>
      <span class="num" id="hdr-range">—</span>
    </div>
    <div id="grid"></div>
    <div id="pager"></div>
  </main>

</div>

<div id="stats-view">
  <div class="ov-grid" id="overview-grid"></div>
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

<div id="toast">Copiado</div>

<script>
const tasks = __TASKS_JSON__;
const STATUS = {0:"Desconocido",1:"En progreso",2:"Completado",3:"Fallido",4:"Cancelado"};

// ── Tamaño de página adaptativo ──────────────────────────────────────────────
// En una pantalla ancha entran más columnas y más filas; con un número fijo
// sobraba espacio abajo. Se mide el grid real y se llena lo que se ve.
// PAGE_SIZE con un número lo fija; "auto" (default) lo calcula.
const PAGE_SIZE_CFG = "__PAGE_SIZE__";
let pageSize = 12;

function medirPageSize() {
  const grid   = document.getElementById('grid');
  const cs     = getComputedStyle(grid);
  const pistas = cs.gridTemplateColumns.split(' ').filter(Boolean);
  const cols   = pistas.length || 1;
  const gap    = parseFloat(cs.rowGap) || 14;

  const suelto = () => {   // sin alto forzado: la card usa su proporción natural
    grid.classList.remove('fill');
    grid.style.height = '';
    grid.style.gridTemplateRows = '';
  };

  // Número fijo: el usuario mandó, no se toca nada
  if (PAGE_SIZE_CFG !== 'auto') {
    suelto();
    return Math.max(1, parseInt(PAGE_SIZE_CFG, 10) || 12);
  }
  // En el teléfono la página se scrollea igual: no tiene sentido forzar el alto
  if (window.innerWidth <= 820) {
    suelto();
    return cols * 4;
  }

  // Alto "natural" de una card: portada 4:3 sobre el ancho de columna + cuerpo.
  // El cuerpo se mide de una card real cuando ya hay alguna.
  const colW   = parseFloat(pistas[0]) || 230;
  const cuerpo = grid.querySelector('.card-body');
  const bodyH  = cuerpo ? cuerpo.getBoundingClientRect().height : 92;
  const natural = colW * 0.75 + bodyH;

  // Espacio libre hasta abajo de la ventana, dejando lugar al paginador
  const top   = grid.getBoundingClientRect().top;
  const libre = Math.max(natural, window.innerHeight - Math.max(top, 0) - 64);

  // Se redondea: las filas se estiran o se achican un poco, pero llenan justo.
  const filas = Math.max(1, Math.round((libre + gap) / (natural + gap)));

  grid.classList.add('fill');
  grid.style.height = libre + 'px';
  grid.style.gridTemplateRows = 'repeat(' + filas + ', 1fr)';

  return Math.min(60, cols * filas);
}

// Devuelve true si cambió. Conserva el lugar: la primera card de la página
// actual sigue siendo la primera después de recalcular.
function ajustarPagina(rerender) {
  const nuevo = medirPageSize();
  if (nuevo === pageSize) return false;
  const primero = (page - 1) * pageSize;
  pageSize = nuevo;
  page = Math.floor(primero / pageSize) + 1;
  if (rerender) goToPage(page, false);
  return true;
}

// ── Utils ────────────────────────────────────────────────────────────────────
function parseColor(c) {
  if (!c) return null;
  return '#' + c.toString().replace('#','').slice(0,6).toUpperCase();
}
// Horas y gramos van en decimal con PUNTO: es lo que se copia y se pega en el
// sistema de ventas, así que el número tiene que salir pelado y listo.
function fmtHours(secs) { return ((secs || 0) / 3600).toFixed(2); }
function fmtG(g)        { return (g || 0).toFixed(1); }
function fmtGrams(g) {
  if (!g) return "0 g";
  return g >= 1000 ? (g/1000).toFixed(2).replace('.', ',') + " kg" : g.toFixed(0) + " g";
}
function fmtDuration(s) {
  if (!s) return "—";
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return h + " h " + String(m).padStart(2,"0");
}
function fmtDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("es-AR", {day:"numeric", month:"short"});
}
function esc(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

// ── Copiado ──────────────────────────────────────────────────────────────────
// La página se sirve por HTTP plano (LAN / Tailscale), y ahí navigator.clipboard
// no existe: solo está en contextos seguros. Por eso el fallback con textarea.
function copiar(id) {
  const txt = document.getElementById(id).textContent.trim();
  const ok = () => toast('Copiado: ' + txt);

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(txt).then(ok).catch(() => copiarFallback(txt, ok));
  } else {
    copiarFallback(txt, ok);
  }
}
function copiarFallback(txt, ok) {
  const ta = document.createElement('textarea');
  ta.value = txt;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed'; ta.style.top = '-1000px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); ok(); }
  catch (e) { toast('No se pudo copiar: ' + txt); }
  document.body.removeChild(ta);
}
let toastT = null;
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => el.classList.remove('show'), 1600);
}

// ── Gramos respetando el filtro activo ───────────────────────────────────────
function getFilteredGrams(task) {
  if (!activeFil && !activeCol) return task.weight || 0;
  return (task.amsDetailMapping || [])
    .filter(a => {
      const typeOk = !activeFil || a.filamentType === activeFil;
      const colOk  = !activeCol || parseColor(a.sourceColor) === activeCol;
      return typeOk && colOk;
    })
    .reduce((sum, a) => sum + (a.weight || 0), 0);
}

// ── Estadísticas globales (vista aparte) ─────────────────────────────────────
function buildGlobalStats() {
  const byType  = new Map();
  const byColor = new Map();
  let totalSecs = 0, totalGrams = 0, completed = 0, failed = 0;

  tasks.forEach(t => {
    totalSecs  += t.costTime || 0;
    totalGrams += t.weight   || 0;
    if (t.status === 2) completed++;
    if (t.status === 3) failed++;
    (t.amsDetailMapping || []).forEach(a => {
      const type = a.filamentType || 'Desconocido';
      const hex  = parseColor(a.sourceColor) || '#555555';
      const w    = a.weight || 0;
      const pt   = byType.get(type) || {g:0, count:0};
      pt.g += w; pt.count++; byType.set(type, pt);
      const ckey = type + '|' + hex;
      const pc   = byColor.get(ckey) || {g:0, count:0, hex, type};
      pc.g += w; pc.count++; byColor.set(ckey, pc);
    });
  });

  const successPct = tasks.length ? Math.round(completed/tasks.length*100) : 0;
  const topType    = [...byType.entries()].sort((a,b)=>b[1].g-a[1].g)[0];

  const cards = [
    {label:"Trabajos",      value: tasks.length,            sub:"en la base"},
    {label:"Tiempo total",  value: fmtDuration(totalSecs),  sub:"de impresión"},
    {label:"Filamento",     value: fmtGrams(totalGrams),    sub:"en total"},
    {label:"Completadas",   value: completed,               sub: successPct+"% del total", ac:true},
    {label:"Fallidas",      value: failed,                  sub: Math.round(failed/(tasks.length||1)*100)+"% del total", bad:true},
    {label:"Tipo más usado",value: topType ? topType[0] : "—", sub: topType ? fmtGrams(topType[1].g) : ""},
  ];

  document.getElementById('overview-grid').innerHTML = cards.map(c => `
    <div class="ov-card">
      <div class="ov-label">${c.label}</div>
      <div class="ov-value" style="${c.ac ? 'color:var(--ac)' : c.bad ? 'color:var(--bad)' : ''}">${c.value}</div>
      ${c.sub ? `<div class="ov-sub">${c.sub}</div>` : ''}
    </div>`).join('');

  const sortedTypes = [...byType.entries()].sort((a,b)=>b[1].g-a[1].g);
  const maxTypeG    = sortedTypes[0] ? sortedTypes[0][1].g : 1;
  document.getElementById('chart-type').innerHTML = sortedTypes.map(([type, v]) => `
    <div class="chart-row">
      <span class="chart-label" title="${esc(type)}">${esc(type)}</span>
      <div class="chart-bar-wrap"><div class="chart-bar" style="width:${Math.round(v.g/maxTypeG*100)}%"></div></div>
      <span class="chart-val num">${fmtGrams(v.g)}</span>
    </div>`).join('');

  const sortedColors = [...byColor.entries()].sort((a,b)=>b[1].g-a[1].g).slice(0,15);
  const maxColorG    = sortedColors[0] ? sortedColors[0][1].g : 1;
  document.getElementById('chart-color').innerHTML = sortedColors.map(([, v]) => `
    <div class="chart-row">
      <span class="color-label-dot" style="background:${v.hex};border:1px solid #333"></span>
      <span class="chart-label" title="${esc(v.type)} ${v.hex}">${esc(v.type)}</span>
      <div class="chart-bar-wrap"><div class="chart-bar" style="width:${Math.round(v.g/maxColorG*100)}%;background:${v.hex}"></div></div>
      <span class="chart-val num">${fmtGrams(v.g)}</span>
    </div>`).join('');
}

function toggleStats() {
  const stats = document.getElementById('stats-view');
  const app   = document.getElementById('app');
  const btn   = document.getElementById('btn-stats');
  const open  = stats.classList.toggle('open');
  app.classList.toggle('hidden', open);
  btn.classList.toggle('active', open);
  document.getElementById('btn-stats-tx').textContent = open ? 'Volver al historial' : 'Estadísticas';
  window.scrollTo(0, 0);
  if (!open) ajustarPagina(true);   // al volver, el grid se vuelve a medir
}

// ── Filtros ──────────────────────────────────────────────────────────────────
let activeFil = null, activeCol = null;

function buildFilters() {
  const byType  = new Map();
  const byColor = new Map();
  tasks.forEach(t => {
    (t.amsDetailMapping || []).forEach(a => {
      if (a.filamentType) byType.set(a.filamentType, true);
      const hex = parseColor(a.sourceColor);
      if (hex) byColor.set(hex, a.filamentType || '');
    });
  });

  const pills = document.getElementById('fil-pills');
  byType.forEach((_, type) => {
    const p = document.createElement('span');
    p.className = 'pill'; p.textContent = type;
    p.id = 'fp-'+type; p.onclick = () => setFilFilter(type);
    pills.appendChild(p);
  });

  const dots = document.getElementById('col-dots');
  byColor.forEach((type, hex) => {
    const d = document.createElement('div');
    d.className = 'dot'; d.style.background = hex;
    d.title = type + ' ' + hex; d.id = 'cp-'+hex.slice(1);
    d.onclick = () => setColFilter(hex);
    dots.appendChild(d);
  });
}

function setFilFilter(type) {
  activeFil = type;
  document.querySelectorAll('#fil-pills .pill').forEach(p => p.classList.remove('active'));
  document.getElementById(type ? 'fp-'+type : 'fp-all').classList.add('active');
  applyFilters();
}
function setColFilter(hex) {
  activeCol = hex;
  document.querySelectorAll('.dot').forEach(d => d.classList.remove('active'));
  document.getElementById('cp-all').classList.toggle('active', !hex);
  if (hex) document.getElementById('cp-'+hex.slice(1)).classList.add('active');
  applyFilters();
}

function taskVisible(t) {
  const ams = t.amsDetailMapping || [];
  if (activeFil && !ams.some(a => a.filamentType === activeFil)) return false;
  if (activeCol && !ams.some(a => parseColor(a.sourceColor) === activeCol)) return false;
  return true;
}

function applyFilters() {
  visibleIdx = [];
  tasks.forEach((t, i) => {
    if (taskVisible(t)) visibleIdx.push(i);
    else                selected.delete(i);   // lo filtrado no queda seleccionado
  });
  document.getElementById('hdr-count').textContent = visibleIdx.length + ' impresiones';

  const label = document.getElementById('s-grams-label');
  const parts = [activeFil, activeCol].filter(Boolean);
  label.textContent = parts.length ? 'Gramos (solo ' + parts.join(' + ') + ')' : 'Gramos';

  goToPage(1, false);   // al cambiar el filtro se vuelve a la página 1
  updateStats();
}

// ── Selección ────────────────────────────────────────────────────────────────
const selected = new Set();

function selectVisible() {
  visibleIdx.forEach(i => selected.add(i));
  document.querySelectorAll('.card').forEach(c => c.classList.add('selected'));
  updateStats();
}
function clearAll() {
  selected.clear();
  document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
  updateStats();
}

// ── Cards ────────────────────────────────────────────────────────────────────
function buildCard(t, i) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.idx = i;
  if (selected.has(i)) card.classList.add('selected');
  if (t.status === 3) card.classList.add('failed');
  if (t.status === 4) card.classList.add('cancelled');

  card.addEventListener('click', function () {
    if (selected.has(i)) { selected.delete(i); card.classList.remove('selected'); }
    else                 { selected.add(i);    card.classList.add('selected'); }
    updateStats();
  });

  const img = t.cover
    ? `<img class="card-img" src="${esc(t.cover)}" alt="" loading="lazy"
           onerror="this.parentNode.innerHTML='<div class=card-ph>·</div>'">`
    : `<div class="card-ph">·</div>`;

  const badge = t.status === 3 ? '<span class="badge badge-3">FALLIDA</span>'
              : t.status === 4 ? '<span class="badge badge-4">CANCELADA</span>'
              : '';

  const ams  = t.amsDetailMapping || [];
  const hex  = ams.length ? (parseColor(ams[0].sourceColor) || '#555') : '#555';
  const mats = [...new Set(ams.map(a => a.filamentType).filter(Boolean))].join(' + ');

  card.innerHTML = `
    <div class="card-img-wrap">
      ${img}${badge}
      <div class="check">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#04140A" stroke-width="3.4"
             stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg>
      </div>
    </div>
    <div class="card-body">
      <div class="card-title" title="${esc(t.title)}">${esc(t.title) || 'Sin nombre'}</div>
      <div class="card-nums num">
        <span>${fmtHours(t.costTime)} h</span>
        <span class="card-sep">·</span>
        <span>${Math.round(t.weight || 0)} g</span>
        <span style="flex-grow:1"></span>
        <span class="card-dot" style="background:${hex}"></span>
      </div>
      <div class="card-sub num">${fmtDay(t.startTime)}${mats ? ' · ' + esc(mats) : ''}</div>
    </div>`;
  return card;
}

// ── Paginación ───────────────────────────────────────────────────────────────
let visibleIdx = tasks.map((_, i) => i);
let page = 1;

function totalPages() { return Math.max(1, Math.ceil(visibleIdx.length / pageSize)); }

function goToPage(p, scroll) {
  page = Math.min(Math.max(1, p), totalPages());
  const y = window.scrollY;
  location.hash = 'p=' + page;   // sobrevive al <meta refresh> del modo live
  renderPage();

  if (scroll) {
    document.getElementById('grid').scrollIntoView({behavior:'smooth', block:'start'});
  } else {
    // Ni el cambio de hash ni el re-render deberían mover al usuario de lugar.
    // Se repite en el frame siguiente porque el ajuste del navegador ocurre
    // después de que termina este handler.
    if (window.scrollY !== y) window.scrollTo(0, y);
    requestAnimationFrame(() => { if (window.scrollY !== y) window.scrollTo(0, y); });
  }
}
function pageFromHash() {
  const m = /p=([0-9]+)/.exec(location.hash || '');
  return m ? parseInt(m[1], 10) : 1;
}

function renderPage() {
  const grid  = document.getElementById('grid');
  const start = (page - 1) * pageSize;
  const slice = visibleIdx.slice(start, start + pageSize);

  // Se arma aparte y se cambia de una sola vez. Si el grid queda vacío aunque
  // sea un instante, el documento se achica, el navegador recorta el scroll al
  // nuevo máximo y el paginador se corre de abajo del dedo.
  const frag = document.createDocumentFragment();
  slice.forEach(i => frag.appendChild(buildCard(tasks[i], i)));
  grid.replaceChildren(frag);

  renderPager(start, slice.length);
}

function renderPager(start, shown) {
  const pager = document.getElementById('pager');
  const total = visibleIdx.length;
  const pages = totalPages();

  if (!total) {
    pager.innerHTML = '<span class="pg-info">Ninguna impresión coincide con el filtro</span>';
    return;
  }
  const info = `<span class="pg-info num">Mostrando ${start + 1}–${start + shown} de ${total}</span>`;
  if (pages === 1) { pager.innerHTML = info; return; }

  let btns = '';
  const add = (label, target, cls) => {
    const dis = target ? '' : 'disabled';
    // false = no scrollear: si la página se mueve, el botón se corre de abajo
    // del dedo y hay que reapuntar en cada paso.
    btns += `<button class="pg-btn ${cls || ''}" ${dis} onclick="goToPage(${target || 1}, false)">${label}</button>`;
  };

  add('‹', page > 1 ? page - 1 : 0);
  pageNumbers(pages).forEach(p => {
    if (p === '…') btns += '<span class="pg-gap">…</span>';
    else           add(p, p, p === page ? 'active' : '');
  });
  add('›', page < pages ? page + 1 : 0);

  // Ancho de slot fijo, calculado con los dígitos del número más largo
  pager.style.setProperty('--pg-w', (22 + 8 * String(pages).length) + 'px');
  pager.innerHTML = info + '<div class="pg-btns">' + btns + '</div>';
}

// Devuelve SIEMPRE la misma cantidad de slots (7 cuando hay más de 7 páginas),
// rellenando con puntos suspensivos. Si la cantidad variara, el bloque cambiaría
// de ancho y las flechas se moverían de lugar a cada paso.
function pageNumbers(pages) {
  // En el teléfono entran menos slots: con 7 la fila se parte en dos y las
  // flechas terminan en renglones distintos.
  const ranuras = window.innerWidth <= 820 ? 5 : 7;

  if (pages <= ranuras) {
    return Array.from({length: pages}, (_, i) => i + 1);
  }

  if (ranuras === 5) {
    if (page <= 3)          return [1, 2, 3, '…', pages];
    if (page >= pages - 2)  return [1, '…', pages - 2, pages - 1, pages];
    return [1, '…', page, '…', pages];
  }

  if (page <= 4)          return [1, 2, 3, 4, 5, '…', pages];
  if (page >= pages - 3)  return [1, '…', pages - 4, pages - 3, pages - 2, pages - 1, pages];
  return [1, '…', page - 1, page, page + 1, '…', pages];
}

// ── Totales de la selección ──────────────────────────────────────────────────
function updateStats() {
  try { _updateStats(); } catch (e) { console.error('updateStats error:', e); }
}
function _updateStats() {
  const n     = selected.size;
  const hours = document.getElementById('s-hours');
  const grams = document.getElementById('s-grams');
  const mats  = document.getElementById('sel-mats');

  document.getElementById('s-count').textContent = n + ' de ' + visibleIdx.length;
  document.getElementById('btn-copy-h').disabled = !n;
  document.getElementById('btn-copy-g').disabled = !n;

  if (!n) {
    hours.textContent = '0.00';
    grams.textContent = '0.0';
    mats.innerHTML = '<span class="sel-note">Tocá las impresiones para sumarlas.</span>';
    return;
  }

  const sel       = [...selected].map(i => tasks[i]);
  const totalSecs = sel.reduce((a, t) => a + (t.costTime || 0), 0);
  const totalG    = sel.reduce((a, t) => a + getFilteredGrams(t), 0);
  const ok        = sel.filter(t => t.status === 2).length;

  hours.textContent = fmtHours(totalSecs);
  grams.textContent = fmtG(totalG);

  // Desglose por material y color: es lo que cambia el precio
  const porMat = new Map();
  sel.forEach(t => {
    (t.amsDetailMapping || []).forEach(a => {
      if (activeFil && a.filamentType !== activeFil) return;
      if (activeCol && parseColor(a.sourceColor) !== activeCol) return;
      const hex  = parseColor(a.sourceColor) || '#555555';
      const type = a.filamentType || 'Desconocido';
      const key  = type + '|' + hex;
      const p    = porMat.get(key) || {g:0, hex, type};
      p.g += a.weight || 0;
      porMat.set(key, p);
    });
  });

  const filas = [...porMat.values()].sort((a,b) => b.g - a.g).map(v => `
    <div class="mat-row">
      <span class="mat-dot" style="background:${v.hex}"></span>
      <span class="mat-name">${esc(v.type)}</span>
      <span class="mat-val num">${v.g.toFixed(1)} g</span>
    </div>`).join('');

  mats.innerHTML = filas + `
    <div class="mat-row">
      <span class="mat-name" style="color:var(--ac-tx)">${n} trabajo${n === 1 ? '' : 's'} · ${ok}/${n} OK</span>
      <span class="mat-val num" style="color:var(--ac-tx)">${fmtHours(totalSecs / n)} h c/u</span>
    </div>`;
}

// ── Init ─────────────────────────────────────────────────────────────────────
function initRange() {
  const fechas = tasks.map(t => t.startTime).filter(Boolean).sort();
  if (!fechas.length) return;
  document.getElementById('hdr-range').textContent =
    fmtDay(fechas[0]) + ' → ' + fmtDay(fechas[fechas.length - 1]);
}

buildGlobalStats();
buildFilters();
initRange();
pageSize = medirPageSize();
goToPage(pageFromHash(), false);
ajustarPagina(true);   // segunda pasada, ya con una card real para medir
updateStats();

let resizeT = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeT);
  // Si no cambió el tamaño de página igual hay que repintar: al cruzar el
  // breakpoint cambia la cantidad de slots del paginador.
  resizeT = setTimeout(() => { if (!ajustarPagina(true)) renderPage(); }, 180);
});
</script>
</body>
</html>"""


# Lo único que el visor lee de cada trabajo. Lo que devuelve la nube trae mucho
# más: el campo `extention` solo son 704 KB (el 70 % del JSON) de URLs firmadas a
# metadata de MakerWorld que caducan en una hora y que nadie abre nunca. Podando,
# el HTML pasa de 1,24 MB a 0,15 MB de datos.
# `id` no lo usa el JS, pero es la clave de las miniaturas y la que va a necesitar
# cualquier integración posterior (print-roi), así que se conserva.
CAMPOS_TASK = ("id", "title", "startTime", "status", "costTime", "weight", "cover")
CAMPOS_AMS  = ("filamentType", "sourceColor", "weight")


def slim_tasks(tasks: list) -> list:
    """Deja solo los campos que el visor usa de verdad."""
    salida = []
    for t in tasks:
        d = {k: t[k] for k in CAMPOS_TASK if k in t}
        ams = [{k: a[k] for k in CAMPOS_AMS if k in a}
               for a in (t.get("amsDetailMapping") or [])]
        if ams:
            d["amsDetailMapping"] = ams
        salida.append(d)
    return salida


def inline_covers(tasks: list) -> int:
    """
    Reemplaza "covers/<id>.webp" por un data: URI con la imagen adentro. Es lo que
    convierte al visor en un archivo suelto: sin esto, fuera de su carpeta quedan
    233 cuadros rotos. Muta la lista recibida (tiene que ser la podada, no la
    original). Devuelve cuántas quedaron embebidas.
    """
    n = descartadas = 0
    for t in tasks:
        rel = t.get("cover")
        if not rel:
            continue

        datos = None
        if rel.startswith("covers/"):
            try:
                with open(f"{OUTPUT_DIR}/{rel}", "rb") as f:
                    datos = f.read()
            except OSError as e:
                print(f"  [portable] sin miniatura para {t.get('id')}: {e}")

        if datos:
            t["cover"] = "data:image/webp;base64," + base64.b64encode(datos).decode("ascii")
            n += 1
        else:
            # No se pudo embeber: el `cover` que quedó es la URL firmada de S3 que
            # trae la nube, y esa caduca a los 30 min. Dejarla haría que el archivo
            # "portable" dependa de la red para mostrar una imagen que ya no carga,
            # y encima la firma viajaría adentro del archivo que se comparte.
            # Mejor sin portada: la card cae sola al placeholder.
            t.pop("cover", None)
            descartadas += 1

    return n, descartadas


def generate_html(tasks: list, portable: bool = False) -> str:
    # En el portable no va el auto-refresh: abierto con doble clic no hay servidor
    # que le sirva una versión nueva, así que solo parpadearía al pedo.
    refresh_tag = (
        f'<meta http-equiv="refresh" content="{REFRESH_INTERVAL}">'
        if REFRESH_INTERVAL > 0 and not portable else ''
    )
    datos = slim_tasks(tasks)
    if portable:
        n, fuera = inline_covers(datos)
        extra = f", {fuera} descartadas por no tener copia local" if fuera else ""
        print(f"  {n} miniaturas embebidas{extra}")

    # El JSON va último: así nada de lo que venga en los datos se confunde con un marcador
    return (
        HTML_TEMPLATE
        .replace("__REFRESH_TAG__", refresh_tag)
        .replace("__PAGE_SIZE__", str(PAGE_SIZE))
        .replace("__N__", str(len(datos)))
        .replace("__TASKS_JSON__", json.dumps(datos, ensure_ascii=False))
    )



# ── MAIN ──────────────────────────────────────────────────────────────────────

def fetch_and_render(token: str):
    print(f"Obteniendo historial (máx. {LIMIT} trabajos)...")
    tasks = get_tasks(token)
    print(f"{len(tasks)} trabajos devueltos por la nube (ventana de 90 días)")

    db_seed_from_json()
    if tasks:
        nuevos, conocidos = db_upsert(tasks)
        print(f"Base → {nuevos} nuevos, {conocidos} ya conocidos")

    # El visor se arma con TODO lo acumulado, no solo con lo que sigue en la nube
    tasks = db_all()
    if not tasks:
        print("Sin resultados.")
        return
    print(f"{len(tasks)} trabajos en la base\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cache_covers(tasks)

    if SAVE_JSON:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        print(f"JSON  → {JSON_FILE}")

    html = generate_html(tasks)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML  → {HTML_FILE} ({os.path.getsize(HTML_FILE)/1e6:.2f} MB)")

    # Versión de un solo archivo: se abre con doble clic, sin Docker y sin
    # servidor, y se puede mandar por Telegram tal cual.
    if PORTABLE:
        portable_html = generate_html(tasks, portable=True)
        with open(PORTABLE_FILE, "w", encoding="utf-8") as f:
            f.write(portable_html)
        print(f"Único → {PORTABLE_FILE} ({os.path.getsize(PORTABLE_FILE)/1e6:.2f} MB)")


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))
        return s.getsockname()[0]
    except OSError:
        return "localhost"
    finally:
        s.close()


def viewer_urls(port) -> tuple:
    """(URL local, URL de red). La local es la que sirve a quien lo corre en su
    propia maquina; la de red es para entrar desde el telefono u otra PC."""
    return (f"http://localhost:{port}/historial.html",
            f"http://{local_ip()}:{port}/historial.html")


def serve(token: str):
    """Modo servidor: atiende HTTP y regenera el HTML on-demand si está stale."""
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import threading

    port = int(os.getenv("VIEWER_PORT", "8766"))
    interval = max(REFRESH_INTERVAL, 60)  # nunca por debajo de 60s para no martirizar la API
    last_render = 0.0
    lock = threading.Lock()

    def maybe_refresh():
        nonlocal last_render
        with lock:
            age = time.time() - last_render
            if last_render == 0.0 or age >= interval:
                print(f"[refresh] last_render hace {age:.0f}s, regenerando...")
                try:
                    fetch_and_render(token)
                    last_render = time.time()
                except Exception as e:
                    print(f"[refresh] error: {e} (sirvo HTML cacheado si existe)")

    maybe_refresh()  # render inicial

    output_dir = OUTPUT_DIR

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=output_dir, **kw)

        def send_head(self):
            # Defensa en profundidad: nunca servir dotfiles (cubre GET y HEAD)
            if any(p.startswith(".") for p in self.path.split("/") if p):
                self.send_error(403)
                return None
            if self.path in ("/", "/historial.html"):
                maybe_refresh()
            return super().send_head()

        def log_message(self, fmt, *args):
            print(f"[http] {self.address_string()} {fmt % args}")

    local, red = viewer_urls(port)
    print(f"\n{'=' * 60}")
    print(f"  Visor live: {local}")
    print(f"  Desde otro equipo o el telefono: {red}")
    print(f"{'=' * 60}")
    print(f"Refresh: cada {interval}s o en cada reload del navegador (lo que pase antes)\n")
    HTTPServer(("", port), Handler).serve_forever()


def main():
    token = get_token()
    port = os.getenv("VIEWER_PORT", "8766")
    local, red = viewer_urls(port)

    if os.getenv("SERVE", "0") == "1":
        serve(token)
        return

    while True:
        try:
            fetch_and_render(token)
        except Exception as e:
            print(f"Error en este ciclo: {e}")
        print(f"\n{'=' * 60}")
        print(f"  Visor: {local}")
        print(f"  Desde otro equipo o el telefono: {red}")
        print(f"{'=' * 60}")
        if REFRESH_INTERVAL <= 0:
            return
        print(f"Próximo refresh en {REFRESH_INTERVAL}s...\n")
        time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
