from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.uploads import save_defect_photo
from config import settings


class _FakeUploadFile:
    def __init__(self, content: bytes, content_type: str, filename: str = "photo.jpg"):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self.read = AsyncMock(return_value=content)


@pytest.mark.anyio
async def test_rejects_unsupported_content_type(tmp_path):
    photo = _FakeUploadFile(b"not-really-a-pdf", "application/pdf")

    with pytest.raises(HTTPException) as exc_info:
        await save_defect_photo(photo, tmp_path)

    assert exc_info.value.status_code == 415
    assert exc_info.value.detail["code"] == "UNSUPPORTED_PHOTO_TYPE"


@pytest.mark.anyio
async def test_rejects_oversized_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_bytes", 10)
    photo = _FakeUploadFile(b"x" * 11, "image/jpeg")

    with pytest.raises(HTTPException) as exc_info:
        await save_defect_photo(photo, tmp_path)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "PHOTO_TOO_LARGE"


@pytest.mark.anyio
async def test_saves_valid_photo_and_returns_path_matching_content_type(tmp_path):
    photo = _FakeUploadFile(b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg", filename="photo.png")

    saved_path = await save_defect_photo(photo, tmp_path, filename_stem="defect")

    assert saved_path.endswith("defect.jpg")
    from pathlib import Path
    assert Path(saved_path).read_bytes() == b"\xff\xd8\xff\xe0fakejpeg"


@pytest.mark.anyio
async def test_raises_clean_500_on_disk_write_failure(tmp_path):
    photo = _FakeUploadFile(b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")

    with patch("api.uploads.open", side_effect=OSError("disk full")):
        with pytest.raises(HTTPException) as exc_info:
            await save_defect_photo(photo, tmp_path)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["code"] == "PHOTO_SAVE_FAILED"
    assert "disk full" not in str(exc_info.value.detail)
