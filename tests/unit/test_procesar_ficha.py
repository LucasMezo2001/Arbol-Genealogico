"""Tests del worker de fichas: agrupado AHDSS (ID global) vs 1 GET por fila
en AHEB-BEHA / AHDV-GEAH. El cliente HTTP se mockea; no hay red."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.selectable import Select

from arbol_genealogico.features.scraping.procesar_ficha import procesar_fichas, procesar_item
from arbol_genealogico.infrastructure.db.models import Archivo, FichaStatus, Sacramento, ScrapeFicha

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
TABLAS_SACRAMENTALES = {"bautismos", "matrimonios", "defunciones"}


def _load(name: str) -> str:
    with open(FIXTURES_DIR / name, encoding="utf-8", newline="") as f:
        return f.read()


HTML_BAUTISMO_AHDSS = _load("ficha_bautismo_gipuzkoa_1.html")
HTML_MATRIMONIO_AHDSS = _load("ficha_matrimonio_gipuzkoa_1.html")
HTML_DIFUNTO_AHDSS = _load("ficha_difunto_gipuzkoa_1.html")
HTML_VACIO_AHDSS = _load("ficha_gipuzkoa_vacio.html")
HTML_BAUTISMO_SIGA = _load("ficha_bautismo_araba_1.html")


class FakeResult:
    def __init__(self, rows: list[ScrapeFicha] | None = None, scalar: int = 1) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[ScrapeFicha]:
        return self._rows

    def scalar_one(self) -> int:
        return self._scalar


class FakeSession:
    """Session mínima: select sobre scrape_fichas + captura de INSERT."""

    def __init__(self, fichas: list[ScrapeFicha]) -> None:
        self.fichas = fichas
        self.inserts: list[str] = []
        self.commits = 0

    async def execute(self, stmt: object) -> FakeResult:
        if isinstance(stmt, Select):
            return FakeResult(self._aplicar_select(stmt))
        if isinstance(stmt, Insert):
            self.inserts.append(stmt.table.name)
            return FakeResult(scalar=1)
        return FakeResult(scalar=1)

    async def commit(self) -> None:
        self.commits += 1

    @staticmethod
    def _como_enum(tipo: type, value: object) -> object:
        if isinstance(value, tipo):
            return value
        if isinstance(value, str):
            try:
                return tipo[value]
            except KeyError:
                return tipo(value)
        return value

    def _aplicar_select(self, stmt: Select) -> list[ScrapeFicha]:
        rows = list(self.fichas)
        params = dict(stmt.compile().params)
        statuses: list[object] = []
        for key, value in params.items():
            if "status" not in key:
                continue
            valores = value if isinstance(value, (list, tuple, set)) else [value]
            statuses.extend(self._como_enum(FichaStatus, v) for v in valores)
        if statuses:
            rows = [r for r in rows if r.status in statuses]
        for key, value in params.items():
            if "id_registro" in key:
                rows = [r for r in rows if r.id_registro == value]
            elif "archivo" in key:
                archivo = self._como_enum(Archivo, value)
                rows = [r for r in rows if r.archivo == archivo]
            elif "retries" in key:
                rows = [r for r in rows if r.retries < value]
        rows.sort(key=lambda r: r.id or 0)
        clause = getattr(stmt, "_limit_clause", None)
        if clause is not None:
            limite = getattr(clause, "value", clause)
            try:
                rows = rows[: int(limite)]
            except (TypeError, ValueError):
                pass
        return rows


def _ficha(
    pk: int,
    archivo: Archivo,
    id_registro: int,
    sacramento: Sacramento,
    *,
    status: FichaStatus = FichaStatus.PENDING,
    retries: int = 0,
) -> ScrapeFicha:
    return ScrapeFicha(
        id=pk,
        archivo=archivo,
        id_registro=id_registro,
        sacramento=sacramento,
        id_localidad=None,
        status=status,
        retries=retries,
        error=None,
    )


def _hermanas_ahdss(
    id_registro: int,
    *,
    pk0: int = 1,
    statuses: tuple[FichaStatus, FichaStatus, FichaStatus] | None = None,
) -> list[ScrapeFicha]:
    statuses = statuses or (FichaStatus.PENDING, FichaStatus.PENDING, FichaStatus.PENDING)
    return [
        _ficha(pk0, Archivo.AHDSS, id_registro, Sacramento.BAUTISMO, status=statuses[0]),
        _ficha(pk0 + 1, Archivo.AHDSS, id_registro, Sacramento.MATRIMONIO, status=statuses[1]),
        _ficha(pk0 + 2, Archivo.AHDSS, id_registro, Sacramento.DIFUNTO, status=statuses[2]),
    ]


def _cliente() -> SimpleNamespace:
    return SimpleNamespace(sha256=lambda html: "a" * 64)


def _html_por_sacramento(real: Sacramento | None) -> dict[Sacramento, str]:
    html_ok = {
        Sacramento.BAUTISMO: HTML_BAUTISMO_AHDSS,
        Sacramento.MATRIMONIO: HTML_MATRIMONIO_AHDSS,
        Sacramento.DIFUNTO: HTML_DIFUNTO_AHDSS,
    }
    return {s: html_ok[s] if s == real else HTML_VACIO_AHDSS for s in Sacramento}


def _patch_fetch_ahdss(monkeypatch: pytest.MonkeyPatch, html_por_sac: dict[Sacramento, str], gets: list[Sacramento]):
    async def fake_fetch(_client: object, sacramento: Sacramento, _id: int) -> str:
        gets.append(sacramento)
        return html_por_sac[sacramento]

    monkeypatch.setattr(
        "arbol_genealogico.features.scraping.procesar_ficha.fetch_ficha_html_ahdss",
        fake_fetch,
    )


def _por_sacramento(filas: list[ScrapeFicha]) -> dict[Sacramento, ScrapeFicha]:
    return {f.sacramento: f for f in filas}


@pytest.mark.asyncio
async def test_ahdss_primer_sacramento_ok_un_get_hermanas_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    filas = _hermanas_ahdss(42)
    session = FakeSession(filas)
    gets: list[Sacramento] = []
    _patch_fetch_ahdss(monkeypatch, _html_por_sacramento(Sacramento.BAUTISMO), gets)

    await procesar_item(session, _cliente(), filas[0])

    por = _por_sacramento(filas)
    assert gets == [Sacramento.BAUTISMO]
    assert por[Sacramento.BAUTISMO].status == FichaStatus.DONE
    assert por[Sacramento.MATRIMONIO].status == FichaStatus.VACIO
    assert por[Sacramento.DIFUNTO].status == FichaStatus.VACIO
    assert por[Sacramento.MATRIMONIO].error is None
    assert por[Sacramento.DIFUNTO].error is None
    assert "bautismos" in session.inserts
    assert "matrimonios" not in session.inserts
    assert "defunciones" not in session.inserts


@pytest.mark.asyncio
async def test_ahdss_primer_404_segundo_ok_tercer_vacio_sin_get(monkeypatch: pytest.MonkeyPatch) -> None:
    filas = _hermanas_ahdss(42)
    session = FakeSession(filas)
    gets: list[Sacramento] = []
    _patch_fetch_ahdss(monkeypatch, _html_por_sacramento(Sacramento.MATRIMONIO), gets)

    await procesar_item(session, _cliente(), filas[0])

    por = _por_sacramento(filas)
    assert gets == [Sacramento.BAUTISMO, Sacramento.MATRIMONIO]
    assert por[Sacramento.BAUTISMO].status == FichaStatus.VACIO
    assert por[Sacramento.MATRIMONIO].status == FichaStatus.DONE
    assert por[Sacramento.DIFUNTO].status == FichaStatus.VACIO
    assert "matrimonios" in session.inserts
    assert "bautismos" not in session.inserts
    assert "defunciones" not in session.inserts


@pytest.mark.asyncio
async def test_ahdss_tres_404_todo_vacio_sin_registros(monkeypatch: pytest.MonkeyPatch) -> None:
    filas = _hermanas_ahdss(42)
    session = FakeSession(filas)
    gets: list[Sacramento] = []
    _patch_fetch_ahdss(monkeypatch, _html_por_sacramento(None), gets)

    await procesar_item(session, _cliente(), filas[0])

    assert gets == [Sacramento.BAUTISMO, Sacramento.MATRIMONIO, Sacramento.DIFUNTO]
    assert all(f.status == FichaStatus.VACIO for f in filas)
    assert TABLAS_SACRAMENTALES.isdisjoint(session.inserts)


@pytest.mark.asyncio
async def test_ahdss_ya_done_en_matrimonio_cero_get(monkeypatch: pytest.MonkeyPatch) -> None:
    filas = _hermanas_ahdss(
        42,
        statuses=(FichaStatus.PENDING, FichaStatus.DONE, FichaStatus.PENDING),
    )
    session = FakeSession(filas)
    gets: list[Sacramento] = []
    _patch_fetch_ahdss(monkeypatch, _html_por_sacramento(Sacramento.BAUTISMO), gets)

    await procesar_item(session, _cliente(), filas[0])

    por = _por_sacramento(filas)
    assert gets == []
    assert por[Sacramento.MATRIMONIO].status == FichaStatus.DONE
    assert por[Sacramento.BAUTISMO].status == FichaStatus.VACIO
    assert por[Sacramento.DIFUNTO].status == FichaStatus.VACIO
    assert TABLAS_SACRAMENTALES.isdisjoint(session.inserts)


@pytest.mark.asyncio
async def test_ahdss_excepcion_en_primer_get_no_cierra_hermanas(monkeypatch: pytest.MonkeyPatch) -> None:
    filas = _hermanas_ahdss(42)
    session = FakeSession(filas)
    gets: list[Sacramento] = []

    async def fake_fetch(_client: object, sacramento: Sacramento, _id: int) -> str:
        gets.append(sacramento)
        raise RuntimeError("red caida")

    monkeypatch.setattr(
        "arbol_genealogico.features.scraping.procesar_ficha.fetch_ficha_html_ahdss",
        fake_fetch,
    )

    await procesar_item(session, _cliente(), filas[0])

    por = _por_sacramento(filas)
    assert gets == [Sacramento.BAUTISMO]
    assert por[Sacramento.BAUTISMO].status == FichaStatus.ERROR
    assert por[Sacramento.BAUTISMO].retries == 1
    assert por[Sacramento.BAUTISMO].error is not None
    assert "red caida" in por[Sacramento.BAUTISMO].error
    assert por[Sacramento.MATRIMONIO].status == FichaStatus.PENDING
    assert por[Sacramento.DIFUNTO].status == FichaStatus.PENDING
    assert por[Sacramento.MATRIMONIO].retries == 0
    assert TABLAS_SACRAMENTALES.isdisjoint(session.inserts)


@pytest.mark.asyncio
@pytest.mark.parametrize("archivo", [Archivo.AHDV_GEAH, Archivo.AHEB_BEHA])
async def test_siga_no_agrupa_un_get_por_fila(monkeypatch: pytest.MonkeyPatch, archivo: Archivo) -> None:
    filas = [
        _ficha(1, archivo, 1, Sacramento.BAUTISMO),
        _ficha(2, archivo, 1, Sacramento.MATRIMONIO),
        _ficha(3, archivo, 1, Sacramento.DIFUNTO),
    ]
    session = FakeSession(filas)
    gets_siga: list[Sacramento] = []
    gets_ahdss: list[Sacramento] = []

    async def fake_siga(_client: object, sacramento: Sacramento, _id: int) -> str:
        gets_siga.append(sacramento)
        return HTML_BAUTISMO_SIGA

    async def fake_ahdss(_client: object, sacramento: Sacramento, _id: int) -> str:
        gets_ahdss.append(sacramento)
        return HTML_VACIO_AHDSS

    monkeypatch.setattr("arbol_genealogico.features.scraping.procesar_ficha.fetch_ficha_html", fake_siga)
    monkeypatch.setattr("arbol_genealogico.features.scraping.procesar_ficha.fetch_ficha_html_ahdss", fake_ahdss)

    await procesar_item(session, _cliente(), filas[0])

    por = _por_sacramento(filas)
    assert gets_ahdss == []
    assert gets_siga == [Sacramento.BAUTISMO]
    assert por[Sacramento.BAUTISMO].status == FichaStatus.DONE
    assert por[Sacramento.MATRIMONIO].status == FichaStatus.PENDING
    assert por[Sacramento.DIFUNTO].status == FichaStatus.PENDING
    assert "bautismos" in session.inserts


@pytest.mark.asyncio
async def test_procesar_fichas_ahdss_limite_cuenta_ids_distintos(monkeypatch: pytest.MonkeyPatch) -> None:
    """El lote AHDSS cuenta IDs, no filas: 6 PENDING (2 IDs × 3) y limite=1
    resuelve un ID (1 DONE + 2 VACIO) y deja el otro PENDING."""
    filas = _hermanas_ahdss(10, pk0=1) + _hermanas_ahdss(11, pk0=4)
    session = FakeSession(filas)
    gets: list[Sacramento] = []
    _patch_fetch_ahdss(monkeypatch, _html_por_sacramento(Sacramento.BAUTISMO), gets)

    procesados = await procesar_fichas(session, {Archivo.AHDSS: _cliente()}, limite=1, archivo=Archivo.AHDSS)

    assert procesados == 1
    assert gets == [Sacramento.BAUTISMO]
    id10 = [f for f in filas if f.id_registro == 10]
    id11 = [f for f in filas if f.id_registro == 11]
    por10 = _por_sacramento(id10)
    assert por10[Sacramento.BAUTISMO].status == FichaStatus.DONE
    assert por10[Sacramento.MATRIMONIO].status == FichaStatus.VACIO
    assert por10[Sacramento.DIFUNTO].status == FichaStatus.VACIO
    assert all(f.status == FichaStatus.PENDING for f in id11)
