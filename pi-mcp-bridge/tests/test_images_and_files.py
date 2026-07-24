"""Tests for image encoding, file helpers, and new tool prompts."""

import base64
from pathlib import Path

import pytest

from pi_mcp_bridge.pi_client import encode_image
from pi_mcp_bridge.server import (
    _build_edit_file_prompt,
    _build_read_file_prompt,
    _resolve_path,
    _strip_code_fences,
    _write_text_file,
)


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    """Create a minimal 1x1 transparent PNG for testing."""
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    png_path = tmp_path / "tiny.png"
    png_path.write_bytes(data)
    return png_path


def test_encode_image(tiny_png: Path):
    result = encode_image(str(tiny_png))
    assert result["type"] == "image"
    assert result["mimeType"] == "image/png"
    assert result["data"]


def test_encode_image_missing():
    with pytest.raises(FileNotFoundError):
        encode_image("/nonexistent/image.png")


def test_strip_code_fences():
    assert _strip_code_fences("```python\nprint(1)\n```") == "print(1)"
    assert _strip_code_fences("plain text") == "plain text"


def test_resolve_path_relative(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = _resolve_path("foo/bar.txt", str(tmp_path))
    assert resolved == tmp_path / "foo" / "bar.txt"


def test_resolve_path_absolute(tmp_path: Path):
    resolved = _resolve_path(str(tmp_path / "x.txt"), "/other")
    assert resolved == tmp_path / "x.txt"


def test_write_text_file(tmp_path: Path):
    path = _write_text_file("nested/dir/file.txt", "hello", str(tmp_path))
    assert path.exists()
    assert path.read_text() == "hello"


def test_build_read_file_prompt_with_question():
    prompt = _build_read_file_prompt("src/a.py", "x = 1", "What does x do?")
    assert "src/a.py" in prompt
    assert "x = 1" in prompt
    assert "What does x do?" in prompt


def test_build_read_file_prompt_without_question():
    prompt = _build_read_file_prompt("src/a.py", "x = 1", None)
    assert "src/a.py" in prompt
    assert "x = 1" in prompt


def test_build_edit_file_prompt():
    prompt = _build_edit_file_prompt("src/a.py", "x = 1", "change to y")
    assert "src/a.py" in prompt
    assert "x = 1" in prompt
    assert "change to y" in prompt
    assert "Return the complete updated file content" in prompt
