from __future__ import annotations

from pathlib import Path

from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.client import ScraperClient

# La "búsqueda simple" (n_indexacion.php) exige nombre/apellido y, para
# matrimonio/difunto, además exige >=3 caracteres reales (no admite un "%" o
# comodín corto en solitario: probado empíricamente, ver notas de diseño).
# La "búsqueda especial" (n_indexacion_especial.php) sólo exige localidad +
# rango de fechas, sin ningún dato de persona, y funciona igual para los 3
# sacramentos: es la que se usa para el barrido exhaustivo.
ESPECIAL_PATH = "/paginas/indexacion/n_indexacion_especial.php"

_FICHA_PATH = {
    Sacramento.BAUTISMO: "/paginas/indexacion/n_ficha_bautismos.php",
    Sacramento.MATRIMONIO: "/paginas/indexacion/n_ficha_matrimonios.php",
    Sacramento.DIFUNTO: "/paginas/indexacion/n_ficha_difuntos.php",
}

_FICHA_PARAM = {
    Sacramento.BAUTISMO: "id_bautismo",
    Sacramento.MATRIMONIO: "id_matrimonio",
    Sacramento.DIFUNTO: "id_difunto",
}


def build_listado_payload(
    sacramento: Sacramento,
    id_localidad: int,
    anio_ini: int,
    anio_fin: int,
) -> dict[str, str]:
    return {
        "accion": "buscar",
        "auxiliar": "",
        "sacramento": sacramento.value,
        "id_localidad": str(id_localidad),
        "fecha_form_ini_esp": str(anio_ini),
        "fecha_form_fin_esp": str(anio_fin),
    }


def listado_cache_path(
    raw_dir: Path,
    sacramento: Sacramento,
    id_localidad: int,
    anio_ini: int,
    anio_fin: int,
    resultpage: int,
) -> Path:
    return raw_dir / "listado" / sacramento.value / str(id_localidad) / f"{anio_ini}-{anio_fin}" / f"p{resultpage}.html"


def ficha_path(sacramento: Sacramento) -> str:
    return _FICHA_PATH[sacramento]


def ficha_param_name(sacramento: Sacramento) -> str:
    return _FICHA_PARAM[sacramento]


def ficha_cache_path(raw_dir: Path, sacramento: Sacramento, id_registro: int) -> Path:
    return raw_dir / "ficha" / sacramento.value / f"{id_registro}.html"


async def fetch_listado_html(
    client: ScraperClient,
    sacramento: Sacramento,
    id_localidad: int,
    anio_ini: int,
    anio_fin: int,
    resultpage: int = 1,
) -> str:
    payload = build_listado_payload(sacramento, id_localidad, anio_ini, anio_fin)
    # El paginado sólo funciona si "resultpage" viaja en el query string (tal
    # cual generan los enlaces "2", "3"... del propio listado); en el body del
    # POST el servidor lo ignora y siempre devuelve la página 1.
    params = {"resultpage": str(resultpage)} if resultpage > 1 else None
    cache_file = listado_cache_path(client.raw_dir, sacramento, id_localidad, anio_ini, anio_fin, resultpage)
    return await client.post_cached(ESPECIAL_PATH, cache_file, data=payload, params=params)


async def fetch_ficha_html(client: ScraperClient, sacramento: Sacramento, id_registro: int) -> str:
    path = ficha_path(sacramento)
    param = ficha_param_name(sacramento)
    cache_file = ficha_cache_path(client.raw_dir, sacramento, id_registro)
    return await client.get_cached(path, cache_file, params={param: str(id_registro)})


# --- AHDSS (Gipuzkoa, portal de Méndez Mende): plataforma Yii/Arinka, ajena
# a SIGA-AKIS. El ID de registro es GLOBAL (compartido entre los 3
# sacramentos) y la ficha se pide con "id" + "sacramento" (b/m/d) en vez de
# un parámetro "id_<sacramento>" propio por tipo. Cuando el ID no
# corresponde al sacramento pedido, el portal devuelve un 404 real (no una
# página de error con contenido, como AHDV-GEAH): ver ``allow_statuses`` en
# ``ScraperClient``.
AHDSS_FICHA_PATH = "/es/busque-partidas-sacramentales/ver.html"

_AHDSS_SACRAMENTO_PARAM = {
    Sacramento.BAUTISMO: "b",
    Sacramento.MATRIMONIO: "m",
    Sacramento.DIFUNTO: "d",
}


def ahdss_sacramento_param(sacramento: Sacramento) -> str:
    return _AHDSS_SACRAMENTO_PARAM[sacramento]


def ficha_cache_path_ahdss(raw_dir: Path, sacramento: Sacramento, id_registro: int) -> Path:
    return raw_dir / "ficha" / sacramento.value / f"{id_registro}.html"


async def fetch_ficha_html_ahdss(client: ScraperClient, sacramento: Sacramento, id_registro: int) -> str:
    params = {"id": str(id_registro), "sacramento": _AHDSS_SACRAMENTO_PARAM[sacramento]}
    cache_file = ficha_cache_path_ahdss(client.raw_dir, sacramento, id_registro)
    return await client.get_cached(AHDSS_FICHA_PATH, cache_file, params=params, allow_statuses=frozenset({404}))
