"""Shared I/O for the offline solar data publishers."""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

WORKSPACE = Path(os.getenv("JW_WORKSPACE_DIR") or Path(__file__).resolve().parents[1] / "workspace")


def utc_time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetch(url: str, limit: int = 4_000_000) -> tuple[bytes, str | None]:
    request = Request(url, headers={"User-Agent": "JW-Today-Sun/1.0"})
    for attempt in range(2):
        try:
            with urlopen(request, timeout=15) as response:
                data = response.read(limit + 1)
                modified = response.headers.get("Last-Modified")
            if len(data) > limit:
                raise ValueError(f"Response exceeds the size limit: {url}")
            return data, modified
        except OSError as error:
            if attempt or (isinstance(error, HTTPError) and error.code < 500):
                raise
    raise AssertionError("unreachable")


def download(url: str, limit: int = 4_000_000) -> bytes:
    return fetch(url, limit)[0]


@contextmanager
def cache_lock(path: Path):
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def publish(target: Path, data: dict) -> None:
    pending = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as file:
            pending = Path(file.name)
            json.dump(data, file, separators=(",", ":"), allow_nan=False)
        pending.replace(target)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
