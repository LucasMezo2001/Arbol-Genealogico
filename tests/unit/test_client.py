"""Tests de integración del cliente HTTP usando respx (sin red real)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.infrastructure.scraper.client import ScraperClient

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
