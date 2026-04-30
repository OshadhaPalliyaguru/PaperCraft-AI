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
    # Comma-separated list of HF tokens. We rotate through them on quota
    # errors so 3 free accounts ~= 15 min of ZeroGPU minutes per day.
    HF_TOKEN: str = ""
    TARGET_FACES: int = 35
    PAGE_SIZE: str = "A4"
    # If true, generate textured (colored) GLB. ~3x more GPU minutes per call.
    # Leave false on the free tier so you get more generations per day.
    WITH_TEXTURE: bool = False

    def hf_tokens(self) -> list[str]:
        """Parse HF_TOKEN into a clean list (supports comma-separated list)."""
        return [t.strip() for t in (self.HF_TOKEN or "").split(",") if t.strip()]


settings = Settings()
