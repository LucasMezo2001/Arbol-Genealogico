"""Tests de integración (respx) de las funciones fetch_* de endpoints.py.

Cubren dos puntos que fallaron durante el desarrollo y no deben regresar:
- ``resultpage`` debe viajar como query param (el POST body lo ignora).
- Las respuestas cacheadas en disco deben ser idénticas a las de red (sin
  problemas de encoding/CRLF).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.client import ScraperClient
from arbol_genealogico.infrastructure.scraper.endpoints import fetch_ficha_html, fetch_listado_html

BASE_URL = "https://internet.aheb-beha.org"


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        scraper_base_url=BASE_URL,
        scraper_min_delay_s=0.0,
        scraper_max_delay_s=0.0,
        scraper_max_retries=3,
        scraper_raw_dir=tmp_path,
    )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_listado_html_envia_resultpage_como_query_param(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    ruta = respx.post(
        f"{BASE_URL}/paginas/indexacion/n_indexacion_especial.php",
        params={"resultpage": "2"},
    ).mock(return_value=httpx.Response(200, content="pagina 2".encode("iso-8859-1")))

    async with ScraperClient(_settings(tmp_path)) as client:
        html = await fetch_listado_html(client, Sacramento.BAUTISMO, 29, 1850, 1852, resultpage=2)

    assert html == "pagina 2"
    assert ruta.call_count == 1
    peticion = ruta.calls.last.request
    assert peticion.url.params["resultpage"] == "2"
    # El id_localidad y las fechas sí viajan en el cuerpo del POST.
    body = peticion.content.decode("utf-8")
    assert "id_localidad=29" in body
    assert "resultpage" not in body


@pytest.mark.asyncio
@respx.mock
async def test_fetch_ficha_html_usa_cache_tras_primera_descarga(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    ruta = respx.get(f"{BASE_URL}/paginas/indexacion/n_ficha_bautismos.php").mock(
        return_value=httpx.Response(200, content="<html>ficha</html>".encode("iso-8859-1"))
    )

    async with ScraperClient(_settings(tmp_path)) as client:
        primero = await fetch_ficha_html(client, Sacramento.BAUTISMO, 198970)
        segundo = await fetch_ficha_html(client, Sacramento.BAUTISMO, 198970)

    assert primero == segundo == "<html>ficha</html>"
    assert ruta.call_count == 1
    assert (tmp_path / "ficha" / "bautismo" / "198970.html").exists()
