# Árbol Genealógico

Aplicación para gestionar y visualizar un árbol genealógico.

---

## Requisitos

- **Python 3.12+**
- **Poetry** ([instalación](https://python-poetry.org/docs/#installation))

---

## Instalación (desarrollo local)

```bash
git clone https://github.com/LucasMezo2001/Arbol-Genealogico.git
cd Arbol-Genealogico
poetry install

# Configurar variables de entorno
cp .env.example .env
# Editar .env según sea necesario

# Instalar pre-commit hooks
poetry run pre-commit install
```

---

## Estructura

```
src/arbol_genealogico/
  config/           # settings, paths
  domain/           # entidades y reglas de dominio
  entrypoints/      # CLI y puntos de entrada
  features/         # casos de uso / features
  front/            # interfaz de usuario
  infrastructure/   # logger, DB, integraciones
  utils/            # utilidades
config/             # YAML de configuración
docs/               # documentación
tests/              # unit + integration
logs/               # logs locales (ignorados por git)
```

---

## Variables de entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ENV` | Entorno activo: `dev`, `preprod` o `prod` | `dev` |
| `CONFIG_PATH` | Ruta al YAML de configuración | `config/config.yaml` |
| `DATABASE_URL` | URL de BD (opcional) | `postgresql://...` |

Copia `.env.example` a `.env`. El fichero `.env` **no** se versiona.

---

## Comandos útiles

```bash
poetry run arbol          # CLI (placeholder)
poetry run pytest         # tests
poetry run pre-commit run --all-files
```
