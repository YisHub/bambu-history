# 🖨️ Bambu Print History

Visor del historial de impresiones de tu Bambu Lab.  
Descarga todas tus impresiones desde la nube, las muestra con thumbnails, filamentos y colores, y te deja calcular estadísticas de las que seleccionés.

![Demo](https://img.shields.io/badge/Docker-ready-blue?logo=docker) ![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python) ![License](https://img.shields.io/badge/license-MIT-green)

---

## ¿Qué hace?

- Descarga tu historial desde la nube de Bambu Lab
- Genera una página web interactiva con:
  - **Grid de cards** con thumbnail de cada impresión
  - **Filtros** por tipo de filamento (PLA, PETG, ABS…) y por color
  - **Selección** de impresiones con click
  - **Estadísticas en tiempo real**: tiempo total, filamento total, promedio por impresión
  - **Breakdown** por tipo y color de filamento con barras proporcionales
- Guarda el historial en JSON
- **Recuerda el login**: no pide código de verificación cada vez (token guardado ~3 meses)

---

## Requisitos

- [Docker](https://docs.docker.com/engine/install/) instalado y corriendo
- Una cuenta de Bambu Lab

> **Windows**: instalá [Docker Desktop](https://www.docker.com/products/docker-desktop/) con WSL2. Ver [sección Windows](#windows-wsl2) al final.

---

## ⚡ Inicio rápido

```bash
# 1. Cloná el repositorio
git clone https://github.com/YisHub/bambu-history.git
cd bambu-history

# 2. Copiá y completá la configuración
cp .env.example .env
nano .env          # o cualquier editor de texto

# 3. Construí la imagen (solo la primera vez)
docker compose build

# 4. Ejecutá
docker compose run --rm bambu-history
```

Cuando termine, el script imprime la URL del visor:

```
Visor: http://192.168.0.235:8765/historial.html
```

Abrila desde cualquier dispositivo de tu red. Si preferís abrir el HTML directo, está en `output/historial.html`.

### Visor web (opcional)

Para no abrir el HTML a mano cada vez, levantá el visor que sirve la carpeta `output/` por HTTP:

```bash
docker compose up -d viewer
```

Queda corriendo en segundo plano en el puerto **8765**. La URL es la misma que imprime el script al terminar.

**Auto-apagado**: por defecto el visor se apaga solo a los **30 min** (evita dejar el puerto abierto indefinidamente). Controlable con `VIEWER_TIMEOUT`:

```bash
# Tenerlo levantado 2 horas
VIEWER_TIMEOUT=2h docker compose up -d viewer

# Que quede hasta que vos lo apagues
VIEWER_TIMEOUT=0 docker compose up -d viewer
```

Apagarlo manualmente: `docker compose down`.

### Auto-refresh del historial

Hay dos formas, según qué tan vivo quieras que sea el historial:

**A. Loop simple (regenera siempre, sirvas o no):**

```bash
REFRESH_INTERVAL=300 docker compose up -d bambu-history
```

El script entra en loop: cada 300s repollea Bambu Cloud y regenera el HTML. La página lleva un `<meta refresh>` con el mismo intervalo. En este modo el visor estático (`viewer`) sigue sirviendo los archivos.

**B. Modo live (recomendado) — refresh cada N seg O al recargar la página:**

```bash
SERVE=1 REFRESH_INTERVAL=300 docker compose up -d bambu-history
```

Acá el propio `bambu-history` levanta el servidor HTTP en el puerto **8765** y reemplaza al `viewer`. Cada request a `historial.html` chequea cuán vieja está la data: si pasó más de `REFRESH_INTERVAL` segundos desde la última generación, repollea Bambu Cloud antes de servir.

Ventajas vs. modo A:
- Si recargás (F5) la página, ves data fresca (no tenés que esperar al próximo tick del loop).
- Si nadie visita la página, no se desperdician llamadas a la cloud.
- Mismo puerto, misma URL, sin `viewer` aparte.

> **No corras `viewer` y `bambu-history` con `SERVE=1` al mismo tiempo** — los dos quieren bindear el puerto 8765.

> El piso mínimo de `REFRESH_INTERVAL` en modo live es **60s** (para no martirizar la API de Bambu).

> Solo activá cualquiera de estos modos **después** de la primera ejecución interactiva (la que pide el código de 6 dígitos). Con el token ya guardado en `data/.bambu_token` no se necesita stdin.

---

## Configuración (`.env`)

```env
BAMBU_EMAIL=tu@email.com        # Email de tu cuenta Bambu Lab
BAMBU_PASSWORD=tupassword       # Contraseña

BAMBU_DEVICE_ID=03919D573008914 # Serial de tu impresora (opcional)
                                # Vacío = trae todas las impresoras de tu cuenta

LIMIT=100                       # Máximo de impresiones a traer
SAVE_JSON=1                     # 1 = guardar historial.json, 0 = no
OUTPUT_FILE=/output/historial.json
```

### ¿Dónde encontrar el serial?

En la pantalla táctil de la Bambu:  
**Settings → Dispositivo → SN de la Impresora**

---

## Verificación por email (primer uso)

Bambu Lab requiere un código de 6 dígitos la primera vez:

```
Verificación requerida. Enviando código a tu@email.com...
Código enviado. Revisá tu email.

Código de 6 dígitos: _
```

Ingresás el código y listo. **Las próximas veces no lo pide** — el token se guarda en `output/.bambu_token`.

> Si el token expira (~3 meses), el script lo detecta y vuelve a pedir el código automáticamente.

---

## Cómo usar el visor HTML

Abrí `output/historial.html` en cualquier navegador.

### Filtros

| Elemento | Acción |
|---|---|
| Pills `PLA` `PETG` `ABS`… | Muestra solo impresiones de ese material |
| Dots de color | Muestra solo impresiones que usaron ese color |
| Combinar filtros | Filamento + color al mismo tiempo |

### Selección

- **Click en card** → seleccionás (borde azul + ✓)
- **"Sel. visibles"** → selecciona todas las filtradas
- **"Limpiar"** → deselecciona todo

### Estadísticas (tiempo real)

| Campo | Descripción |
|---|---|
| Seleccionadas | Cantidad elegida |
| Tiempo total | Suma de horas de impresión |
| Promedio | Tiempo promedio por impresión |
| Filamento total | Gramos totales usados |
| Completadas | OK vs total seleccionado |

### Breakdown por filamento y color

Al seleccionar impresiones aparece un panel:
```
Por tipo          Por color
─────────────     ─────────────────────
PLA  ████  1.2kg  🟫 PLA  ████  342g
PETG ██    234g   ⬜ PLA  ██    180g
```

---

## Comandos útiles

```bash
# Traer más impresiones
LIMIT=200 docker compose run --rm bambu-history

# Solo una impresora
BAMBU_DEVICE_ID=03919D573008914 docker compose run --rm bambu-history

# Forzar re-login
rm data/.bambu_token
docker compose run --rm bambu-history

# Reconstruir si modificaste el script
docker compose build && docker compose run --rm bambu-history

# Visor estático (solo sirve el HTML; el script lo regeneras vos cuando querés)
docker compose up -d viewer

# Visor live: refresh cada 5 min o al recargar la página
SERVE=1 REFRESH_INTERVAL=300 docker compose up -d bambu-history

# Apagar todo
docker compose down

# Cambiar puerto (cambiar también el mapeo en docker-compose.yml si usás `viewer`)
VIEWER_PORT=9000 docker compose run --rm bambu-history
```

---

## Estructura del proyecto

```
bambu-history/
├── bambu_history.py        # Script principal
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example            # Plantilla de configuración
├── .env                    # Tu configuración ← NO subir a git
├── data/                   # Token de sesión ← NO subir a git, NO se sirve por HTTP
│   └── .bambu_token
└── output/                 # Generado al ejecutar ← NO subir a git
    ├── historial.html      # Visor web
    └── historial.json      # Datos en JSON
```

> **Por qué `data/` separado de `output/`**: el visor web sirve `output/` por HTTP sin auth. Si el token estuviera ahí dentro, cualquiera en la LAN podría descargarlo y suplantar tu cuenta de Bambu por ~3 meses. Por eso vive en `data/`, que no se sirve nunca.

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `Error: no se pudo obtener el token` | Verificá email y contraseña en `.env` |
| Imágenes no cargan en el HTML | Las URLs expiran ~30 min. Volvé a ejecutar para regenerar |
| `docker: command not found` | Verificá que Docker esté corriendo |
| Token expirado (pide código de nuevo) | Normal cada ~3 meses, ingresás el código una vez |

---

## Windows (WSL2)

<details>
<summary>Expandir instrucciones para Windows</summary>

1. Instalá [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. En Docker Desktop → Settings → Resources → WSL Integration → activá tu distro
3. Abrí una terminal WSL y navegá al proyecto:

```bash
cd /mnt/c/Users/TuUsuario/ruta/al/proyecto/bambu-history
```

4. El resto de los comandos son idénticos a Linux.

Los archivos de `output/` aparecen en Windows en la carpeta del proyecto normalmente.

</details>
