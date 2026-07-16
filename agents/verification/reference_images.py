from __future__ import annotations

from pathlib import Path

from config import settings

REFERENCE_IMAGE_DIR = Path(settings.reference_image_directory)

_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def get_path(product_type: str) -> Path | None:
    for ext in _EXTENSIONS:
        candidate = REFERENCE_IMAGE_DIR / f"{product_type}{ext}"
        if candidate.is_file():
            return candidate
    return None
