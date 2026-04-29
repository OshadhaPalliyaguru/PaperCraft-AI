"""Central configuration loaded from environment / .env file."""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / "tmp"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
STATIC_DIR = PROJECT_ROOT / "static"
BLENDER_SCRIPTS_DIR = PROJECT_ROOT / "blender_scripts"

for _d in (TMP_DIR, OUTPUTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    BLENDER_PATH: str = ""
    HF_SPACE_ID: str = "tencent/Hunyuan3D-2"
    HF_TOKEN: str = ""
    TARGET_FACES: int = 80
    PAGE_SIZE: str = "A4"


settings = Settings()
