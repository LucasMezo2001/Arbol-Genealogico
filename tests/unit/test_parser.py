"""Tests del parser BeautifulSoup usando los HTML reales capturados como fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.parser import parse_ficha, parse_listado

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> str:
    with open(FIXTURES_DIR / name, encoding="utf-8", newline="") as f:
        return f.read()


class TestParseListado:
    def test_pagina_unica_sin_paginacion(self) -> None:
        html = _load("listado_matrimonio_ubidea_1850_1860_pag1.html")
        resultado = parse_listado(html, Sacramento.MATRIMONIO)

        assert resultado.total_registros == 46
        assert resultado.sin_resultados is False
        assert len(resultado.ids) == 46
        assert len(set(resultado.ids)) == 46

    def test_bautismo_con_paginacion_pagina_1(self) -> None:
        html = _load("listado_bautismo_bilbao_1850_1852_pag1.html")
        resultado = parse_listado(html, Sacramento.BAUTISMO)

        assert resultado.total_registros == 2689
        assert resultado.sin_resultados is False
        assert len(resultado.ids) == 100

    def test_bautismo_con_paginacion_pagina_2_distinta_de_pagina_1(self) -> None:
        html_p1 = _load("listado_bautismo_bilbao_1850_1852_pag1.html")
        html_p2 = _load("listado_bautismo_bilbao_1850_1852_pag2.html")
        pag1 = parse_listado(html_p1, Sacramento.BAUTISMO)
        pag2 = parse_listado(html_p2, Sacramento.BAUTISMO)

        assert pag2.total_registros == 2689
        assert len(pag2.ids) == 100
        # Las dos páginas deben traer IDs distintos (si no, la paginación no funcionaría).
        assert set(pag1.ids).isdisjoint(pag2.ids)

    def test_difunto_una_sola_pagina_de_100(self) -> None:
        html = _load("listado_difunto_ubidea_1850_1860_pag1.html")
        resultado = parse_listado(html, Sacramento.DIFUNTO)

        assert resultado.total_registros == 160
        assert len(resultado.ids) == 100

    def test_bautismo_ubidea_registros_bajo_100(self) -> None:
        html = _load("listado_bautismo_ubidea_1850_1860_pag1.html")
        resultado = parse_listado(html, Sacramento.BAUTISMO)

        assert resultado.total_registros == 254
        assert len(resultado.ids) == 100

    def test_sin_resultados(self) -> None:
        html = _load("listado_matrimonio_sin_resultados.html")
        resultado = parse_listado(html, Sacramento.MATRIMONIO)

        assert resultado.total_registros is None
        assert resultado.sin_resultados is True
        assert resultado.ids == []


class TestParseFicha:
    def test_ficha_bautismo(self) -> None:
        html = _load("ficha_bautismo_198970.html")
        ficha = parse_ficha(html, 198970)

        assert ficha.id_registro == 198970
        assert ficha.fecha == "1850-05-18"
        assert ficha.persona is not None
        assert ficha.persona.nombre == "Pascuala Ysidora"
        assert ficha.persona.apellido1 == "Gonzalez"
        assert ficha.persona.apellido2 == "Abaroa"
        assert ficha.padre is not None
        assert ficha.padre.nombre == "Domingo"
        assert ficha.padre.apellido1 == "Gonzalez"
        assert ficha.madre is not None
        assert ficha.madre.apellido1 == "Abaroa"
        assert ficha.esposo is None
        assert ficha.esposa is None
        assert ficha.conyuge is None
        assert ficha.diocesis == "Bilbao"
        assert ficha.territorio_historico == "BIZKAIA"
        assert ficha.localidad_texto == "Bilbao-Abando - Albia"
        assert ficha.parroquia_codigo == "31050"
        assert ficha.parroquia_nombre == "San Vicente Mártir"
        assert ficha.fondo_codigo == "01.02.01.067"
        assert ficha.fondo_descripcion == (
            "Fondos Parroquiales / Archivos Parroquiales / Bilbao - Abando - Albia / San Vicente Mártir"
        )

    def test_ficha_matrimonio(self) -> None:
        html = _load("ficha_matrimonio_39957.html")
        ficha = parse_ficha(html, 39957)

        assert ficha.fecha == "1678-10-03"
        assert ficha.esposo is not None
        assert ficha.esposo.nombre == "Pedro"
        assert ficha.esposo.apellido1 == "Gonçalez de Vega"
        assert ficha.esposo.apellido2 == "Merino"
        assert ficha.esposa is not None
        assert ficha.esposa.nombre == "Mari Cruz"
        assert ficha.esposa.apellido1 == "Oqueluri"
        assert ficha.persona is None
        assert ficha.territorio_historico == "BIZKAIA"
        assert ficha.localidad_texto == "Bilbao-Casco Viejo"
        assert ficha.parroquia_codigo == "76080"
        assert ficha.parroquia_nombre == "Señor Santiago"
        assert ficha.fondo_codigo == "01.02.01.080"

    def test_ficha_difunto(self) -> None:
        html = _load("ficha_difunto_73525.html")
        ficha = parse_ficha(html, 73525)

        assert ficha.fecha == "1881-05-23"
        assert ficha.persona is not None
        assert ficha.persona.nombre == "Josefa"
        assert ficha.persona.apellido1 == "Gondin"
        assert ficha.persona.apellido2 == "Montero"
        assert ficha.conyuge is None
        assert ficha.padre is None
        assert ficha.madre is None
        assert ficha.diocesis == "Bilbao"
        assert ficha.localidad_texto == "Bilbao-Casco Viejo"
        assert ficha.parroquia_codigo == "76060"
        assert ficha.parroquia_nombre == "San Nicolás de Bari"
        assert ficha.fondo_codigo == "01.02.01.079"


@pytest.mark.parametrize(
    "fixture",
    [
        "ficha_bautismo_198970.html",
        "ficha_matrimonio_39957.html",
        "ficha_difunto_73525.html",
    ],
)
def test_ficha_nunca_lanza_para_htmls_reales(fixture: str) -> None:
    html = _load(fixture)
    ficha = parse_ficha(html, 1)
    assert ficha.id_registro == 1
