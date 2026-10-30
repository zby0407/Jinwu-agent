"""Bounded, consent-scoped public observation acquisition; no inference calls."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .solar_data_catalog import (
    _POLAR_FILENAME,
    _POLAR_PERSISTENT_ID,
    _WSO_CURRENT_ROW,
    _validate_noaa_monthly_f107,
    _validate_polar_field,
    _validate_silso_extrema,
    _validate_silso_monthly,
    _validate_silso_smoothed,
    _wso_current_rows,
)
from .workspaces import register_project_data_file

CURATED_SOLAR_SOURCES = {
    "silso-monthly-total-v2": (
        "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt",
        "SN_m_tot_V2.0.txt",
    ),
    "silso-monthly-smoothed-v2": (
        "https://www.sidc.be/SILSO/DATA/SN_ms_tot_V2.0.csv",
        "SN_ms_tot_V2.0.csv",
    ),
    "silso-cycle-extrema-v2": (
        "https://www.sidc.be/SILSO/DATA/Cycles/TableCyclesMiMa.txt",
        "TableCyclesMiMa.txt",
    ),
    "mwo-wso-polar-field-v2": (
        "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi%3A10.7910%2FDVN%2FKF96B2",
        "e_PField_MWO_WSO.csv",
    ),
    "noaa-swpc-monthly-f107-v1": (
        "https://services.swpc.noaa.gov/json/solar-cycle/f10-7cm-flux.json",
        "f10-7cm-flux.json",
    ),
    "wso-current-polar-field-v1": ("http://wso.stanford.edu/Polar.html", "Polar.html"),
    "silso-monthly-hemispheric-v2": (
        "https://www.sidc.be/SILSO/DATA/SN_m_hem_V2.0.txt",
        "SN_m_hem_V2.0.txt",
    ),
    "silso-data-policy-v1": (
        "https://www.sidc.be/SILSO/datafiles",
        "silso_datafiles.html",
    ),
    "noaa-data-policy-v1": (
        "https://www.swpc.noaa.gov/content/data-access",
        "noaa_data_access.html",
    ),
    "wso-data-policy-v1": (
        "http://wso.stanford.edu/Polar.html",
        "wso_data_description.html",
    ),
}
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,95}\Z")


def _dump(path: Path, value: Any) -> None:
    staged = path.with_suffix(path.suffix + ".writing")
    staged.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(staged, path)


def _safe_path(root: Path, relative: str) -> Path:
    target = root / relative
    cursor = root
    for part in Path(relative).parts:
        if part == "..":
            raise ValueError("path traversal forbidden")
        cursor /= part
        if cursor.is_symlink() or (
            hasattr(cursor, "is_junction") and cursor.is_junction()
        ):
            raise ValueError("linked acquisition paths forbidden")
    if not target.resolve().is_relative_to(root):
        raise ValueError("acquisition path escaped root")
    return target


def profile_solar_payload(dataset_id: str, payload: bytes) -> dict[str, Any]:
    """Profile actual raw values and sentinels, never deriving hypothesis results."""
    text = payload.decode("utf-8")
    if dataset_id in {
        "silso-monthly-total-v2",
        "silso-monthly-smoothed-v2",
        "silso-monthly-hemispheric-v2",
    }:
        rows = [
            line.replace(";", " ").split() for line in text.splitlines() if line.strip()
        ]
        keys = [f"{int(row[0]):04d}-{int(row[1]):02d}" for row in rows]
        if any(not 1 <= int(row[1]) <= 12 for row in rows):
            raise ValueError("invalid calendar month")
        ordinals = [int(row[0]) * 12 + int(row[1]) for row in rows]
        profile = {
            "row_count": len(rows),
            "coverage_start": min(keys),
            "coverage_end": max(keys),
            "duplicate_keys": len(keys) - len(set(keys)),
            "sorted": keys == sorted(keys),
            "absent_months": max(ordinals) - min(ordinals) + 1 - len(set(keys)),
            "grain": "calendar month; not independent solar cycles",
            "sentinel": "negative values are missing, never zero-filled",
        }
        if dataset_id == "silso-monthly-hemispheric-v2":
            if len(rows) < 300 or not {len(row) for row in rows} <= {12, 13}:
                raise ValueError("unexpected hemispheric monthly shape")
            profile["field_counts"] = sorted({len(row) for row in rows})
            profile["missing_by_column"] = {
                str(i): sum(
                    not math.isfinite(float(row[i])) or float(row[i]) < 0
                    for row in rows
                )
                for i in range(3, 12)
            }
            profile["provisional_rows"] = sum(
                len(row) == 13 and row[-1] == "*" for row in rows
            )
            profile["total_north_south_inconsistency_count"] = sum(
                abs(float(row[3]) - float(row[4]) - float(row[5])) > 0.11
                for row in rows
                if all(float(row[j]) >= 0 for j in (3, 4, 5))
            )
        else:
            validator = (
                _validate_silso_monthly
                if dataset_id == "silso-monthly-total-v2"
                else _validate_silso_smoothed
            )
            validator(payload)
            profile["missing_by_column"] = {
                name: sum(float(row[i]) < 0 for row in rows)
                for i, name in (
                    (3, "sunspot_number"),
                    (4, "standard_deviation"),
                    (5, "observation_count"),
                )
            }
            valid = [
                key for key, row in zip(keys, rows, strict=True) if float(row[3]) >= 0
            ]
            profile["valid_value_start"] = min(valid)
            profile["valid_value_end"] = max(valid)
            profile["provisional_rows"] = sum(row[-1] in {"*", "0"} for row in rows)
        if (
            profile["duplicate_keys"]
            or not profile["sorted"]
            or profile["absent_months"]
        ):
            raise ValueError("monthly key completeness failed")
        return profile
    if dataset_id == "silso-cycle-extrema-v2":
        result = _validate_silso_extrema(payload)
        result["grain"] = (
            "one official cycle row; maximum availability is not cycle completion"
        )
        result["cycle_has_duration"] = sum(
            len(line.split()) >= 9
            for line in text.splitlines()
            if line.split() and line.split()[0].isdigit()
        )
        # Legacy validator calls any row with a maximum 'completed'; avoid that inference.
        result["latest_with_maximum"] = result.pop("latest_completed_cycle")
        return result
    if dataset_id == "noaa-swpc-monthly-f107-v1":
        result = _validate_noaa_monthly_f107(payload)
        result.pop("cutoff_month", None)
        result.pop("cutoff_2026_06_available", None)
        keys = [row["time-tag"] for row in json.loads(payload)]
        ordinals = [int(k[:4]) * 12 + int(k[5:]) for k in keys]
        result.update(
            duplicate_keys=len(keys) - len(set(keys)),
            absent_months=max(ordinals) - min(ordinals) + 1 - len(set(keys)),
            missing_values=0,
            grain="calendar month",
            warning="this endpoint is a short record; no unverified extension to 1947",
        )
        return result
    if dataset_id == "mwo-wso-polar-field-v2":
        result = _validate_polar_field(payload)
        rows = list(
            csv.reader(
                line
                for line in text.splitlines()
                if line.strip() and not line.startswith("#")
            )
        )
        header, values = rows[0], rows[1:]
        result["missing_by_column"] = {
            name: sum(not math.isfinite(float(row[i])) for row in values)
            for i, name in enumerate(header)
        }
        result["coverage_by_column"] = {
            name: [min(valid), max(valid)]
            if (
                valid := [
                    float(row[i]) for row in values if math.isfinite(float(row[i]))
                ]
            )
            else None
            for i, name in enumerate(header)
            if "Date" in name
        }
        result["duplicate_dates_by_column"] = {
            name: len(valid) - len(set(valid))
            for i, name in enumerate(header)
            if "Date" in name
            and (
                valid := [
                    float(row[i]) for row in values if math.isfinite(float(row[i]))
                ]
            )
        }
        result["grain"] = (
            "annual seasonally sampled pole/campaign records; proxy and direct measurements differ"
        )
        result["observable_kind"] = (
            "calibrated_faculae_proxy_and_polar_aperture_field; not axial_dipole_moment"
        )
        return result
    if dataset_id == "wso-current-polar-field-v1":
        rows = _wso_current_rows(payload)
        if not rows:
            raise ValueError("no recognizable WSO observations")
        dates = [row["date"] for row in rows]
        for date in dates:
            datetime.fromisoformat(date)
        raw_fields = [
            line.strip().split()
            for line in text.splitlines()
            if _WSO_CURRENT_ROW.fullmatch(line.strip())
        ]
        valid = [row["date"] for row in rows if not row["missing"]]
        if len(set(dates)) != len(dates) or dates != sorted(dates):
            raise ValueError("invalid WSO date keys")
        return {
            "row_count": len(rows),
            "coverage_start": dates[0],
            "coverage_end": dates[-1],
            "duplicate_keys": 0,
            "missing_rows": sum(row["missing"] for row in rows),
            "missing_by_column": {
                name: sum(row[index].startswith("XXX") for row in raw_fields)
                for index, name in (
                    (1, "north"),
                    (2, "south"),
                    (3, "average"),
                    (6, "north_filtered"),
                    (7, "south_filtered"),
                    (8, "average_filtered"),
                )
            },
            "latest_all_fields_valid": max(valid) if valid else None,
            "missing_since_2026_01": sum(
                row["missing"] for row in rows if row["date"] >= "2026-01-01"
            ),
            "grain": "centered 30-day averages at approximately 10-day cadence; serially overlapping",
            "sentinel": "XXX is missing; retained in raw",
            "observable_kind": "polar_aperture_field; not axial_dipole_moment",
            "units": None,
            "unit_status": "not stated in downloaded page; independent authority confirmation required before conversion",
            "source_warning": "source explicitly flags an observational interruption and interpolated or missing values",
        }
    if dataset_id.endswith("-data-policy-v1"):
        if "<html" not in text.lower() and "<!doctype" not in text.lower():
            raise ValueError("policy page is not recognizable HTML")
        return {
            "format": "official policy or data-description HTML",
            "scientific_observation": False,
            "license_status": "inspect exact source text; public access alone does not establish license",
        }
    raise ValueError("unsupported curated dataset")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedSolarAcquisition:
    """Persistent aggregate request/byte budgets, with no retries or overwrites."""

    def __init__(
        self, base: str | Path, *, project_id: str, authorization: dict[str, Any]
    ):
        if not _SAFE_NAME.fullmatch(project_id) or project_id in {"default", "legacy"}:
            raise ValueError("a separate valid project_id is required")
        campaign = authorization.get("acquisition_id", "")
        if not isinstance(campaign, str) or not _SAFE_NAME.fullmatch(campaign):
            raise ValueError("explicit acquisition_id authorization is required")
        ids = authorization.get("dataset_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(i, str) and i in CURATED_SOLAR_SOURCES for i in ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("explicit curated dataset whitelist is required")
        self.limits = {
            "max_http_requests": 30,
            "max_file_bytes": 10_000_000,
            "max_total_bytes": 100_000_000,
            "timeout_seconds": 30,
        }
        for key, ceiling in self.limits.items():
            value = authorization.get(key, ceiling)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= ceiling
            ):
                raise ValueError(f"invalid bounded limit: {key}")
            self.limits[key] = value
        if authorization.get("retries", 0) != 0:
            raise ValueError("acquisition retries must be zero")
        self.base = Path(base).expanduser().resolve()
        if (self.base / "data").exists():
            raise PermissionError(
                "acquisition requires an isolated base without legacy data mirrors"
            )
        self.project_id = project_id
        self.root = _safe_path(self.base, f"projects/{project_id}")
        self.control = _safe_path(self.root, "acquisition")
        self.control.mkdir(parents=True, exist_ok=True)
        self.state_path = self.control / "ledger.json"
        self.lock_path = self.control / "active.lock"
        self.authorization = {
            "acquisition_id": campaign,
            "dataset_ids": sorted(ids),
            **self.limits,
            "retries": 0,
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state["authorization"] != self.authorization:
                raise PermissionError(
                    "persisted acquisition authorization cannot change"
                )
            return state
        if (self.root / "shared/data").exists() or (
            self.root / "shared/project_data_catalog.json"
        ).exists():
            raise PermissionError("new acquisition cannot target existing project data")
        return {
            "schema_version": "bounded-solar-acquisition-v1",
            "authorization": self.authorization,
            "http_requests": 0,
            "received_bytes": 0,
            "requests": [],
            "datasets": {},
        }

    def _allowed_url(self, url: str, *, polar_file: bool = False) -> None:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in {None, 80, 443}
        ):
            raise PermissionError("non-curated URL rejected")
        allowed = {entry[0] for entry in CURATED_SOLAR_SOURCES.values()}
        if url in allowed:
            return
        if (
            polar_file
            and parsed.scheme == "https"
            and parsed.hostname == "dataverse.harvard.edu"
            and re.fullmatch(r"/api/access/datafile/\d+", parsed.path)
            and not parsed.query
        ):
            return
        # Only exact curated paths may move between HTTP and HTTPS on the same host.
        for original in allowed:
            ref = urllib.parse.urlsplit(original)
            if (
                parsed.scheme == "https"
                and parsed.netloc == ref.netloc
                and parsed.path == ref.path
                and parsed.query == ref.query
            ):
                return
        raise PermissionError("redirect or URL outside curated authority whitelist")

    def _fetch(
        self, state: dict[str, Any], url: str, *, polar_file: bool = False
    ) -> bytes:
        self._allowed_url(url, polar_file=polar_file)
        if state["http_requests"] >= self.limits["max_http_requests"]:
            raise RuntimeError("HTTP request budget exhausted")
        remaining = self.limits["max_total_bytes"] - state["received_bytes"]
        if remaining <= 0:
            raise RuntimeError("aggregate byte budget exhausted")
        state["http_requests"] += 1
        entry = {
            "url": url,
            "started_at": datetime.now(UTC).isoformat(),
            "received_bytes": 0,
            "status": "started",
        }
        state["requests"].append(entry)
        _dump(self.state_path, state)
        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "JW-public-data/1.0", "Accept-Encoding": "identity"},
        )
        start = time.monotonic()
        try:
            with opener.open(req, timeout=self.limits["timeout_seconds"]) as response:
                maximum = min(self.limits["max_file_bytes"], remaining)
                length = response.headers.get("Content-Length")
                if length and int(length) < 0:
                    raise ValueError("invalid negative payload length")
                if length and int(length) > maximum:
                    raise ValueError("declared payload exceeds byte budget")
                chunks = []
                while True:
                    seconds_left = self.limits["timeout_seconds"] - (
                        time.monotonic() - start
                    )
                    if seconds_left <= 0:
                        raise TimeoutError("request wall-time budget exhausted")
                    socket = getattr(
                        getattr(getattr(response, "fp", None), "raw", None),
                        "_sock",
                        None,
                    )
                    if socket is not None:
                        socket.settimeout(seconds_left)
                    available = maximum - entry["received_bytes"]
                    if available == 0:
                        if length and entry["received_bytes"] == int(length):
                            break
                        raise ValueError(
                            "payload byte budget reached before confirmed EOF"
                        )
                    block = response.read(min(65536, available))
                    if not block:
                        break
                    entry["received_bytes"] += len(block)
                    state["received_bytes"] += len(block)
                    _dump(self.state_path, state)
                    chunks.append(block)
                if length and entry["received_bytes"] != int(length):
                    raise ValueError(
                        "truncated response does not match declared length"
                    )
                entry["status"] = "downloaded"
                entry["sha256"] = hashlib.sha256(b"".join(chunks)).hexdigest()
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            entry["status"] = f"http_{exc.code}"
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                target = urllib.parse.urljoin(url, exc.headers["Location"])
                target_parts = urllib.parse.urlsplit(target)
                entry["redirect_target_authority_path"] = (
                    f"{target_parts.scheme}://{target_parts.netloc}{target_parts.path}"
                )
                self._allowed_url(target, polar_file=polar_file)
                if target in {item["url"] for item in state["requests"]}:
                    raise ValueError("redirect loop rejected") from exc
                return self._fetch(state, target, polar_file=polar_file)
            raise
        except Exception as exc:
            entry["status"] = type(exc).__name__
            raise
        finally:
            entry["finished_at"] = datetime.now(UTC).isoformat()
            _dump(self.state_path, state)

    def acquire(self, dataset_ids: list[str]) -> dict[str, Any]:
        if (
            not dataset_ids
            or len(dataset_ids) != len(set(dataset_ids))
            or not set(dataset_ids) <= set(self.authorization["dataset_ids"])
        ):
            raise PermissionError("requested datasets are not explicitly authorized")
        # Exclusive creation prevents concurrent callers from spending one shared budget.
        with self.lock_path.open("x", encoding="ascii") as lock:
            lock.write(str(os.getpid()))
        try:
            state = self._read_state()
            for dataset_id in dataset_ids:
                if dataset_id in state["datasets"]:
                    raise FileExistsError(
                        "dataset already attempted; retries and overwrites are forbidden"
                    )
                url, filename = CURATED_SOLAR_SOURCES[dataset_id]
                stage = _safe_path(self.control, filename)
                if stage.exists():
                    raise FileExistsError("staged payload already exists")
                relative = f"curated/{self.authorization['acquisition_id']}/{dataset_id}/{filename}"
                destination = _safe_path(self.root, f"shared/data/{relative}")
                if (
                    destination.exists()
                    or destination.with_name(
                        destination.name + ".provenance.json"
                    ).exists()
                ):
                    raise FileExistsError(
                        "registered input path already exists; no overwrite allowed"
                    )
                state["datasets"][dataset_id] = {"status": "started"}
                _dump(self.state_path, state)
                try:
                    payload = self._fetch(state, url)
                    provenance: dict[str, Any] = {
                        "authority_url": url,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "acquisition_id": self.authorization["acquisition_id"],
                        "no_fill": True,
                    }
                    if dataset_id == "mwo-wso-polar-field-v2":
                        metadata_path = self.control / "polar_metadata.json"
                        with metadata_path.open("xb") as output:
                            output.write(payload)
                        latest = json.loads(payload)["data"]["latestVersion"]
                        selected = next(
                            item["dataFile"]
                            for item in latest["files"]
                            if item.get("restricted") is False
                            and item["dataFile"]["filename"] == _POLAR_FILENAME
                        )
                        url = f"https://dataverse.harvard.edu/api/access/datafile/{selected['id']}"
                        payload = self._fetch(state, url, polar_file=True)
                        checksum = selected.get("checksum", {})
                        if checksum.get("type") != "MD5" or hashlib.md5(
                            payload, usedforsecurity=False
                        ).hexdigest() != checksum.get("value"):
                            raise ValueError("upstream polar checksum mismatch")
                        provenance.update(
                            metadata_url=CURATED_SOLAR_SOURCES[dataset_id][0],
                            metadata_sha256=hashlib.sha256(
                                metadata_path.read_bytes()
                            ).hexdigest(),
                            persistent_id=_POLAR_PERSISTENT_ID,
                            license=latest.get("license"),
                            upstream_checksum=checksum,
                            version=f"{latest.get('versionNumber')}.{latest.get('versionMinorNumber')}",
                        )
                    elif dataset_id.startswith("silso-"):
                        provenance.update(
                            license="CC BY-NC 4.0",
                            license_source="https://www.sidc.be/SILSO/datafiles",
                            attribution="SILSO, Royal Observatory of Belgium, Brussels",
                            dataset_doi="https://doi.org/10.24414/qnza-ac80",
                        )
                    else:
                        provenance["license_status"] = (
                            "not established from downloaded product; official attribution required"
                        )
                    # Preserve even rejected upstream bytes without registering them as usable data.
                    with stage.open("xb") as output:
                        output.write(payload)
                    profile = profile_solar_payload(dataset_id, payload)
                    provenance.update(retrieval_url=url, validation=profile)
                    if dataset_id.endswith("-data-policy-v1"):
                        state["datasets"][dataset_id] = {
                            "status": "reference_retained",
                            "raw_path": str(stage),
                            "raw_sha256": hashlib.sha256(payload).hexdigest(),
                            "quality_profile": profile,
                        }
                        _dump(self.state_path, state)
                        continue
                    relative = f"curated/{self.authorization['acquisition_id']}/{dataset_id}/{filename}"
                    record = register_project_data_file(
                        self.base,
                        stage,
                        relative,
                        dataset_id=dataset_id,
                        provenance=provenance,
                        project_id=self.project_id,
                    )
                    state["datasets"][dataset_id] = {
                        "status": "registered",
                        "record": record,
                        "quality_profile": profile,
                    }
                except Exception as exc:
                    state["datasets"][dataset_id] = {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                        "raw_retained": stage.exists(),
                    }
                _dump(self.state_path, state)
            return {
                "project_id": self.project_id,
                "ledger_path": str(self.state_path),
                "http_requests": state["http_requests"],
                "received_bytes": state["received_bytes"],
                "retries": 0,
                "datasets": state["datasets"],
            }
        finally:
            self.lock_path.unlink(missing_ok=True)

    def register_retained(
        self,
        dataset_ids: list[str],
        *,
        verified_local_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Locally revalidate retained bytes, preserving every failed fetch record.

        The only local-copy route is the exact pole proxy whose fresh authority
        metadata checksum matches the explicitly runtime-authorized source.
        """
        if (
            not dataset_ids
            or len(dataset_ids) != len(set(dataset_ids))
            or not set(dataset_ids) <= set(self.authorization["dataset_ids"])
        ):
            raise PermissionError("retained registration is not authorized")
        with self.lock_path.open("x", encoding="ascii") as lock:
            lock.write(str(os.getpid()))
        try:
            state = self._read_state()
            repairs = state.setdefault("registration_repairs", {})
            records = []
            for dataset_id in dataset_ids:
                if (
                    dataset_id in repairs
                    or state["datasets"].get(dataset_id, {}).get("status") != "failed"
                ):
                    raise PermissionError(
                        "only one explicit local repair of a failed acquisition is allowed"
                    )
                if dataset_id not in {
                    "mwo-wso-polar-field-v2",
                    "silso-monthly-hemispheric-v2",
                }:
                    raise PermissionError(
                        "this dataset has no retained registration route"
                    )
                url, filename = CURATED_SOLAR_SOURCES[dataset_id]
                stage = self.control / filename
                provenance = {
                    "authority_url": url,
                    "acquisition_id": self.authorization["acquisition_id"],
                    "registered_after_local_revalidation": True,
                    "no_fill": True,
                    "prior_acquisition_failure": dict(state["datasets"][dataset_id]),
                }
                if dataset_id == "mwo-wso-polar-field-v2":
                    settings = (verified_local_inputs or {}).get(dataset_id, {})
                    source = Path(settings.get("path", ""))
                    source_provenance = Path(settings.get("provenance_path", ""))
                    if (
                        not source.is_file()
                        or source.is_symlink()
                        or not source_provenance.is_file()
                    ):
                        raise PermissionError(
                            "authorized immutable local raw input is unavailable"
                        )
                    if source.stat().st_size > self.limits["max_file_bytes"]:
                        raise ValueError(
                            "authorized local raw exceeds per-file byte budget"
                        )
                    payload = source.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != settings.get(
                        "sha256"
                    ) or hashlib.sha256(
                        source_provenance.read_bytes()
                    ).hexdigest() != settings.get("provenance_sha256"):
                        raise ValueError("authorized local input hash mismatch")
                    original = json.loads(source_provenance.read_bytes())
                    if (
                        original.get("dataset_id") != dataset_id
                        or original.get("data_sha256") != settings["sha256"]
                    ):
                        raise ValueError("local input provenance mismatch")
                    metadata_path = self.control / "polar_metadata.json"
                    latest = json.loads(metadata_path.read_bytes())["data"][
                        "latestVersion"
                    ]
                    selected = next(
                        item["dataFile"]
                        for item in latest["files"]
                        if item.get("restricted") is False
                        and item["dataFile"]["filename"] == _POLAR_FILENAME
                    )
                    checksum = selected.get("checksum", {})
                    if checksum.get("type") != "MD5" or hashlib.md5(
                        payload, usedforsecurity=False
                    ).hexdigest() != checksum.get("value"):
                        raise ValueError(
                            "fresh authority metadata differs from local raw"
                        )
                    profile = profile_solar_payload(dataset_id, payload)
                    with stage.open("xb") as output:
                        output.write(payload)
                    provenance.update(
                        source_mode="verified_local_raw_copy",
                        source_path=str(source.resolve()),
                        source_provenance_sha256=settings["provenance_sha256"],
                        metadata_sha256=hashlib.sha256(
                            metadata_path.read_bytes()
                        ).hexdigest(),
                        upstream_checksum=checksum,
                        version=f"{latest.get('versionNumber')}.{latest.get('versionMinorNumber')}",
                        license=latest.get("license"),
                        freshness="freshly fetched metadata confirms local bytes; raw was not redownloaded",
                    )
                else:
                    if not stage.is_file() or stage.is_symlink():
                        raise PermissionError("retained downloaded bytes unavailable")
                    payload = stage.read_bytes()
                    request = next(
                        (
                            entry
                            for entry in state["requests"]
                            if entry.get("url") == url
                            and entry.get("sha256")
                            == hashlib.sha256(payload).hexdigest()
                            and entry.get("status") == "downloaded"
                        ),
                        None,
                    )
                    if request is None:
                        raise ValueError(
                            "retained bytes have no matching native download receipt"
                        )
                    profile = profile_solar_payload(dataset_id, payload)
                    provenance.update(
                        source_mode="retained_download_revalidated",
                        retrieved_at=request["finished_at"],
                        license="CC BY-NC 4.0",
                        license_source="https://www.sidc.be/SILSO/datafiles",
                        attribution="SILSO, Royal Observatory of Belgium, Brussels",
                        dataset_doi="https://doi.org/10.24414/qnza-ac80",
                    )
                provenance["validation"] = profile
                relative = f"curated/{self.authorization['acquisition_id']}/{dataset_id}/{filename}"
                if (self.root / "shared/data" / relative).exists():
                    raise FileExistsError("retained registration cannot overwrite data")
                record = register_project_data_file(
                    self.base,
                    stage,
                    relative,
                    dataset_id=dataset_id,
                    provenance=provenance,
                    project_id=self.project_id,
                )
                repairs[dataset_id] = {
                    "status": "registered",
                    "record": record,
                    "quality_profile": profile,
                    "prior_failure_preserved": True,
                    "new_http_requests": 0,
                }
                records.append(repairs[dataset_id])
                _dump(self.state_path, state)
            return {
                "project_id": self.project_id,
                "new_http_requests": 0,
                "records": records,
                "ledger_path": str(self.state_path),
            }
        finally:
            self.lock_path.unlink(missing_ok=True)
