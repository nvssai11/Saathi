from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from config import settings
from core.media_types import SUPPORTED_IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES = frozenset(SUPPORTED_IMAGE_EXTENSIONS.values())


async def save_defect_photo(photo: UploadFile, upload_dir: Path, filename_stem: str = "defect") -> str:
    if photo.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UNSUPPORTED_PHOTO_TYPE",
                "message": (
                    f"Unsupported photo type {photo.content_type!r} — "
                    f"accepted: {', '.join(sorted(_ALLOWED_CONTENT_TYPES))}"
                ),
            },
        )

    content = await photo.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "PHOTO_TOO_LARGE",
                "message": f"Photo exceeds the {settings.max_upload_size_bytes // (1024 * 1024)}MB limit",
            },
        )

    suffix = next(
        ext for ext, content_type in SUPPORTED_IMAGE_EXTENSIONS.items()
        if content_type == photo.content_type
    )
    photo_path = upload_dir / f"{filename_stem}{suffix}"

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        with open(photo_path, "wb") as f:
            f.write(content)
    except OSError as exc:
        logger.error("Failed to write uploaded photo to %s: %s", photo_path, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "PHOTO_SAVE_FAILED", "message": "Could not save the uploaded photo."},
        )

    return str(photo_path)
