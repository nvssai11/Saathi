from __future__ import annotations

from unittest.mock import patch

from agents.verification import reference_images


def test_get_path_returns_none_when_no_file_matches(tmp_path):
    with patch.object(reference_images, "REFERENCE_IMAGE_DIR", tmp_path):
        assert reference_images.get_path("no-such-product") is None


def test_get_path_finds_png(tmp_path):
    (tmp_path / "jute-tote-bag.png").write_bytes(b"fake")
    with patch.object(reference_images, "REFERENCE_IMAGE_DIR", tmp_path):
        result = reference_images.get_path("jute-tote-bag")
    assert result == tmp_path / "jute-tote-bag.png"


def test_get_path_finds_jpg_when_png_absent(tmp_path):
    (tmp_path / "khadi-scarf.jpg").write_bytes(b"fake")
    with patch.object(reference_images, "REFERENCE_IMAGE_DIR", tmp_path):
        result = reference_images.get_path("khadi-scarf")
    assert result == tmp_path / "khadi-scarf.jpg"


def test_get_path_does_not_match_a_directory(tmp_path):
    (tmp_path / "bamboo-basket.png").mkdir()
    with patch.object(reference_images, "REFERENCE_IMAGE_DIR", tmp_path):
        assert reference_images.get_path("bamboo-basket") is None
