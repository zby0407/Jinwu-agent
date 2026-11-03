"""Zip expansion: bounded, safe, and explicit about what it refused."""

from __future__ import annotations

import zipfile
from pathlib import Path

from jw.scientific_data.archives import expand_zip


def _zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name, payload in members.items():
            handle.writestr(name, payload)
    return archive


def test_members_are_written_under_the_target_directory(tmp_path):
    archive = _zip(
        tmp_path,
        {
            "data/lc.csv": b"mjd,mag\n1,15.0\n",
            "README.txt": b"SN 2011fe\n",
        },
    )

    expansion = expand_zip(archive, target_dir=tmp_path / "out")

    assert sorted(member.name for member in expansion.members) == [
        "README.txt",
        "lc.csv",
    ]
    extracted = tmp_path / "out" / "data" / "lc.csv"
    assert extracted.read_bytes() == b"mjd,mag\n1,15.0\n"
    assert expansion.warnings == []


def test_path_traversal_is_refused_and_nothing_escapes(tmp_path):
    archive = _zip(tmp_path, {"../evil.csv": b"x\n1\n"})

    expansion = expand_zip(archive, target_dir=tmp_path / "out")

    assert expansion.members == []
    assert expansion.warnings == ["zip_unsafe_member:../evil.csv"]
    assert not (tmp_path / "evil.csv").exists()


def test_absolute_member_paths_are_refused(tmp_path):
    archive = _zip(tmp_path, {"/etc/passwd": b"root\n"})

    expansion = expand_zip(archive, target_dir=tmp_path / "out")

    assert expansion.members == []
    assert expansion.warnings == ["zip_unsafe_member:/etc/passwd"]


def test_oversized_members_are_skipped(tmp_path):
    archive = _zip(
        tmp_path,
        {"big.csv": b"x" * 2_000, "small.csv": b"x\n1\n"},
    )

    expansion = expand_zip(archive, target_dir=tmp_path / "out", max_member_bytes=1_000)

    assert [member.name for member in expansion.members] == ["small.csv"]
    assert expansion.warnings == ["zip_member_too_large:big.csv:2000"]
    assert not (tmp_path / "out" / "big.csv").exists()


def test_member_count_is_capped(tmp_path):
    archive = _zip(tmp_path, {f"t{index}.csv": b"a\n1\n" for index in range(5)})

    expansion = expand_zip(archive, target_dir=tmp_path / "out", max_members=2)

    assert len(expansion.members) == 2
    assert expansion.warnings[0].startswith("zip_too_many_members:")


def test_macos_metadata_and_dotfiles_are_ignored_silently(tmp_path):
    archive = _zip(
        tmp_path,
        {
            "__MACOSX/._lc.csv": b"\x00",
            ".hidden": b"x",
            "lc.csv": b"a\n1\n",
        },
    )

    expansion = expand_zip(archive, target_dir=tmp_path / "out")

    assert [member.name for member in expansion.members] == ["lc.csv"]
    assert expansion.warnings == []


def test_a_corrupt_archive_is_reported(tmp_path):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")

    expansion = expand_zip(archive, target_dir=tmp_path / "out")

    assert expansion.members == []
    assert expansion.warnings == ["zip_unreadable:broken.zip"]
