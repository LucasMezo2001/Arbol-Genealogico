"""Tests unitarios de los helpers de endpoints (payloads y rutas de cache)."""

from __future__ import annotations

from pathlib import Path

from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.endpoints import (
    build_listado_payload,
    ficha_cache_path,
    ficha_param_name,
    ficha_path,
    listado_cache_path,
)


def test_build_listado_payload_no_incluye_apellido() -> None:
    payload = build_listado_payload(Sacramento.MATRIMONIO, id_localidad=101, anio_ini=1850, anio_fin=1860)

    assert payload["sacramento"] == "matrimonio"
    assert payload["id_localidad"] == "101"
    assert payload["fecha_form_ini_esp"] == "1850"
    assert payload["fecha_form_fin_esp"] == "1860"
    assert "apellido1" not in payload
    assert "apellido_filtro" not in payload


def test_listado_cache_path_incluye_sacramento_localidad_rango_y_pagina() -> None:
    ruta = listado_cache_path(Path("/data"), Sacramento.BAUTISMO, 29, 1850, 1852, 2)

    assert ruta == Path("/data/listado/bautismo/29/1850-1852/p2.html")


def test_ficha_cache_path_incluye_sacramento_e_id() -> None:
    ruta = ficha_cache_path(Path("/data"), Sacramento.DIFUNTO, 73525)

    assert ruta == Path("/data/ficha/difunto/73525.html")


def test_ficha_path_y_param_por_sacramento() -> None:
    assert ficha_path(Sacramento.BAUTISMO).endswith("n_ficha_bautismos.php")
    assert ficha_path(Sacramento.MATRIMONIO).endswith("n_ficha_matrimonios.php")
    assert ficha_path(Sacramento.DIFUNTO).endswith("n_ficha_difuntos.php")

    assert ficha_param_name(Sacramento.BAUTISMO) == "id_bautismo"
    assert ficha_param_name(Sacramento.MATRIMONIO) == "id_matrimonio"
    assert ficha_param_name(Sacramento.DIFUNTO) == "id_difunto"
