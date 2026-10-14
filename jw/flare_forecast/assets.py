"""Import only declared input/model assets, verifying every original byte."""

import hashlib
import json
import shutil
from pathlib import Path

from .engine import inventory


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def child(root: Path, relative: str) -> Path:
    root = root.resolve()
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or "\\" in relative:
        raise ValueError("Asset paths must be confined POSIX-relative names")
    target = root / part
    for path in (root, *target.parents, target):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Asset paths cannot traverse links")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Asset path escaped root")
    return target


def verify_assets(root: Path) -> dict:
    root = root.resolve()
    total = 0
    for row in inventory()["files"]:
        path = child(root, row["path"])
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError("Original asset bytes differ: " + row["path"])
        total += row["bytes"]
    return {"status": "verified", "files": len(inventory()["files"]), "bytes": total}


def import_assets(raw: Path, sources: dict[str, Path], output: Path) -> dict:
    """No acquisition or scientific execution; copy only the pinned inventory."""
    if output.exists():
        raise ValueError("Asset destination already exists; never overwrite")
    records = inventory()["files"]
    resolved = []
    for row in records:
        source = (
            child(raw, row["source"][4:])
            if row["source"].startswith("raw/")
            else Path(sources[row["path"]]).resolve()
        )
        if source.is_symlink() or (
            hasattr(source, "is_junction") and source.is_junction()
        ):
            raise ValueError("Original asset must be a regular file")
        if source.stat().st_size != row["bytes"] or sha(source) != row["sha256"]:
            raise ValueError("Source asset differs: " + row["path"])
        resolved.append((row, source))
    output.mkdir(parents=True, exist_ok=False)
    for row, source in resolved:
        target = child(output, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    result = verify_assets(output)
    (output / "import_receipt.json").write_text(
        json.dumps(
            {**result, "reports_included": False, "no_fit_or_issue": True}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return result
