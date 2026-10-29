from __future__ import annotations

import io
import json
import urllib.error

import pytest

from jw.solar_acquisition import CURATED_SOLAR_SOURCES, BoundedSolarAcquisition
from jw.tools import get_builtin_tool_registry, get_tool_bundles
from jw.tools.solar_acquisition import download_curated_solar_data

DATASET = "wso-current-polar-field-v1"
BODY = b"2026:01:09_21h:07m:13s 1N -2S 1Avg 20nhz filt: 1Nf -2Sf 1Avgf\n"


def authorization(**overrides):
    return {"acquisition_id": "fresh-audit-001", "dataset_ids": [DATASET], **overrides}


def client(tmp_path, **overrides):
    return BoundedSolarAcquisition(
        tmp_path, project_id="b3-fresh-test", authorization=authorization(**overrides)
    )


class Response(io.BytesIO):
    def __init__(self, body=BODY, length=True):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))} if length else {}


def network(monkeypatch, *, body=BODY, length=True, exception=None):
    calls = []

    class Opener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, timeout))
            if exception:
                raise exception
            return Response(body, length)

    monkeypatch.setattr(
        "jw.solar_acquisition.urllib.request.build_opener", lambda *_args: Opener()
    )
    return calls


def test_native_tool_requires_runtime_authorization():
    with pytest.raises(PermissionError, match="authorization"):
        download_curated_solar_data.func([DATASET], {})


@pytest.mark.parametrize("project_id", ["default", "legacy", "../escape", "X:", "x/y"])
def test_existing_or_traversing_project_ids_are_rejected(tmp_path, project_id):
    with pytest.raises(ValueError, match="project_id"):
        BoundedSolarAcquisition(
            tmp_path, project_id=project_id, authorization=authorization()
        )


def test_rejects_unknown_sources_and_relaxed_limits(tmp_path):
    for overrides in (
        {"dataset_ids": ["unknown"]},
        {"max_http_requests": 31},
        {"max_file_bytes": 10_000_001},
        {"max_total_bytes": 100_000_001},
        {"timeout_seconds": 31},
        {"retries": 1},
    ):
        with pytest.raises(ValueError, match=r"whitelist|limit|retries"):
            client(tmp_path, **overrides)


def test_refuses_preexisting_project_data(tmp_path, monkeypatch):
    (tmp_path / "projects/b3-fresh-test/shared/data").mkdir(parents=True)
    calls = network(monkeypatch)
    with pytest.raises(PermissionError, match="existing"):
        client(tmp_path).acquire([DATASET])
    assert not calls


def test_registers_real_payload_with_hash_and_never_replays(tmp_path, monkeypatch):
    calls = network(monkeypatch)
    acquisition = client(tmp_path)
    receipt = acquisition.acquire([DATASET])
    record = receipt["datasets"][DATASET]["record"]
    raw = tmp_path / "projects/b3-fresh-test/shared/data" / record["path"]
    assert raw.read_bytes() == BODY
    assert receipt["http_requests"] == 1
    assert receipt["received_bytes"] == len(BODY)
    provenance = raw.with_name(raw.name + ".provenance.json")
    before = provenance.read_bytes()
    with pytest.raises(FileExistsError, match="already attempted"):
        client(tmp_path).acquire([DATASET])
    assert provenance.read_bytes() == before
    assert len(calls) == 1


def test_failure_is_recorded_without_retry(tmp_path, monkeypatch):
    calls = network(monkeypatch, exception=TimeoutError("offline test"))
    receipt = client(tmp_path).acquire([DATASET])
    assert receipt["datasets"][DATASET]["status"] == "failed"
    assert len(calls) == 1
    with pytest.raises(FileExistsError):
        client(tmp_path).acquire([DATASET])
    assert len(calls) == 1


def test_whitelist_and_persisted_authorization_cannot_expand(tmp_path, monkeypatch):
    calls = network(monkeypatch)
    with pytest.raises(PermissionError, match="authorized"):
        client(tmp_path).acquire(["silso-cycle-extrema-v2"])
    assert not calls
    client(tmp_path).acquire([DATASET])
    with pytest.raises(PermissionError, match="cannot change"):
        client(tmp_path, max_http_requests=29).acquire([DATASET])


@pytest.mark.parametrize("length", [True, False])
def test_stream_limits_never_read_over_authorized_bytes(tmp_path, monkeypatch, length):
    network(monkeypatch, body=b"x" * 30, length=length)
    receipt = client(tmp_path, max_file_bytes=20, max_total_bytes=20).acquire([DATASET])
    assert receipt["datasets"][DATASET]["status"] == "failed"
    assert receipt["received_bytes"] <= 20
    assert receipt["datasets"][DATASET]["error_type"] == "ValueError"


def test_request_budget_is_aggregate_across_invocations(tmp_path, monkeypatch):
    ids = [DATASET, "noaa-data-policy-v1"]
    calls = network(monkeypatch)
    acq = client(tmp_path, dataset_ids=ids, max_http_requests=1)
    assert acq.acquire([DATASET])["datasets"][DATASET]["status"] == "registered"
    receipt = acq.acquire([ids[1]])
    assert receipt["datasets"][ids[1]]["status"] == "failed"
    assert receipt["http_requests"] == 1
    assert len(calls) == 1


def test_redirect_to_unapproved_host_is_rejected_without_follow(tmp_path, monkeypatch):
    error = urllib.error.HTTPError(
        CURATED_SOLAR_SOURCES[DATASET][0],
        302,
        "redirect",
        {"Location": "http://127.0.0.1/private"},
        None,
    )
    calls = network(monkeypatch, exception=error)
    receipt = client(tmp_path).acquire([DATASET])
    assert receipt["datasets"][DATASET]["error_type"] == "PermissionError"
    assert len(calls) == 1


def test_native_registry_exposes_separate_opt_in_bundle():
    assert "download_curated_solar_data" in get_builtin_tool_registry()
    assert {tool.name for tool in get_tool_bundles()["solar-acquisition"]} == {
        "download_curated_solar_data",
        "list_curated_solar_sources",
        "register_retained_curated_solar_data",
    }


def test_native_tool_returns_inspectable_receipt(tmp_path, monkeypatch):
    network(monkeypatch)
    settings = {
        "base_workspace": str(tmp_path),
        "project_id": "b3-fresh-test",
        **authorization(),
    }
    receipt = json.loads(
        download_curated_solar_data.func(
            [DATASET], {"configurable": {"curated_solar_acquisition": settings}}
        )
    )
    assert receipt["datasets"][DATASET]["status"] == "registered"


def test_isolated_base_rejects_legacy_mirror_before_network(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(PermissionError, match="legacy"):
        client(tmp_path)


def test_policy_pages_remain_references_outside_observation_catalog(
    tmp_path, monkeypatch
):
    policy = "silso-data-policy-v1"
    network(monkeypatch, body=b"<html>source terms</html>")
    receipt = client(tmp_path, dataset_ids=[policy]).acquire([policy])
    assert receipt["datasets"][policy]["status"] == "reference_retained"
    assert not (
        tmp_path / "projects/b3-fresh-test/shared/project_data_catalog.json"
    ).exists()


def test_retained_hemisphere_repair_preserves_failure_without_http(
    tmp_path, monkeypatch
):
    import jw.solar_acquisition as acquisition_module

    dataset = "silso-monthly-hemispheric-v2"
    rows = [
        f"{1992 + i // 12} {i % 12 + 1} 0 3 1 2 1 1 1 1 -1 -1"
        + (" *" if i > 300 else "")
        for i in range(330)
    ]
    calls = network(monkeypatch, body=("\n".join(rows) + "\n").encode())
    original = acquisition_module.profile_solar_payload

    def fail_validation(*_args):
        raise ValueError("initial schema rejected optional flag")

    monkeypatch.setattr(acquisition_module, "profile_solar_payload", fail_validation)
    acq = client(tmp_path, dataset_ids=[dataset])
    assert acq.acquire([dataset])["datasets"][dataset]["status"] == "failed"
    monkeypatch.setattr(acquisition_module, "profile_solar_payload", original)
    receipt = acq.register_retained([dataset])
    assert receipt["new_http_requests"] == 0
    assert len(calls) == 1
    assert receipt["records"][0]["quality_profile"]["row_count"] == 330
    assert receipt["records"][0]["quality_profile"]["missing_by_column"]["11"] == 330
    ledger = json.loads(acq.state_path.read_text())
    assert ledger["datasets"][dataset]["status"] == "failed"
    assert ledger["registration_repairs"][dataset]["status"] == "registered"


def test_existing_destination_rejected_before_http_even_with_existing_ledger(
    tmp_path, monkeypatch
):
    ids = [DATASET, "silso-monthly-hemispheric-v2"]
    calls = network(monkeypatch)
    acq = client(tmp_path, dataset_ids=ids)
    acq.acquire([DATASET])
    dataset = ids[1]
    filename = CURATED_SOLAR_SOURCES[dataset][1]
    destination = (
        acq.root
        / f"shared/data/curated/{acq.authorization['acquisition_id']}/{dataset}/{filename}"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"immutable staged observation")
    with pytest.raises(FileExistsError, match="already exists"):
        acq.acquire([dataset])
    assert len(calls) == 1
    assert destination.read_bytes() == b"immutable staged observation"


def test_wso_partial_gaps_are_not_counted_as_missing_in_every_column():
    from jw.solar_acquisition import profile_solar_payload

    profile = profile_solar_payload(
        DATASET, b"2026:01:09_21h:07m:13s XXXN -2S 1Avg 20nhz filt: 1Nf -2Sf 1Avgf\n"
    )
    assert profile["missing_rows"] == 1
    assert profile["missing_by_column"]["north"] == 1
    assert profile["missing_by_column"]["south"] == 0
