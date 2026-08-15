from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from arbol_genealogico.infrastructure.db.models import Archivo


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    config_path: Path = Path("config/config.yaml")
    database_url: str = "postgresql+asyncpg://arbol:arbol@localhost:5432/arbol"

    scraper_base_url: str = "https://internet.aheb-beha.org"
    """Base URL del archivo AHEB-BEHA (Bizkaia)."""
    scraper_base_url_ahdv_geah: str = "https://internet.ahdv-geah.org"
    """Base URL del archivo AHDV-GEAH (Álava). Misma plataforma SIGA-AKIS,
    pero sin motor de búsqueda por localidad válido para barrido exhaustivo:
    se recorre por rango de ID de registro (ver ``features/scraping/rango``).
    """
    scraper_base_url_ahdss: str = "https://artxiboa.mendezmende.org"
    """Base URL del archivo AHDSS (Gipuzkoa, portal de Méndez Mende).
    Plataforma distinta de SIGA-AKIS (Yii/Arinka): el ID de registro es
    global (compartido entre bautismo/matrimonio/difunto) y se recorre por
    rango igual que AHDV-GEAH, pero probando los 3 sacramentos por ID (ver
    ``features/scraping/rango``)."""
    scraper_user_agent: str = "arbol-genealogico/0.1 (uso genealogico personal)"
    scraper_min_delay_s: float = 0.8
    scraper_max_delay_s: float = 1.6
    scraper_max_retries: int = 5
    scraper_raw_dir: Path = Path("data/raw")

    def base_url_for(self, archivo: Archivo) -> str:
        return {
            Archivo.AHEB_BEHA: self.scraper_base_url,
            Archivo.AHDV_GEAH: self.scraper_base_url_ahdv_geah,
            Archivo.AHDSS: self.scraper_base_url_ahdss,
        }[archivo]

    def raw_dir_for(self, archivo: Archivo) -> Path:
        """Carpeta de caché por archivo.

        AHEB-BEHA mantiene la carpeta histórica (``scraper_raw_dir`` a
        secas) para no perder la caché ya descargada; los archivos nuevos
        usan una subcarpeta propia (los IDs de registro colisionan entre
        archivos, así que además evita pisar cachés entre ellos).
        """
        if archivo is Archivo.AHEB_BEHA:
            return self.scraper_raw_dir
        return self.scraper_raw_dir / archivo.value
