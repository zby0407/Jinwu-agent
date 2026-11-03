"""Expand archived datasets without trusting member names.

Datasets are often shipped as one zip: a supplement bundle, a snapshot of a
catalogue, or a SNANA light-curve export.  Extraction therefore has to open the
archive, verify every member stays inside the target directory, honour the same
size budget as a single download, and report what it refused.  A zip is never
allowed to become an unbounded read.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_MEMBERS = 50
MAX_MEMBER_BYTES = 200_000_000
MAX_TOTAL_BYTES = 500_000_000


@dataclass(slots=True)
class ArchiveExpansion:
    """Member paths that were written, plus everything that was skipped."""

    members: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def expand_zip(
    path: str | Path,
    *,
    target_dir: str | Path,
    max_members: int = MAX_MEMBERS,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> ArchiveExpansion:
    """Extract safe, size-bounded members of ``path`` into ``target_dir``."""

    source = Path(path)
    target = Path(target_dir)
    expansion = ArchiveExpansion()
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile):
        expansion.warnings.append(f"zip_unreadable:{source.name}")
        return expansion

    with archive:
        total = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if _skip_member(name):
                continue
            relative = _safe_relative(name)
            if relative is None:
                expansion.warnings.append(f"zip_unsafe_member:{name}")
                continue
            if len(expansion.members) >= max_members:
                expansion.warnings.append(
                    f"zip_too_many_members:{len(archive.infolist())}"
                )
                break
            if info.file_size > max_member_bytes:
                expansion.warnings.append(
                    f"zip_member_too_large:{name}:{info.file_size}"
                )
                continue
            if total + info.file_size > max_total_bytes:
                expansion.warnings.append(f"zip_total_too_large:{name}")
                break
            destination = target / relative
            if not _inside(target, destination):
                expansion.warnings.append(f"zip_unsafe_member:{name}")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                written = _copy_member(archive, info, destination, max_member_bytes)
            except (OSError, zipfile.BadZipFile, RuntimeError):
                expansion.warnings.append(f"zip_member_unreadable:{name}")
                continue
            if written < 0:
                expansion.warnings.append(f"zip_member_too_large:{name}")
                destination.unlink(missing_ok=True)
                continue
            total += written
            expansion.members.append(destination)
    return expansion


def _skip_member(name: str) -> bool:
    if not name or name.startswith("__MACOSX/"):
        return True
    return any(
        part.startswith(".") and part not in {".", ".."}
        for part in PurePosixPath(name).parts
    )


def _safe_relative(name: str) -> PurePosixPath | None:
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    if any(":" in part for part in candidate.parts):
        return None
    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts:
        return None
    return PurePosixPath(*parts)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _copy_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    max_bytes: int,
) -> int:
    """Copy one member, refusing more bytes than the declared size allows."""

    limit = min(info.file_size, max_bytes) + 1
    written = 0
    with archive.open(info) as source, destination.open("wb") as sink:
        while True:
            chunk = source.read(1 << 16)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                return -1
            sink.write(chunk)
    return written


__all__ = ["ArchiveExpansion", "expand_zip"]
