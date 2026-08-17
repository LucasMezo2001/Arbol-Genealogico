# Árbol Genealógico

Aplicación para gestionar y visualizar un árbol genealógico, con un scraper propio
que descarga los registros sacramentales (bautismos, matrimonios y defunciones)
publicados por archivos diocesanos históricos:

- [AHEB-BEHA](https://internet.aheb-beha.org/) (Bizkaia) y [AHDV-GEAH](https://internet.ahdv-geah.org/) (Álava),
  misma plataforma SIGA-AKIS.
- [AHDSS](https://artxiboa.mendezmende.org/) (Gipuzkoa, portal de Méndez Mende), plataforma distinta (Yii/Arinka).

y los persiste en un Postgres local dockerizado para poder consultarlos offline.

---

## Requisitos

- **Python 3.12+**
- **Poetry** ([instalación](https://python-poetry.org/docs/#installation))
- **Docker Desktop** (para levantar Postgres + pgAdmin)

---

## Instalación (desarrollo local)

```bash
git clone https://github.com/LucasMezo2001/Arbol-Genealogico.git
cd Arbol-Genealogico
poetry install

# Configurar variables de entorno
cp .env.example .env
# Editar .env según sea necesario (usuario/contraseña de Postgres, User-Agent del scraper...)

# Instalar pre-commit hooks
poetry run pre-commit install
```

---

## Estructura

```
docker/                          # docker-compose.yml (Postgres + pgAdmin)
src/arbol_genealogico/
  config/                        # settings (pydantic-settings), paths
  entrypoints/                   # CLI (Typer)
  features/
    scraping/                    # enumerar_jobs, procesar_listado, procesar_ficha, rango (AHDV-GEAH/AHDSS)
  infrastructure/
    db/                          # modelos SQLAlchemy, migraciones Alembic, seeds
    scraper/                     # client.py (HTTP), endpoints.py, parser.py
  domain/                        # entidades y reglas de dominio
  front/                         # interfaz de usuario
  utils/                         # utilidades
config/                          # YAML de configuración
tests/
  fixtures/                      # HTMLs reales capturados del portal (fixtures de tests)
  unit/                          # tests del parser, cliente HTTP (respx) y CLI
data/                            # (ignorado por git) HTML raw cacheado del scraper
logs/                            # logs locales (ignorados por git)
```

---

## Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ENV` | Entorno activo: `dev`, `preprod` o `prod` | `dev` |
| `CONFIG_PATH` | Ruta al YAML de configuración | `config/config.yaml` |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Credenciales del contenedor de Postgres | `arbol` |
| `POSTGRES_PORT` | Puerto expuesto en el host | `5433` |
| `DATABASE_URL` | URL de conexión (SQLAlchemy async) usada por la app | `postgresql+asyncpg://arbol:arbol@localhost:5433/arbol` |
| `PGADMIN_PORT` / `PGADMIN_EMAIL` / `PGADMIN_PASSWORD` | Acceso a pgAdmin | `8081` |
| `SCRAPER_BASE_URL` | Dominio del portal SIGA-AKIS de AHEB-BEHA (Bizkaia) | `https://internet.aheb-beha.org` |
| `SCRAPER_BASE_URL_AHDV_GEAH` | Dominio del portal SIGA-AKIS de AHDV-GEAH (Álava) | `https://internet.ahdv-geah.org` |
| `SCRAPER_BASE_URL_AHDSS` | Dominio del portal de AHDSS (Gipuzkoa) | `https://artxiboa.mendezmende.org` |
| `SCRAPER_USER_AGENT` | User-Agent identificable (recomendado incluir un email de contacto) | `arbol-genealogico/0.1 (contacto: tu-email@example.com)` |
| `SCRAPER_MIN_DELAY_S` / `SCRAPER_MAX_DELAY_S` | Rango de espera aleatoria entre peticiones (segundos) | `0.8` / `1.6` |
| `SCRAPER_MAX_RETRIES` | Reintentos máximos ante 429/5xx/errores de red | `5` |
| `SCRAPER_RAW_DIR` | Carpeta donde se cachea el HTML descargado | `D:/Arbol Genealogico/data/raw` |

Copia `.env.example` a `.env`. El fichero `.env` **no** se versiona.

---

## Base de datos: arranque con Docker

El Postgres vive en un contenedor con un volumen con nombre gestionado por Docker
(persistente en el disco, independiente de rutas de Windows). pgAdmin queda
disponible para inspeccionar la base visualmente.

```bash
# Levanta Postgres + pgAdmin y aplica las migraciones de Alembic
poetry run arbol db up

# Precarga las 115 localidades de referencia (idempotente)
poetry run arbol db seed
```

- pgAdmin: http://localhost:8081 (usuario/contraseña definidos en `.env`).
- Detener los contenedores (sin perder datos): `poetry run arbol db down`.
- Aplicar migraciones sueltas más adelante: `poetry run alembic upgrade head`.

---

## Ejecución del scraper

Es respetuoso por diseño en los tres archivos: 1 sola petición en vuelo, espera
aleatoria entre peticiones, backoff exponencial ante errores, respeta
`robots.txt` (si el portal lo publica) y usa un User-Agent identificable. La
cola de trabajo (`scrape_fichas`) es compartida entre archivos: cada fila
sabe a qué archivo pertenece (columna `archivo`) y con qué cliente HTTP
(dominio/codificación) hay que descargarla.

### Un solo comando: `scrape all`

Orquesta plan + listados + fichas en un proceso (alterna lotes de listados
y fichas hasta agotar ambas colas). Por defecto **no** encola el barrido
completo de Álava/Gipuzkoa (millones de IDs); usa `--con-rango` si lo quieres.

```bash
# Bizkaia: plan + listados + fichas (y fichas pendientes de otros archivos)
poetry run arbol scrape all

# Igual + encolar rangos completos de AHDV-GEAH y AHDSS antes de trabajar
poetry run arbol scrape all --con-rango

# Prueba corta: un ciclo de listados y otro de fichas
poetry run arbol scrape all --no-continuo --lote-listados 5 --lote-fichas 20
```

Flags útiles: `--sin-plan`, `--sin-listados`, `--sin-fichas`.

### AHEB-BEHA (Bizkaia): por localidad + año

Recorre `sacramento × localidad × año` (1501-1900) usando la "búsqueda
especial" del portal (sólo localidad + rango de fechas, sin depender de
apellidos), pagina los listados y descarga la ficha completa de cada
registro.

```bash
# 1. Rellena scrape_jobs con todas las combinaciones pendientes
poetry run arbol scrape plan

# 2. Descarga y pagina los listados, encolando cada id en scrape_fichas
poetry run arbol scrape listados

# 3. Descarga y parsea cada ficha, upsertando personas/parroquias/fondos/registros
poetry run arbol scrape fichas

# 4. Progreso en cualquier momento
poetry run arbol scrape status
```

### AHDV-GEAH (Álava): por rango de ID

Este portal usa la misma plataforma SIGA-AKIS pero **no tiene** un motor de
búsqueda que devuelva el 100% de los registros sólo con localidad + fecha (la
búsqueda simple y la avanzada exigen un apellido real de al menos 3
caracteres). En cambio, sí permite pedir cada ficha directamente por su ID
(`n_ficha_bautismos.php?id_bautismo=N`), igual que AHEB-BEHA. Por eso el
barrido es distinto: se descubre el mayor ID existente por sacramento
(búsqueda binaria) y se encola el rango `[1, max]` completo; los huecos de
numeración se descartan al procesar (quedan como `vacio`, no como error).

```bash
# 1. Descubre el máximo ID por sacramento y encola scrape_fichas [1, max]
poetry run arbol scrape rango --archivo ahdv_geah

# 2. Descarga y parsea cada ficha (cola compartida con AHEB-BEHA)
poetry run arbol scrape fichas

# 3. Progreso, desglosado por archivo
poetry run arbol scrape status
```

### AHDSS (Gipuzkoa): por rango de ID, pero global entre sacramentos

Portal ajeno a SIGA-AKIS (Yii/Arinka: `.../busque-partidas-sacramentales/ver.html?id=N&sacramento=b|m|d`).
Se recorre por rango de ID como AHDV-GEAH, pero con una diferencia
importante: el ID de registro es **global**, compartido entre los 3
sacramentos (no hay un espacio de IDs por sacramento). El portal señaliza
"este ID no es de ese sacramento" con un **404 real** (a diferencia de
AHDV-GEAH, que devuelve 200 con una página de error sin datos), así que se
descubre el máximo ID comprobando existencia en cualquiera de los 3
sacramentos (early-stop al primer hit) y se encola ese mismo rango
`[1, max]` **tres veces** —una por sacramento—.

Eso deja **3 filas por ID** en `scrape_fichas` (compatible con la unicidad
`(archivo, id_registro, sacramento)` y con la cola ya existente). El ×3 de
**filas** es auditoría: al resolver un ID queda `1 done + 2 vacio` (o
`3 vacio` si es un hueco real). El ×3 de **HTTP ya no**: el worker agrupa
por `(archivo, id_registro)`, prueba sacramentos con early-stop y corta
en cuanto sabe cuál es (~1-2 GET de media, a veces 1). No hace falta
borrar ni re-sembrar las filas PENDING ya encoladas.

AHDSS tampoco expone un código de fondo/parroquia estable en la ficha (a
diferencia de SIGA-AKIS): se sintetiza uno determinista a partir de
nombre+municipio para poder deduplicar parroquias (ver
`infrastructure/scraper/parser.py`).

```bash
# 1. Descubre el máximo ID global y encola scrape_fichas [1, max] x3 filas (b/m/d)
poetry run arbol scrape rango --archivo ahdss

# 2. Descarga y parsea por ID (no 3 GET por fila); cola compartida con los demás
poetry run arbol scrape fichas --archivo ahdss

# 3. Progreso, desglosado por archivo
poetry run arbol scrape status
```

Verificación de IDs ya cerrados (sólo conteos, **nunca DELETE**):

```sql
SELECT id_registro,
       count(*) FILTER (WHERE status = 'DONE')  AS n_done,
       count(*) FILTER (WHERE status = 'VACIO') AS n_vacio
FROM siga.scrape_fichas
WHERE archivo = 'AHDSS'
GROUP BY id_registro
HAVING count(*) FILTER (WHERE status IN ('PENDING', 'ERROR')) = 0
   AND NOT (
       (count(*) FILTER (WHERE status = 'DONE') = 1
        AND count(*) FILTER (WHERE status = 'VACIO') = 2)
       OR count(*) FILTER (WHERE status = 'VACIO') = 3
   )
LIMIT 50;
```

Por defecto, `scrape listados` y `scrape fichas` procesan en lotes de forma
continua hasta agotar el trabajo pendiente. Para procesar sólo un lote (por
ejemplo, para hacer una prueba corta) usa `--no-continuo`; `scrape fichas`
también acepta `--archivo` para limitarse a uno de los tres:

```bash
poetry run arbol scrape listados --lote 5 --no-continuo
poetry run arbol scrape fichas --lote 50 --no-continuo
poetry run arbol scrape fichas --archivo ahdv_geah
```

### Reanudación

Todo el proceso es reanudable sin pérdida de trabajo:

- El HTML de cada petición se cachea en disco (`SCRAPER_RAW_DIR`); si el
  fichero ya existe, no se vuelve a pedir por red.
- Cada job de listado (`scrape_jobs`) y cada ficha (`scrape_fichas`) tiene un
  `status` (`pending` / `running` / `done` / `error`) y un contador de
  `retries`. Si el proceso se corta (o falla una petición concreta), basta con
  volver a ejecutar `scrape listados` / `scrape fichas`: sólo se reintentan los
  elementos `pending` o `error` con menos de 5 reintentos.
- Los listados guardan además `ultima_pagina`, así que la paginación continúa
  donde se quedó en vez de reempezar desde la página 1.

### Multi-archivo: `archivo` como parte de la clave

Cada portal numera sus propios registros de forma independiente (p.ej.
`id_bautismo=1` existe en AHEB-BEHA, en AHDV-GEAH y en AHDSS, y son personas
distintas). Por eso `archivo` forma parte de la clave primaria de
`bautismos`/`matrimonios`/`defunciones` y de la restricción única de
`scrape_fichas`. Si vienes de una versión anterior a este soporte
multi-archivo, aplica la migración correspondiente con
`poetry run alembic upgrade head` **con el scraper parado**: cambia claves de
tablas que un proceso en marcha podría estar escribiendo con la definición
antigua en memoria. Tras aplicarla, vuelve a lanzar `scrape fichas`/`scrape
listados` con normalidad: son reanudables, no se pierde nada de lo ya
descargado.

---

## Consultas SQL de ejemplo

Puedes usar pgAdmin, cualquier cliente Postgres, o el atajo de la CLI:

```bash
poetry run arbol query "SELECT archivo, count(*) FROM siga.bautismos GROUP BY archivo"
```

Búsqueda por apellido (opcionalmente acotada a un archivo, ya que `personas`
es común a los tres):

```sql
SELECT b.archivo, b.id_bautismo, b.fecha, p.nombre, p.apellido1, p.apellido2, pq.nombre AS parroquia
FROM siga.bautismos b
JOIN siga.personas p ON p.id = b.id_persona
LEFT JOIN siga.parroquias pq ON pq.id = b.id_parroquia
WHERE p.apellido1 ILIKE 'Ugarte%'
ORDER BY b.fecha;
```

Cruce padre-hijo entre bautismos (encontrar el bautismo de un padre a partir
del bautismo de su hijo):

```sql
SELECT hijo.id_bautismo AS bautismo_hijo, hijo.fecha AS fecha_hijo,
       padre_bautismo.id_bautismo AS bautismo_padre, padre_bautismo.fecha AS fecha_padre
FROM siga.bautismos hijo
JOIN siga.personas padre ON padre.id = hijo.id_padre
JOIN siga.bautismos padre_bautismo ON padre_bautismo.id_persona = padre.id
WHERE hijo.archivo = 'AHEB_BEHA' AND hijo.id_bautismo = 198970;
```

Progreso de la descarga por archivo y sacramento:

```sql
SELECT archivo, sacramento, status, count(*)
FROM siga.scrape_fichas
GROUP BY archivo, sacramento, status
ORDER BY archivo, sacramento, status;
```

---

## Comandos útiles

```bash
poetry run arbol --help          # ver todos los comandos disponibles
poetry run pytest                # tests (parser + integración respx del cliente HTTP)
poetry run ruff check .          # lint
poetry run pre-commit run --all-files
```
