"""Tests de integración del cliente HTTP usando respx (sin red real)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.infrastructure.db.models import Archivo
from arbol_genealogico.infrastructure.scraper.client import ScraperClient, build_client

BASE_URL = "https://internet.aheb-beha.org"


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    return AppSettings(
        scraper_base_url=BASE_URL,
        scraper_min_delay_s=0.0,
        scraper_max_delay_s=0.0,
        scraper_max_retries=3,
        scraper_raw_dir=tmp_path,
        **overrides,
    )


@pytest.mark.asyncio
@respx.mock
async def test_get_decodifica_iso_8859_1(tmp_path: Path) -> None:
    # "ó" en iso-8859-1 es el byte 0xF3.
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/pagina.php").mock(return_value=httpx.Response(200, content="Bautizó".encode("iso-8859-1")))

    async with ScraperClient(_settings(tmp_path)) as client:
        response = await client.get("/pagina.php")
        assert response.text == "Bautizó"


@pytest.mark.asyncio
@respx.mock
async def test_get_cached_no_repite_peticion(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    ruta = respx.get(f"{BASE_URL}/ficha.php").mock(
        return_value=httpx.Response(200, content="contenido".encode("iso-8859-1"))
    )

    cache_file = tmp_path / "ficha" / "1.html"
    async with ScraperClient(_settings(tmp_path)) as client:
        primero = await client.get_cached("/ficha.php", cache_file)
        segundo = await client.get_cached("/ficha.php", cache_file)

    assert primero == segundo == "contenido"
    assert ruta.call_count == 1
    assert cache_file.exists()


@pytest.mark.asyncio
@respx.mock
async def test_post_cached_respeta_cache_en_disco(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    ruta = respx.post(f"{BASE_URL}/listado.php").mock(
        return_value=httpx.Response(200, content="listado".encode("iso-8859-1"))
    )

    cache_file = tmp_path / "listado" / "p1.html"
    async with ScraperClient(_settings(tmp_path)) as client:
        await client.post_cached("/listado.php", cache_file, data={"a": "1"})
        await client.post_cached("/listado.php", cache_file, data={"a": "1"})

    assert ruta.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_respeta_robots_txt(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /privado/\n")
    )

    async with ScraperClient(_settings(tmp_path)) as client:
        with pytest.raises(PermissionError):
            await client.get("/privado/secreto.php")


@pytest.mark.asyncio
@respx.mock
async def test_build_client_usa_dominio_y_encoding_de_ahdv_geah(tmp_path: Path) -> None:
    """AHDV-GEAH usa otro dominio y, a diferencia de AHEB-BEHA, sirve UTF-8
    de verdad (no iso-8859-1): el cliente construido para ese archivo debe
    reflejarlo, y cachear en una subcarpeta propia para no pisar AHEB-BEHA."""
    otro_dominio = "https://internet.ahdv-geah.org"
    respx.get(f"{otro_dominio}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{otro_dominio}/pagina.php").mock(return_value=httpx.Response(200, content="Muñoz".encode("utf-8")))

    settings = _settings(tmp_path, scraper_base_url_ahdv_geah=otro_dominio)
    async with build_client(settings, Archivo.AHDV_GEAH) as client:
        assert client.raw_dir == tmp_path / "ahdv_geah"
        response = await client.get("/pagina.php")
        assert response.text == "Muñoz"


@pytest.mark.asyncio
@respx.mock
async def test_build_client_usa_dominio_y_encoding_de_ahdss(tmp_path: Path) -> None:
    """AHDSS (Gipuzkoa) es una plataforma distinta de SIGA-AKIS, con su
    propio dominio y cache propia (no debe pisar AHEB-BEHA/AHDV-GEAH)."""
    otro_dominio = "https://artxiboa.mendezmende.org"
    respx.get(f"{otro_dominio}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{otro_dominio}/pagina.php").mock(return_value=httpx.Response(200, content="Oñati".encode("utf-8")))

    settings = _settings(tmp_path, scraper_base_url_ahdss=otro_dominio)
    async with build_client(settings, Archivo.AHDSS) as client:
        assert client.raw_dir == tmp_path / "ahdss"
        response = await client.get("/pagina.php")
        assert response.text == "Oñati"


@pytest.mark.asyncio
@respx.mock
async def test_get_con_allow_statuses_no_lanza_para_404(tmp_path: Path) -> None:
    """AHDSS señaliza "ID no existe para este sacramento" con un 404 real
    (a diferencia de AHDV-GEAH, que devuelve 200 con una página de error):
    ``allow_statuses`` permite tratarlo como respuesta válida en vez de
    lanzar ``httpx.HTTPStatusError``."""
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE_URL}/no-existe.php").mock(return_value=httpx.Response(404, content=b"Not Found"))

    async with ScraperClient(_settings(tmp_path)) as client:
        response = await client.get("/no-existe.php", allow_statuses=frozenset({404}))

    assert response.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_reintenta_ante_500_y_luego_tiene_exito(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    ruta = respx.get(f"{BASE_URL}/inestable.php")
    ruta.side_effect = [
        httpx.Response(500),
        httpx.Response(200, content="ok".encode("iso-8859-1")),
    ]

    async with ScraperClient(_settings(tmp_path)) as client:
        response = await client.get("/inestable.php")

    assert response.text == "ok"
    assert ruta.call_count == 2
