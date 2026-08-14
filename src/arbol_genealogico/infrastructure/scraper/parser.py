from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import Tag

from arbol_genealogico.infrastructure.db.models import Sacramento
from arbol_genealogico.infrastructure.scraper.endpoints import ficha_param_name

_TOTAL_RE = re.compile(r"Registros encontrados.*?<span[^>]*>\s*(\d+)\s*</span>", re.DOTALL)

# El portal usa guiones para marcar un dato ausente en la documentación
# original (ver tests/fixtures/ficha_difunto_*.html y la ayuda del portal).
_VACIO_RE = re.compile(r"^-+$")


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    value = " ".join(text.split()).strip()
    if not value or _VACIO_RE.match(value):
        return None
    return value


@dataclass(frozen=True)
class PersonaDatos:
    nombre: str | None = None
    apellido1: str | None = None
    apellido2: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.nombre, self.apellido1, self.apellido2))


def _parse_persona(raw: str | None) -> PersonaDatos | None:
    """Convierte "Nombre, Apellido1, Apellido2" (orden usado en las fichas)."""
    value = _clean(raw)
    if value is None:
        return None
    partes = [p.strip() for p in value.split(",")]
    partes = partes + [None] * (3 - len(partes))
    nombre, apellido1, apellido2 = (p if p else None for p in partes[:3])
    persona = PersonaDatos(nombre=_clean(nombre), apellido1=_clean(apellido1), apellido2=_clean(apellido2))
    return None if persona.is_empty else persona


@dataclass(frozen=True)
class ListadoPage:
    total_registros: int | None
    ids: list[int]
    sin_resultados: bool


def parse_listado(html: str, sacramento: Sacramento) -> ListadoPage:
    """Extrae el total de registros y los ids de ficha de una página de resultados."""
    match = _TOTAL_RE.search(html)
    total = int(match.group(1)) if match else None

    param = ficha_param_name(sacramento)
    soup = BeautifulSoup(html, "lxml")
    ids: list[int] = []
    seen: set[int] = set()
    for a in soup.select(f'a[href*="{param}="]'):
        href = a.get("href", "")
        m = re.search(rf"{param}=(\d+)", href)
        if not m:
            continue
        id_registro = int(m.group(1))
        if id_registro not in seen:
            seen.add(id_registro)
            ids.append(id_registro)

    sin_resultados = total is None and "no ha encontrado" in html.lower()
    return ListadoPage(total_registros=total, ids=ids, sin_resultados=sin_resultados)


@dataclass(frozen=True)
class FichaCompleta:
    id_registro: int
    sacramento_texto: str | None = None
    fecha: str | None = None
    persona: PersonaDatos | None = None
    padre: PersonaDatos | None = None
    madre: PersonaDatos | None = None
    esposo: PersonaDatos | None = None
    esposa: PersonaDatos | None = None
    conyuge: PersonaDatos | None = None
    edad: str | None = None
    comentarios: str | None = None
    diocesis: str | None = None
    territorio_historico: str | None = None
    localidad_texto: str | None = None
    parroquia_codigo: str | None = None
    parroquia_nombre: str | None = None
    fondo_codigo: str | None = None
    fondo_descripcion: str | None = None
    codigo_referencia: str | None = None
    signatura: str | None = None
    sig_antigua: str | None = None
    sig_microfilm: str | None = None
    sig_digital: str | None = None
    sig_digital_libro: str | None = None
    pagina: str | None = None
    fechas_libro: str | None = None


def _split_parroquia(raw: str | None) -> tuple[str | None, str | None]:
    value = _clean(raw)
    if value is None:
        return None, None
    m = re.match(r"^(\S+)\s+(.*)$", value)
    if m and re.match(r"^[\d.]+$", m.group(1)):
        return m.group(1), _clean(m.group(2))
    return None, value


def _split_fondo(raw: str | None) -> tuple[str | None, str | None]:
    value = _clean(raw)
    if value is None:
        return None, None
    partes = [p.strip() for p in value.split("/", 1)]
    if len(partes) == 2 and re.match(r"^[\d.]+$", partes[0]):
        return partes[0], _clean(partes[1])
    return None, value


def _extract_labelled_rows(soup: BeautifulSoup) -> dict[str, str]:
    datos: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        assert isinstance(tr, Tag)
        label_span = tr.find("span", class_="negritaform")
        if label_span is None:
            continue
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        label = _clean(label_span.get_text())
        if label is None:
            continue
        label = label.rstrip(":").strip()
        value = tds[1].get_text(" ", strip=True)
        datos[label] = value
    return datos


def parse_ficha(html: str, id_registro: int) -> FichaCompleta:
    soup = BeautifulSoup(html, "lxml")
    datos = _extract_labelled_rows(soup)

    parroquia_codigo, parroquia_nombre = _split_parroquia(datos.get("Parroquia"))
    fondo_codigo, fondo_descripcion = _split_fondo(datos.get("Fondo"))

    return FichaCompleta(
        id_registro=id_registro,
        sacramento_texto=_clean(datos.get("Sacramento")),
        fecha=_clean(datos.get("Fecha")),
        persona=_parse_persona(datos.get("Nombre y Apellidos")),
        padre=_parse_persona(datos.get("Padre") or datos.get("[Padre]")),
        madre=_parse_persona(datos.get("Madre") or datos.get("[Madre]")),
        esposo=_parse_persona(datos.get("[Esposo] Nombre y Apellidos")),
        esposa=_parse_persona(datos.get("[Esposa] Nombre y Apellidos")),
        conyuge=_parse_persona(datos.get("Cónyuge")),
        edad=_clean(datos.get("Edad")),
        comentarios=_clean(datos.get("Comentarios")),
        diocesis=_clean(datos.get("Diócesis")),
        territorio_historico=_clean(datos.get("Territorio histórico")),
        localidad_texto=_clean(datos.get("Localidad")),
        parroquia_codigo=parroquia_codigo,
        parroquia_nombre=parroquia_nombre,
        fondo_codigo=fondo_codigo,
        fondo_descripcion=fondo_descripcion,
        codigo_referencia=_clean(datos.get("Código de Referencia")),
        signatura=_clean(datos.get("Signatura")),
        sig_antigua=_clean(datos.get("Sig.Antigua")),
        sig_microfilm=_clean(datos.get("Sig.Microfilm")),
        sig_digital=_clean(datos.get("Sig.Digital")),
        sig_digital_libro=_clean(datos.get("Sig. Digital Libro")),
        pagina=_clean(datos.get("Página/folio")),
        fechas_libro=_clean(datos.get("Fechas del libro")),
    )
