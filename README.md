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
  - **Horas y gramos de lo seleccionado**, en decimal y con un botón para copiar cada uno: es lo que se carga después en el sistema de ventas
  - **Desglose por tipo y color** de filamento, que es lo que cambia el precio
  - **Grid que llena la pantalla**: muestra tantas cards como entren, sin dejar hueco abajo
  - **Estadísticas globales** (totales y gráficos) en una vista aparte
- Guarda el historial en JSON
- **Acumula el historial en una base**: Bambu Cloud solo expone los **últimos 90 días**, así que lo que sale de esa ventana se pierde. Todo lo que se vio alguna vez queda en `data/historial.db` (SQLite) y el visor se genera desde ahí, no desde lo que devolvió el último fetch
- **Cachea las miniaturas en disco y las guarda en WebP**: las descarga a `output/covers/` en cada fetch, así no dependen de las URLs firmadas de Bambu (que caducan a los 30 min). WebP pesa ~la mitad que el PNG original sin diferencia visible
- **Recuerda el login**: no pide código de verificación cada vez (token guardado ~3 meses)

---

## Requisitos

- Una cuenta de Bambu Lab
- [Docker](https://docs.docker.com/engine/install/), **o** Python 3.12+ si preferís correrlo a mano (ver [Correr sin Docker](#correr-sin-docker))

> **Windows**: lo más simple es doble clic en `bambu-history.bat`, que no necesita Docker. Ver [sección Windows](#windows) al final.

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
Visor: http://192.168.0.235:8766/historial.html
```

Abrila desde cualquier dispositivo de tu red. Si preferís abrir el HTML directo, está en `output/historial.html`.

### Visor web (opcional)

Para no abrir el HTML a mano cada vez, levantá el visor que sirve la carpeta `output/` por HTTP:

```bash
docker compose up -d viewer
```

Queda corriendo en segundo plano en el puerto configurado (**8766** por defecto). La URL es la misma que imprime el script al terminar.

> **El puerto es de cada máquina.** El default es 8766, pero si en la tuya está ocupado, poné `VIEWER_PORT` en el `.env` y listo — no hay que tocar ni el compose ni el código:
>
> ```env
> VIEWER_PORT=9000
> ```
>
> Cómo se nota que un puerto está ocupado: el contenedor arranca y muere enseguida con `OSError: [Errno 98] Address already in use`, mientras `docker compose up -d` dice *Started*. Ojo que un servicio nativo (systemd) **no aparece en `docker ps`**, así que conviene mirar también `ss -tlnH`. Acá el 8765 lo usa `pc-agent`, el servicio que apaga el server desde el ESP32; por eso el default quedó en 8766.

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

Acá el propio `bambu-history` levanta el servidor HTTP en el puerto **8766** y reemplaza al `viewer`. Cada request a `historial.html` chequea cuán vieja está la data: si pasó más de `REFRESH_INTERVAL` segundos desde la última generación, repollea Bambu Cloud antes de servir.

Ventajas vs. modo A:
- Si recargás (F5) la página, ves data fresca (no tenés que esperar al próximo tick del loop).
- Si nadie visita la página, no se desperdician llamadas a la cloud.
- Mismo puerto, misma URL, sin `viewer` aparte.

> **No corras `viewer` y `bambu-history` con `SERVE=1` al mismo tiempo** — los dos quieren bindear el puerto 8766.

> **Acceso remoto por Tailscale**: el contenedor usa `network_mode: host`, así que la misma URL funciona con la IP de Tailscale de tu server (`tailscale ip -4`): `http://<tu-ip-tailscale>:8766/historial.html`.

> El piso mínimo de `REFRESH_INTERVAL` en modo live es **60s** (para no martirizar la API de Bambu).

> Solo activá cualquiera de estos modos **después** de la primera ejecución interactiva (la que pide el código de 6 dígitos). Con el token ya guardado en `data/.bambu_token` no se necesita stdin.

---

## Correr sin Docker

No hace falta Docker: el script corre derecho con Python 3.12+.

```bash
pip install -r requirements.txt
cp .env.example .env     # completá email y contraseña
python bambu_history.py
```

Fuera de Docker el script **lee el `.env` del directorio actual** por su cuenta (con Docker eso lo hace `env_file`). Lo que ya esté en el entorno tiene prioridad, así que esto sigue funcionando:

```bash
LIMIT=5 PAGE_SIZE=24 python bambu_history.py
```

Las rutas se acomodan solas: dentro de Docker usa `/output` y `/data` (los volúmenes), y fuera `./output` y `./data`, al lado del script. Se detecta con `/.dockerenv`. Si necesitás otra cosa, `OUTPUT_DIR` y `DATA_DIR` mandan sobre todo lo demás.

> Si faltan las credenciales, corta con un mensaje claro en vez de un `KeyError`.

---

## Configuración (`.env`)

```env
BAMBU_EMAIL=tu@email.com        # Email de tu cuenta Bambu Lab
BAMBU_PASSWORD=tupassword       # Contraseña

BAMBU_DEVICE_ID=00M00A000000000 # Serial de tu impresora (opcional, de ejemplo)
                                # Vacío = trae todas las impresoras de tu cuenta

VIEWER_PORT=8766                # Puerto del visor ← propio de cada máquina
LIMIT=100                       # Máximo de impresiones a traer
PAGE_SIZE=auto                  # Cards por página: "auto" llena la pantalla, o un número fijo
COVER_QUALITY=82                # Calidad WebP de las miniaturas (1-100)
SAVE_JSON=1                     # 1 = guardar historial.json, 0 = no
```

Las que solo hacen falta en casos puntuales:

| Variable | Para qué |
|---|---|
| `SERVE=1` | Modo live: el script sirve el HTTP y regenera al recargar |
| `REFRESH_INTERVAL` | Segundos entre refrescos (0 = una sola corrida) |
| `VIEWER_TIMEOUT` | Auto-apagado del servicio `viewer` (`30m` por defecto, `0` = sin apagado) |
| `OUTPUT_DIR` / `DATA_DIR` | Forzar dónde se guardan los archivos |
| `AM_I_IN_A_DOCKER_CONTAINER` | Escape: tratar el entorno como Docker aunque no haya `/.dockerenv` |

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

Ingresás el código y listo. **Las próximas veces no lo pide** — el token se guarda en `data/.bambu_token`, que no se sirve por HTTP.

> Si el token expira (~3 meses), el script lo detecta y vuelve a pedir el código automáticamente.

---

## Cómo usar el visor HTML

Abrí `output/historial.html` en cualquier navegador.

La interfaz está armada alrededor del uso real: **mirar el historial, seleccionar impresiones y leer horas y gramos** para cargarlos en el sistema de ventas y las calculadoras de precios. Por eso todo lo operativo vive en la **barra lateral** y el cuerpo queda solo con el grid y el paginador.

### Filtros (en el lateral)

| Elemento | Acción |
|---|---|
| Pills `PLA` `PETG` `ABS`… | Muestra solo impresiones de ese material |
| Dots de color | Muestra solo impresiones que usaron ese color |
| Combinar filtros | Filamento + color al mismo tiempo |

### Paginación

El grid **llena la pantalla exactamente**, sin dejar hueco abajo. Mide cuántas columnas entran, cuántas filas caben en el alto disponible, y después le da al grid ese alto justo repartido entre las filas (`grid-template-rows: repeat(N, 1fr)`): las portadas se estiran o achican un poco y recortan con `object-fit`, en vez de dejar espacio muerto.

- En un monitor grande entran más columnas **y** más filas (en 1920×1080 son 6 × 3 = 18 cards).
- Al cambiar el tamaño de la ventana se recalcula solo, **conservando el lugar** donde estabas.
- En el teléfono (≤820 px) no se fuerza el alto: la página scrollea normal y las cards mantienen su proporción 4:3.

> **Ojo con el alto real**: lo que importa no es que el monitor sea de 1080 px, sino el alto del *viewport*. Entre pestañas, barra de direcciones y barra de tareas se van ~150 px, y el cálculo usa ese alto real.

`PAGE_SIZE=auto` es el default. Poniéndole un número (`PAGE_SIZE=24`) se fija, se desactiva el llenado y las cards vuelven a su proporción natural.

Abajo del grid queda el paginador, que solo aparece si hay más de una página.

### El paginador no se mueve

Pasar de página no debería obligarte a reapuntar el mouse, así que:

- **Cantidad de slots fija**: siempre 7 en escritorio y 5 en pantallas angostas, rellenando con `…`. Si la cantidad variara (`1 2 3 … 20` vs `1 … 5 6 7 … 20`), el bloque cambiaría de ancho y las flechas se correrían.
- **Todos los slots miden lo mismo** (`--pg-w`, calculado con los dígitos del total), así cambiar un `9` por un `10`, o un número por `…`, no mueve nada.
- **Los botones no scrollean la página**: llaman a `goToPage(n, false)`. Con `true` la vista salta al inicio del grid, que en el teléfono corre el botón de abajo del dedo.
- En pantallas angostas son 5 slots porque con 7 el paginador se parte en dos renglones y las flechas quedan en líneas distintas.

| Detalle | Comportamiento |
|---|---|
| Qué pagina | Lo **filtrado**, no el historial entero: al filtrar por material o color se recalculan las páginas |
| Al cambiar de filtro | Volvés a la página 1 |
| `Seleccionar visibles` | Selecciona **todo lo filtrado**, no solo la página a la vista |
| Selección entre páginas | Se mantiene: si seleccionás en la página 1 y volvés, siguen marcadas |
| Modo live (`<meta refresh>`) | La página actual queda en la URL (`#p=3`), así el auto-refresh no te devuelve a la 1 |

> Solo se montan en el DOM las cards de la página actual. Ojo: el HTML sigue trayendo el JSON completo del historial, así que esto aligera el render y el filtrado, no el peso de la descarga.

### Selección

- **Click en card** → seleccionás (borde verde + ✓)
- **"Seleccionar visibles"** → selecciona **todo lo filtrado**, no solo la página a la vista
- **"Limpiar selección"** → deselecciona todo

La selección se mantiene al cambiar de página y se recorta sola si un filtro deja algo afuera.

### Horas y gramos (arriba del lateral)

Es la razón de ser del visor, así que va primero y siempre visible:

| Campo | Descripción |
|---|---|
| Horas | Suma de la selección, en **decimal** (`17,75`), no en `17 h 45` |
| Gramos | Suma de la selección, en decimal (`538,6`) |
| Por material | Desglose por tipo y color — es lo que cambia el precio |
| N de M | Cuántas seleccionaste sobre cuántas hay filtradas |

Cada número tiene su propio botón **Copiar**, y copia el valor **pelado** (`17,75`, `538,6`), sin unidad, listo para pegar en una celda.

> **Coma decimal**: se copia tal cual se ve, con coma. Si tu planilla espera punto, cambiá los dos `.replace('.', ',')` de `fmtHours()` y `fmtG()` en `bambu_history.py`.

> La página se sirve por HTTP plano, donde el navegador no habilita `navigator.clipboard` (solo funciona en contextos seguros). Por eso el copiado cae a un `textarea` temporal, que sí anda por LAN y Tailscale.

### Estadísticas globales

Las métricas de todo el historial y los gráficos por tipo y color viven en una **vista aparte**, detrás del botón *Estadísticas* del header. Son para mirar de vez en cuando, no para el uso diario.

### En el teléfono

El lateral pasa arriba del grid, así que lo primero que ves al entrar es **horas, gramos y los dos botones de copiar**; después los filtros y el grid en 2 columnas. Los botones son de 44 px para que se puedan tocar bien.

---

## Comandos útiles

```bash
# Traer más impresiones
LIMIT=200 docker compose run --rm bambu-history

# Fijar el tamaño de página (por defecto se adapta a la pantalla)
PAGE_SIZE=50 docker compose run --rm bambu-history

# Solo una impresora
BAMBU_DEVICE_ID=00M00A000000000 docker compose run --rm bambu-history

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

# Cambiar el puerto (default 8766). Vale para el visor y para `viewer`:
# no hay que tocar el compose
VIEWER_PORT=9000 SERVE=1 docker compose up -d bambu-history

# Correrlo sin Docker
python bambu_history.py
```

---

## Estructura del proyecto

```
bambu-history/
├── bambu_history.py        # Script principal
├── bambu-history.bat       # Lanzador para Windows (sin Docker)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example            # Plantilla de configuración
├── .env                    # Tu configuración ← NO subir a git
├── design/                 # Mockups del visor ← local, NO está en el repo
├── .venv/                  # Lo crea el .bat en Windows ← NO subir a git
├── data/                   # ← NO subir a git, NO se sirve por HTTP
│   ├── .bambu_token        # Token de sesión
│   └── historial.db        # Acumulado histórico (SQLite) ← la fuente de verdad
└── output/                 # Generado al ejecutar ← NO subir a git
    ├── historial.html      # Visor web
    ├── historial.json      # Volcado de la base en JSON
    └── covers/             # Miniaturas cacheadas en WebP (covers/<id>.webp)
```

> **Por qué `data/` separado de `output/`**: el visor web sirve `output/` por HTTP sin auth. Si el token estuviera ahí dentro, cualquiera en la LAN podría descargarlo y suplantar tu cuenta de Bambu por ~3 meses. Por eso vive en `data/`, que no se sirve nunca. La base vive ahí por lo mismo, y porque es la única copia de los trabajos que ya salieron de la ventana de 90 días: `output/` se puede borrar entero y se regenera, `data/` no.

---

## Qué no va al repo

Este repositorio es **público**. Lo que es de tu máquina o de tu taller se queda afuera:

| Fuera del repo | Por qué |
|---|---|
| `.env` | Email y contraseña de Bambu Lab |
| `data/` | Token de sesión (vale ~3 meses) y la base con todo el historial |
| `output/` | HTML, JSON y miniaturas de tus impresiones |
| `design/` | Los mockups llevan miniaturas reales y títulos con nombres de clientes |

Todo eso está en `.gitignore`. La regla práctica: **ni seriales reales, ni IPs de tu red o tu tailnet, ni miniaturas de trabajos**. Los ejemplos del README y del `.env.example` usan valores inventados a propósito (`00M00A000000000`).

Antes de commitear, si tenés dudas:

```bash
git check-ignore -v design/ .env data/ output/   # confirmá que están ignorados
git grep -n "TU-SERIAL\|TU-IP"                   # buscá lo que no querés publicar
```

> **Nota honesta**: hasta septiembre de 2026 el serial real de la impresora estaba en `.env.example` y en el README, puesto como si fuera un ejemplo. Se reemplazó, pero **sigue en la historia ya publicada** y en el fork del repo: eso no se puede deshacer sin reescribir historia pública. No es una credencial por sí sola —para la nube hace falta la cuenta, y para el acceso local el código de acceso de la impresora—, pero no conviene repetir el patrón.

---

## La ventana de 90 días

La API de Bambu Cloud responde con un campo `total` y no entrega nada más allá: hoy son **233 trabajos**, los de los últimos ~90 días. Subir `LIMIT` no trae más, porque el tope no es del cliente sino de la nube.

Por eso el flujo es acumulativo:

1. Se pide a la nube lo que haya (paginado de a 50, hasta `LIMIT`).
2. Se hace *upsert* en `data/historial.db` por `id`: lo nuevo entra, lo conocido se actualiza, y lo que ya no está en la nube **se queda en la base**.
3. El visor y el `historial.json` se generan con **todo** lo acumulado.

La primera corrida con la base vacía importa el `output/historial.json` que existiera de antes, así no se arranca perdiendo lo ya bajado.

> Las miniaturas siguen la misma lógica: un `.png` de una versión anterior se convierte a WebP en vez de volver a descargarse, porque para un trabajo fuera de la ventana ese archivo local es la única copia que queda.

```bash
# Cuántos trabajos hay acumulados
sqlite3 data/historial.db "SELECT COUNT(*) FROM tasks;"

# Los más viejos que la nube ya no tiene
sqlite3 data/historial.db "SELECT start_time, title FROM tasks ORDER BY start_time LIMIT 5;"
```

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `Error: no se pudo obtener el token` | Verificá email y contraseña en `.env` |
| Imágenes no cargan en el HTML | Las miniaturas se cachean en `output/covers/<id>.webp`. Si alguna falta, la descarga falló (red/S3 lento): volvé a ejecutar y reintenta solo las que falten |
| `docker: command not found` | Verificá que Docker esté corriendo |
| Token expirado (pide código de nuevo) | Normal cada ~3 meses, ingresás el código una vez |
| `docker compose up -d` dice *Started* pero la página no carga / da 404 | Mirá `docker logs bambu-history-bambu-history-1`. Si dice `Address already in use`, otro proceso tiene ese puerto: poné otro en `VIEWER_PORT`. Acordate de mirar `ss -tlnH` además de `docker ps`, porque un servicio nativo no aparece en Docker |
| `KeyError` o falta de credenciales | Copiá `.env.example` a `.env` y completalo. Corriendo sin Docker, el `.env` tiene que estar en el directorio desde donde ejecutás |

---

## Windows

Dos caminos. El primero no necesita Docker y es el recomendado para alguien que solo quiere usarlo.

### Opción A: doble clic (sin Docker)

Doble clic en **`bambu-history.bat`**. La primera vez:

1. Si no tenés Python, te avisa y te pasa el link. Instalalo tildando **"Add python.exe to PATH"**.
2. Crea un entorno virtual en `.venv/` e instala las dependencias por su cuenta.
3. Si no existe `.env`, lo copia de `.env.example`, lo abre en el Bloc de notas y se detiene: completá `BAMBU_EMAIL` y `BAMBU_PASSWORD`, guardá y volvé a ejecutarlo.
4. Puede pedir un código de 6 dígitos que llega por mail (ver [Verificación por email](#verificación-por-email-primer-uso)).

Arranca en **modo live**, que es el equivalente a:

```bash
SERVE=1 REFRESH_INTERVAL=300 docker compose up bambu-history
```

O sea: sirve el visor y regenera el historial cada 5 minutos, o cuando recargás la página. La ventana tiene que quedar abierta; para cortar, `Ctrl+C` o cerrala.

Las variables del entorno ganan sobre los valores del `.bat`, igual que en Linux:

```bat
set SERVE=0
bambu-history.bat
```

Eso hace una sola pasada y termina, sin levantar el servidor.

### Opción B: Docker Desktop + WSL2

<details>
<summary>Expandir instrucciones</summary>

1. Instalá [Docker Desktop](https://www.docker.com/products/docker-desktop/)
2. En Docker Desktop → Settings → Resources → WSL Integration → activá tu distro
3. Abrí una terminal WSL y navegá al proyecto:

```bash
cd /mnt/c/Users/TuUsuario/ruta/al/proyecto/bambu-history
```

4. El resto de los comandos son idénticos a Linux.

Los archivos de `output/` aparecen en Windows en la carpeta del proyecto normalmente.

</details>
