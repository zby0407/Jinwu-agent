"""Synthetic-only tests: no network, historical preparation, model or plots."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from jw.flare_forecast._frozen import available24_live as live

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 16, 30, tzinfo=UTC)
VERSIONS = {
    "BLD_VERS": ["v10.0.5"],
    "CALVER64": ["0x0000000010042012"],
    "CODEVER7": ["literal-known-sharp-v1.39"],
}


def schema():
    return {
        "status": 0,
        "keywords": [
            {
                "name": field,
                "type": "string",
                "units": "degree" if field == "LON_FWT" else "literal-unit",
                "note": "Stonyhurst longitude of flux-weighted center of active pixels"
                if field == "LON_FWT"
                else "definition-" + field,
            }
            for field in live.REQUIRED
        ],
    }


def row(harp="1", nominal="2026.10.12_16:00:00_TAI", **overrides):
    observed = live.tai_utc(nominal)
    result = {field: "1" for field in live.REQUIRED}
    result.update(
        {
            "HARPNUM": harp,
            "T_REC": nominal,
            "T_OBS": nominal,
            "DATE": live.utc_stamp(observed + timedelta(minutes=16)),
            "LON_FWT": "10",
            "QUALITY": "0x00000400",
            "QUAL_S": "0x00000400",
            "QUALLEV1": "0x40000000",
            "ARM_QUAL": "0x00000000",
            **{field: values[0] for field, values in VERSIONS.items()},
            **overrides,
        }
    )
    return result


def payload(rows):
    return {
        "status": 0,
        "count": len(rows),
        "keywords": [
            {"name": field, "values": [item[field] for item in rows]}
            for field in live.REQUIRED
        ],
    }


@pytest.fixture
def contract(tmp_path):
    baseline, proof = tmp_path / "schema.json", tmp_path / "warning-proof.txt"
    live.write_json(baseline, schema())
    proof.write_text(
        "Synthetic warning-policy proof, not source verification.", encoding="utf-8"
    )
    source = {
        "series": live.SERIES,
        "endpoint": live.ENDPOINT,
        **deepcopy(live.LIMITS),
        "schema_literal_baseline_path": str(baseline),
        "schema_literal_baseline_sha256": live.sha(baseline),
        "known_version_literals": deepcopy(VERSIONS),
        "source_policy_proofs": {
            "warning": {"path": str(proof), "sha256": live.sha(proof)}
        },
    }
    path = tmp_path / "contract.json"
    live.write_json(
        path, {"live_source": source, "authorship": "synthetic engineering fixture"}
    )
    return path


class Response:
    def __init__(self, url, body, status=200, headers=None):
        self.url, self.status_code, self.headers = url, status, headers or {}
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self):
        self.closed = True


def transport_for(responses):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        next_response = responses[len(calls) - 1]
        if isinstance(next_response, BaseException):
            raise next_response
        return (
            next_response(url)
            if callable(next_response)
            else Response(url, next_response)
        )

    return transport, calls


def qualification(rows, contract, when=NOW):
    source = live.load_contract(contract)["live_source"]
    return live.qualify_groups(
        rows,
        live.utc_stamp(when),
        live.utc_stamp(when - timedelta(hours=12)),
        live.utc_stamp(when),
        source,
    )


def test_tai_utc_observation_and_processing_are_distinct():
    assert live.tai_utc("2026.10.12_16:00:00_TAI") == datetime(
        2026, 10, 12, 15, 59, 23, tzinfo=UTC
    )
    assert (
        live.tai_stamp(datetime(2026, 10, 12, 16, tzinfo=UTC))
        == "2026.10.12_16:00:37_TAI"
    )
    with pytest.raises(ValueError):
        live.tai_utc("2026-10-12T16:00:00Z")
    with pytest.raises(ValueError):
        live.date_utc("2026.10.12_16:00:00_TAI")


def test_latest_complete_group_aggregates_every_central_patch(contract):
    rows = [
        row("1", USFLUX="2"),
        row("2", USFLUX="8", LON_FWT="-60"),
        row("3", LON_FWT="61", QUALITY="0"),
    ]
    result, sample = qualification(rows, contract)
    assert result["status"] == "eligible"
    assert sample["patch_count"] == 2
    assert sample["USFLUX_max"] == 8
    assert sample["USFLUX_median"] == 5
    assert sample["observation_utc"] == "2026-10-12T15:59:23Z"
    assert sample["hmi_latest_support_end_utc"] == "2026-10-12T16:10:38Z"
    assert sample["hmi_latest_processing_utc"] == "2026-10-12T16:15:23Z"
    assert sample["history_baseline_status"] == "unknown_not_issued"
    assert not any(key.startswith("history_24h") for key in sample)
    assert result["quality_raw_histograms"]["QUALLEV1"] == {"0x40000000": 2}
    assert result["scientific_acceptance"] == "not_reviewed"


def test_selects_older_qualified_group_without_mixing_or_partial_deletion(contract):
    rows = [
        row("1", "2026.10.12_16:12:00_TAI"),
        row("2", "2026.10.12_16:12:00_TAI", QUALITY="0x00000401"),
        row("9", "2026.10.12_16:00:00_TAI", USFLUX="9"),
    ]
    result, sample = qualification(rows, contract)
    assert sample["hmi_nominal_tai"] == "2026.10.12_16:00:00_TAI"
    assert sample["patch_count"] == 1
    assert result["groups"][0]["status"] == "rejected"
    assert result["groups"][0]["candidate_central_patch_count"] == 2
    assert (
        "central_patch_rejected_no_partial_patch_aggregation"
        in result["groups"][0]["reasons"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"QUALITY": "0"},
        {"QUALITY": "1025"},
        {"QUALITY": "bad"},
        {"QUAL_S": "bad"},
        {"QUALLEV1": "bad"},
        {"ARM_QUAL": "bad"},
        {"ARM_QUAL": "0x80000000"},
        {"USFLUX": "nan"},
        {"TOTPOT": "-1"},
        {"BLD_VERS": "new-version"},
        {"CODEVER7": "new-code"},
        {"CALVER64": "unverified"},
        {"T_OBS": "bad"},
        {"DATE": "2026-10-12T15:00:00Z"},
        {"DATE": "2026-10-12T16:31:00Z"},
        {"HARPNUM": "0"},
    ],
)
def test_one_rejected_central_patch_invalidates_entire_group(contract, change):
    result, sample = qualification([row("1"), row("2", **change)], contract)
    assert result["status"] == "unknown"
    assert sample is None


@pytest.mark.parametrize("geometry", ["nan", "bad", "181"])
def test_unknown_geometry_rejects_group_even_central_membership_unknown(
    contract, geometry
):
    result, sample = qualification([row("1"), row("2", LON_FWT=geometry)], contract)
    assert sample is None
    assert "unknown_geometry_rejects_entire_group" in result["groups"][0]["reasons"]


def test_duplicate_even_outside_central_domain_rejects_group(contract):
    result, sample = qualification(
        [row("1"), row("2", LON_FWT="61"), row("2", LON_FWT="61")], contract
    )
    assert sample is None
    assert "duplicate_HARP_rejects_entire_group" in result["groups"][0]["reasons"]


def test_empty_and_no_central_rows_are_unknown_not_zero(contract):
    assert qualification([], contract)[1] is None
    result, sample = qualification([row(LON_FWT="61")], contract)
    assert sample is None and result["status"] == "unknown"


def test_three_hour_inclusive_boundary_and_one_second_later(contract):
    observed = live.tai_utc("2026.10.12_16:00:00_TAI")
    assert (
        qualification([row()], contract, observed + timedelta(hours=3))[1] is not None
    )
    assert (
        qualification([row()], contract, observed + timedelta(hours=3, seconds=1))[1]
        is None
    )


def test_support_not_completed_rejects_even_processing_done(contract):
    observed = live.tai_utc("2026.10.12_16:00:00_TAI")
    result, sample = qualification(
        [row(DATE=live.utc_stamp(observed + timedelta(seconds=10)))],
        contract,
        observed + timedelta(seconds=674),
    )
    assert sample is None
    assert (
        "input_not_available_at_retrieval" in result["groups"][0]["rows"][0]["reasons"]
    )


def test_old_snapshot_and_illegal_nominal_time_cannot_fallback(contract):
    with pytest.raises(ValueError, match="outside_requested_interval"):
        qualification([row(nominal="2026.10.11_13:00:00_TAI")], contract)
    with pytest.raises(ValueError, match="TAI"):
        qualification([row(T_REC="unknown")], contract)


def test_schema_units_and_version_definition_literal_drift_rejected(contract):
    source = live.load_contract(contract)["live_source"]
    for field in ("TOTPOT", "BLD_VERS", "CALVER64", "CODEVER7", "QUALITY"):
        changed = schema()
        next(item for item in changed["keywords"] if item["name"] == field)["units"] = (
            "new-units"
        )
        with pytest.raises(ValueError, match="literal_drift"):
            live.validate_schema(changed, source)


def test_fetch_performs_exactly_two_bounded_queries_and_archives_proofs(
    contract, tmp_path
):
    transport, calls = transport_for([schema(), payload([row()])])
    output = tmp_path / "out"
    result = live.fetch(contract, output, transport, lambda: NOW)
    assert result["status"] == "eligible"
    assert len(calls) == 2 and all(timeout == 30 for _, timeout in calls)
    query0, query1 = [parse_qs(urlsplit(url).query) for url, _ in calls]
    assert query0 == {"op": ["series_struct"], "ds": [live.SERIES]}
    assert query1["n"] == ["0"] and query1["op"] == ["rs_list"]
    assert query1["ds"][0].startswith(live.SERIES + "[][")
    assert (
        query1["ds"][0]
        == live.SERIES + "[][2026.10.12_04:36:00_TAI-2026.10.12_16:24:00_TAI@12m]"
    )
    assert "segment" not in query1 and "FITS" not in query1["ds"][0]
    manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["HTTP"]["retry_count"] == 0
    assert manifest["future_outcome_read"] is False
    assert result["future_outcome_read"] is False
    for entry in manifest["files"]:
        assert entry["sha256"] == live.sha(output / entry["path"])
    audit = json.loads((output / "row_audit.json").read_text(encoding="utf-8"))
    assert audit["groups"][0]["rows"][0]["raw_record"] == row()
    assert not result["forecast_issued"] and not result["fit_predict_plot_performed"]
    with pytest.raises(ValueError, match="existing_output"):
        live.fetch(contract, output, transport, lambda: NOW)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "bad_response",
    [
        TimeoutError("synthetic timeout"),
        lambda url: Response(url, {}, status=302),
        lambda url: Response(url + "&redirect=1", {}),
        lambda url: Response(url, b"x" * (2 * 1024 * 1024 + 1)),
        lambda url: Response(
            url, b"", headers={"Content-Length": str(2 * 1024 * 1024 + 1)}
        ),
        {"status": 1},
        b"not-json",
    ],
)
def test_fetch_failure_has_no_retry_or_old_input_fallback(
    contract, tmp_path, bad_response
):
    transport, calls = transport_for([bad_response])
    output = tmp_path / "out"
    result = live.fetch(contract, output, transport, lambda: NOW)
    assert result["status"] == "failed" and len(calls) == 1
    assert not (output / "live_physical.csv").exists()
    receipt = json.loads((output / "source_requests.json").read_text(encoding="utf-8"))[
        "requests"
    ][0]
    assert receipt["status"] == "failed" and receipt["retry_count"] == 0
    assert receipt["sha256"] == live.sha(output / receipt["raw_path"])
    assert receipt["bytes"] <= 2 * 1024 * 1024


def test_invalid_keyword_array_or_source_error_rejected():
    data = payload([row()])
    data["keywords"][0]["values"] = []
    with pytest.raises(ValueError, match="misaligned"):
        live.decode_keywords(data)
    with pytest.raises(ValueError, match="source_error"):
        live.decode_keywords({"status": 1})


def test_contract_limits_proof_and_baseline_cannot_be_relaxed(contract):
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["live_source"]["HTTP_retries"] = 1
    live.write_json(contract, data)
    with pytest.raises(ValueError, match="limits_changed"):
        live.load_contract(contract)


def test_changed_warning_proof_or_baseline_rejected(contract):
    data = json.loads(contract.read_text(encoding="utf-8"))
    Path(data["live_source"]["schema_literal_baseline_path"]).write_text(
        "changed", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="baseline_hash"):
        live.load_contract(contract)


def test_keyword_timeout_stops_after_second_attempt_with_receipt(contract, tmp_path):
    transport, calls = transport_for([schema(), TimeoutError("synthetic read timeout")])
    output = tmp_path / "out"
    result = live.fetch(contract, output, transport, lambda: NOW)
    assert len(calls) == 2 and result["status"] == "failed"
    receipts = json.loads(
        (output / "source_requests.json").read_text(encoding="utf-8")
    )["requests"]
    assert receipts[0]["status"] == "success"
    assert receipts[1]["status"] == "failed"
    assert receipts[1]["error_class"] == "TimeoutError"
    assert not (output / "live_physical.csv").exists()


def test_second_payload_cap_leaves_both_raw_receipts(contract, tmp_path):
    transport, calls = transport_for(
        [schema(), lambda url: Response(url, b"x" * (2 * 1024 * 1024 + 1))]
    )
    output = tmp_path / "out"
    result = live.fetch(contract, output, transport, lambda: NOW)
    assert result["status"] == "failed" and len(calls) == 2
    receipts = json.loads(
        (output / "source_requests.json").read_text(encoding="utf-8")
    )["requests"]
    assert receipts[1]["truncated_at_registered_byte_cap"]
    assert sum(receipt["bytes"] for receipt in receipts) <= 4 * 1024 * 1024
    assert all(
        receipt["sha256"] == live.sha(output / receipt["raw_path"])
        for receipt in receipts
    )


def test_changed_frozen_proof_during_fetch_invalidates_output(contract, tmp_path):
    data = json.loads(contract.read_text(encoding="utf-8"))
    proof_path = Path(data["live_source"]["source_policy_proofs"]["warning"]["path"])

    def changed_proof(url):
        proof_path.write_text("tampered", encoding="utf-8")
        return Response(url, payload([row()]))

    transport, _ = transport_for([schema(), changed_proof])
    result = live.fetch(contract, tmp_path / "out", transport, lambda: NOW)
    assert result["status"] == "failed"
    assert result["live_physical_csv_eligible"] is False


def test_native_transport_single_connection_no_retry_and_decreasing_deadline(
    monkeypatch,
):
    class Socket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class HTTPResponse:
        status = 200
        fp = None

        def __init__(self):
            self.reads = iter([b"part", b""])
            self.closed = False

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def read1(self, size):
            return next(self.reads)

        def close(self):
            self.closed = True

    connection_calls = []

    class Connection:
        def __init__(self, host, timeout):
            assert host == "jsoc.stanford.edu" and timeout == 30
            self.sock, self.response = Socket(), HTTPResponse()
            self.connects, self.requests = 0, []
            connection_calls.append(self)

        def connect(self):
            self.connects += 1

        def request(self, method, path, headers):
            self.requests.append((method, path, headers))

        def getresponse(self):
            return self.response

        def close(self):
            pass

    ticks = iter([100, 101, 102, 103, 104])
    monkeypatch.setattr(live.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(live.http.client, "HTTPConnection", Connection)
    response = live.request_once(live.ENDPOINT + "?op=series_struct", 30)
    assert list(response.iter_content(16384)) == [b"part"]
    response.close()
    conn = connection_calls[0]
    assert conn.connects == 1 and len(conn.requests) == 1
    assert conn.sock.timeouts == [29, 28, 27, 26]
    assert conn.response.closed


def test_native_transport_total_deadline_rejects_before_next_read(monkeypatch):
    response = object.__new__(live.BoundedHTTPResponse)
    response.deadline = 100
    monkeypatch.setattr(live.time, "monotonic", lambda: 100)
    with pytest.raises(TimeoutError, match="deadline"):
        response._set_timeout()


def test_query_native_grid_contains_every_720_second_slot_in_12h():
    query = live.live_query(NOW)
    first = live.tai_utc(query["first_native_slot_tai"])
    last = live.tai_utc(query["last_native_slot_tai"])
    assert NOW - timedelta(hours=12) <= first <= last <= NOW
    assert first - timedelta(seconds=720) < NOW - timedelta(hours=12)
    assert last + timedelta(seconds=720) > NOW
    slots = [first + timedelta(seconds=720 * index) for index in range(60)]
    assert slots[-1] == last
    assert all(NOW - timedelta(hours=12) <= slot <= NOW for slot in slots)
    assert query["HARP_selection"] == "all_HARP_empty_first_prime_key"


def test_query_midnight_wrap_retains_tai_utc_offset_and_all_harp():
    now = datetime(2026, 10, 13, 0, 0, 1, tzinfo=UTC)
    query = live.live_query(now)
    assert query["first_native_slot_tai"] == "2026.10.12_12:12:00_TAI"
    assert query["last_native_slot_tai"] == "2026.10.13_00:00:00_TAI"
    assert query["last_native_slot_utc"] == "2026-10-12T23:59:23Z"
    assert query["parameters"]["ds"].startswith(live.SERIES + "[][")


@pytest.mark.parametrize("word", ["0x80000000", "0x80000400", "-2147483648"])
def test_secondary_highbit_is_conservatively_pending_review_not_reinterpreted(
    contract, word
):
    result, sample = qualification([row(QUAL_S=word)], contract)
    assert sample is None
    audit = result["groups"][0]["rows"][0]
    assert audit["raw_record"]["QUAL_S"] == word
    assert "secondary_highbit_pending_source_review_QUAL_S" in audit["reasons"]


def test_live_secondary_policy_must_be_explicit_and_frozen(contract):
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["live_source"]["secondary_highbit_policy"] = "assume_harmless"
    live.write_json(contract, data)
    with pytest.raises(ValueError, match="limits_changed"):
        live.load_contract(contract)


def test_live_source_products_consumed_by_issue_interface_without_rewrite(
    contract, tmp_path
):
    from jw.flare_forecast._frozen import available24_model as model

    formal_path = Path(live.__file__).resolve().parents[1] / "contract_template.json"
    if not formal_path.exists():
        pytest.skip(
            "Frozen research contract is required for this engineering interface test"
        )
    protocol = json.loads(formal_path.read_text(encoding="utf-8-sig"))
    synthetic = json.loads(contract.read_text(encoding="utf-8"))
    protocol["live_source"] = synthetic["live_source"]
    protocol["secondary_highbit_policy"] = "reject_pending_source_review"
    protocol["engineering_fixture_only"] = True
    live.write_json(contract, protocol)
    transport, calls = transport_for([schema(), payload([row()])])
    directory = tmp_path / "interface_fake_source"
    result = live.fetch(contract, directory, transport, lambda: NOW)
    assert result["status"] == "eligible" and len(calls) == 2
    csv_hash = live.sha(directory / "live_physical.csv")
    checked_contract, manifest, before, frame, manifest_path = model.checked_live(
        directory, contract
    )
    model.validate_physical(frame, checked_contract, live=True)
    model.validate_live_at_issue(frame, NOW + timedelta(seconds=1))
    assert manifest_path.name == "source_manifest.json"
    assert manifest["future_outcome_read"] is False
    assert frame.iloc[0].oldest_patch_observation_utc == "2026-10-12T15:59:23Z"
    assert live.sha(directory / "live_physical.csv") == csv_hash
    model.verify_unchanged(before)
