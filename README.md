# Árbol Genealógico

Aplicación para gestionar y visualizar un árbol genealógico, con un scraper propio
que descarga los registros sacramentales (bautismos, matrimonios y defunciones)
publicados por el archivo histórico [AHEB-BEHA](https://internet.aheb-beha.org/) y
los persiste en un Postgres local dockerizado para poder consultarlos offline.

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
    scraping/                    # enumerar_jobs, procesar_listado, procesar_ficha
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
| `SCRAPER_BASE_URL` | Dominio del portal SIGA-AKIS | `https://internet.aheb-beha.org` |
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

El scraper recorre `sacramento × localidad × año` (1501-1900) usando la
"búsqueda especial" del portal (sólo localidad + rango de fechas, sin depender
de apellidos), pagina los listados y descarga la ficha completa de cada
registro. Es respetuoso por diseño: 1 sola petición en vuelo, espera aleatoria
entre peticiones, backoff exponencial ante errores, respeta `robots.txt` y usa
un User-Agent identificable.

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

Por defecto, `scrape listados` y `scrape fichas` procesan en lotes de forma
continua hasta agotar el trabajo pendiente. Para procesar sólo un lote (por
ejemplo, para hacer una prueba corta) usa `--no-continuo`:

```bash
poetry run arbol scrape listados --lote 5 --no-continuo
poetry run arbol scrape fichas --lote 50 --no-continuo
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

---

## Consultas SQL de ejemplo

Puedes usar pgAdmin, cualquier cliente Postgres, o el atajo de la CLI:

```bash
poetry run arbol query "SELECT count(*) FROM siga.bautismos"
```

Búsqueda por apellido:

```sql
SELECT b.id_bautismo, b.fecha, p.nombre, p.apellido1, p.apellido2, pq.nombre AS parroquia
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
WHERE hijo.id_bautismo = 198970;
```

Progreso de la descarga por sacramento:

```sql
SELECT sacramento, status, count(*)
FROM siga.scrape_jobs
GROUP BY sacramento, status
ORDER BY sacramento, status;
```

---

## Comandos útiles

```bash
poetry run arbol --help          # ver todos los comandos disponibles
poetry run pytest                # tests (parser + integración respx del cliente HTTP)
poetry run ruff check .          # lint
poetry run pre-commit run --all-files
```
