from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import urllib.robotparser
from pathlib import Path
from types import TracebackType
from typing import Self

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from arbol_genealogico.config.paths import resolve_path
from arbol_genealogico.config.settings import AppSettings
from arbol_genealogico.infrastructure.db.models import Archivo

logger = logging.getLogger(__name__)

# El portal AHEB-BEHA sirve el HTML declarando iso-8859-1; los acentos se
# pierden si se decodifica como UTF-8 (verificado en tests/fixtures/*.html).
# AHDV-GEAH, aunque usa la misma plataforma SIGA-AKIS, sirve UTF-8 de verdad
# (cabecera "Content-Type: text/html; charset=UTF-8", comprobado a mano).
# AHDSS (Gipuzkoa) también declara y sirve UTF-8 de verdad.
RESPONSE_ENCODING = "iso-8859-1"
_RESPONSE_ENCODING_POR_ARCHIVO = {
    Archivo.AHEB_BEHA: "iso-8859-1",
    Archivo.AHDV_GEAH: "utf-8",
    Archivo.AHDSS: "utf-8",
}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class ScraperClient:
    """Cliente HTTP respetuoso para el portal SIGA-AKIS de AHEB-BEHA.

    - Un único request en vuelo a la vez (rate limiting serializado).
    - Espera aleatoria entre requests (``scraper_min_delay_s``..``scraper_max_delay_s``).
    - Reintentos con backoff exponencial ante 429/5xx/errores de red.
    - Cache en disco: si el HTML ya fue descargado, no se vuelve a pedir.
    - Cookies de sesión persistentes durante toda la vida del cliente.
    """

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        base_url: str | None = None,
        raw_dir: Path | None = None,
        response_encoding: str | None = None,
    ) -> None:
        """``base_url``/``raw_dir``/``response_encoding`` permiten reutilizar
        este cliente con otro archivo diocesano (misma plataforma SIGA-AKIS,
        distinto dominio y, en el caso de AHDV-GEAH, distinta codificación
        de respuesta: sirve UTF-8 en vez de iso-8859-1)."""
        self.settings = settings or AppSettings()
        self.base_url = base_url or self.settings.scraper_base_url
        self.raw_dir: Path = resolve_path(raw_dir or self.settings.scraper_raw_dir)
        self.response_encoding = response_encoding or RESPONSE_ENCODING
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._robots: urllib.robotparser.RobotFileParser | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "User-Agent": self.settings.scraper_user_agent,
                "Accept-Language": "es-ES,es;q=0.9,eu;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        await self._load_robots()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _load_robots(self) -> None:
        parser = urllib.robotparser.RobotFileParser()
        try:
            assert self._client is not None
            resp = await self._client.get("/robots.txt")
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                logger.info("robots.txt cargado correctamente")
            else:
                parser.parse([])
        except httpx.HTTPError:
            logger.warning("No se pudo leer robots.txt; se continúa sin restricciones conocidas")
            parser.parse([])
        self._robots = parser

    def allowed(self, path: str) -> bool:
        if self._robots is None:
            return True
        return self._robots.can_fetch(self.settings.scraper_user_agent, path)

    async def _throttle(self) -> None:
        delay = random.uniform(self.settings.scraper_min_delay_s, self.settings.scraper_max_delay_s)
        await asyncio.sleep(delay)

    async def _request(
        self, method: str, path: str, *, allow_statuses: frozenset[int] = frozenset(), **kwargs: object
    ) -> httpx.Response:
        """``allow_statuses`` permite tratar ciertos códigos como respuestas
        válidas (no reintentables ni excepción), sin encender ``raise_for_status``:
        AHDSS señaliza "no existe este ID para este sacramento" con un 404 real
        (a diferencia de AHDV-GEAH, que devuelve 200 con una página de error PHP
        sin datos: ver ``parser.parse_ficha``/``parse_ficha_ahdss``)."""
        assert self._client is not None, "usar dentro de 'async with ScraperClient() as client'"
        if not self.allowed(path):
            raise PermissionError(f"robots.txt prohíbe acceder a {path}")

        async with self._lock:

            @retry(
                reraise=True,
                stop=stop_after_attempt(self.settings.scraper_max_retries),
                wait=wait_exponential(multiplier=5, min=5, max=60),
                retry=retry_if_exception(_is_retryable),
            )
            async def _do() -> httpx.Response:
                response = await self._client.request(method, path, **kwargs)  # type: ignore[union-attr]
                if response.status_code not in allow_statuses:
                    response.raise_for_status()
                response.encoding = self.response_encoding
                return response

            try:
                return await _do()
            finally:
                await self._throttle()

    async def get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_statuses: frozenset[int] = frozenset(),
    ) -> httpx.Response:
        return await self._request("GET", path, params=params, allow_statuses=allow_statuses)

    async def post(
        self, path: str, data: dict[str, object] | None = None, params: dict[str, object] | None = None
    ) -> httpx.Response:
        return await self._request("POST", path, data=data, params=params)

    def cache_path(self, *parts: str) -> Path:
        return self.raw_dir.joinpath(*parts)

    @staticmethod
    def sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_cached(
        self,
        path: str,
        cache_file: Path,
        params: dict[str, object] | None = None,
        *,
        allow_statuses: frozenset[int] = frozenset(),
    ) -> str:
        """GET con cache en disco: si ``cache_file`` existe, no se hace la petición."""
        if cache_file.exists():
            return _read_cache(cache_file)
        response = await self.get(path, params=params, allow_statuses=allow_statuses)
        html = response.text
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        _write_cache(cache_file, html)
        return html

    async def post_cached(
        self,
        path: str,
        cache_file: Path,
        data: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
    ) -> str:
        """POST con cache en disco: si ``cache_file`` existe, no se hace la petición."""
        if cache_file.exists():
            return _read_cache(cache_file)
        response = await self.post(path, data=data, params=params)
        html = response.text
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        _write_cache(cache_file, html)
        return html


def build_client(settings: AppSettings, archivo: Archivo) -> ScraperClient:
    """Construye el cliente HTTP apuntando al dominio/codificación del archivo dado."""
    return ScraperClient(
        settings,
        base_url=settings.base_url_for(archivo),
        raw_dir=settings.raw_dir_for(archivo),
        response_encoding=_RESPONSE_ENCODING_POR_ARCHIVO[archivo],
    )


def _write_cache(path: Path, text: str) -> None:
    # newline="" desactiva la traducción de saltos de línea: el HTML ya trae
    # "\r\n" del servidor y, si no se desactiva, Windows lo duplicaría a "\r\r\n".
    # Path.write_text/read_text no exponen "newline" hasta Python 3.13, así que
    # se usa open() directamente.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read_cache(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()
