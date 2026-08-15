"""Tests de descubrimiento de rango de IDs (AHDV-GEAH no expone un contador
público de registros: hay que sondear por búsqueda binaria, ver rango.py)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.features.scraping.rango import descubrir_max_id
from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.client import ScraperClient

BASE_URL = "https://internet.ahdv-geah.org"
FICHA_OK = "<html><body><table><tr><td><span class='negritaform'>ID:</span></td><td>1</td></tr></table></body></html>"


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        scraper_base_url=BASE_URL,
        scraper_min_delay_s=0.0,
        scraper_max_delay_s=0.0,
        scraper_max_retries=3,
        scraper_raw_dir=tmp_path,
    )


def _mock_max_id(maximo: int) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        id_ = int(request.url.params["id_bautismo"])
        if id_ <= maximo:
            return httpx.Response(200, content=FICHA_OK.encode("utf-8"))
        return httpx.Response(200, content=b"Fatal error")

    respx.get(f"{BASE_URL}/paginas/indexacion/n_ficha_bautismos.php").mock(side_effect=responder)


@pytest.mark.asyncio
@respx.mock
async def test_descubrir_max_id_encuentra_el_limite_exacto(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    _mock_max_id(37)

    async with ScraperClient(_settings(tmp_path), response_encoding="utf-8") as client:
        maximo = await descubrir_max_id(client, Sacramento.BAUTISMO)

    assert maximo == 37


@pytest.mark.asyncio
@respx.mock
async def test_descubrir_max_id_sin_registros_devuelve_cero(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    _mock_max_id(0)

    async with ScraperClient(_settings(tmp_path), response_encoding="utf-8") as client:
        maximo = await descubrir_max_id(client, Sacramento.BAUTISMO)

    assert maximo == 0


@pytest.mark.asyncio
@respx.mock
async def test_descubrir_max_id_con_un_solo_registro(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/robots.txt").mock(return_value=httpx.Response(404))
    _mock_max_id(1)

    async with ScraperClient(_settings(tmp_path), response_encoding="utf-8") as client:
        maximo = await descubrir_max_id(client, Sacramento.BAUTISMO)

    assert maximo == 1
