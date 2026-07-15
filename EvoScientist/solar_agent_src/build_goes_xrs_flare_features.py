from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "goes_xrs_legacy"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"
EVENT_OUTPUT = INTERIM_DIR / "goes_xrs_events_interim.csv"
MONTHLY_OUTPUT = PROCESSED_DIR / "goes_xrs_monthly_features.csv"
CYCLE_FLARE_OUTPUT = PROCESSED_DIR / "cycle_flare_features.csv"
LEGACY_COVERAGE_START = pd.Timestamp("1975-09-01")
LEGACY_COVERAGE_END = pd.Timestamp("2017-06-01")

CLASS_BASE_FLUX = {
    "A": 1e-8,
    "B": 1e-7,
    "C": 1e-6,
    "M": 1e-5,
    "X": 1e-4,
}


def selected_report_files() -> list[Path]:
    files = []
    for year in range(1975, 2018):
        if year == 2015 and (RAW_DIR / "goes-xrs-report_2015_modifiedreplacedmissingrows.txt").exists():
            files.append(RAW_DIR / "goes-xrs-report_2015_modifiedreplacedmissingrows.txt")
        elif year == 2017 and (RAW_DIR / "goes-xrs-report_2017-ytd.txt").exists():
            files.append(RAW_DIR / "goes-xrs-report_2017-ytd.txt")
        else:
            candidate = RAW_DIR / f"goes-xrs-report_{year}.txt"
            if candidate.exists():
                files.append(candidate)
    return files


def source_year(path: Path) -> int | None:
    match = re.search(r"(19|20)\d{2}", path.name)
    return int(match.group(0)) if match else None


def parse_event_date(token: str) -> pd.Timestamp | pd.NaT:
    match = re.match(r"^31777(?P<ymd>\d{6})", token)
    if not match:
        return pd.NaT
    ymd = match.group("ymd")
    yy = int(ymd[:2])
    year = 1900 + yy if yy >= 70 else 2000 + yy
    try:
        return pd.Timestamp(year=year, month=int(ymd[2:4]), day=int(ymd[4:6]))
    except ValueError:
        return pd.NaT


def clean_time_token(token: str) -> tuple[str | None, bool]:
    digits = re.findall(r"\d", token or "")
    has_quality_letters = bool(re.search(r"[A-Za-z]", token or ""))
    if len(digits) < 4:
        return None, has_quality_letters
    value = "".join(digits[:4])
    hour = int(value[:2])
    minute = int(value[2:])
    if hour > 23 or minute > 59:
        return None, True
    return f"{hour:02d}:{minute:02d}", has_quality_letters


def time_to_datetime(event_date: pd.Timestamp, time_value: str | None) -> datetime | None:
    if pd.isna(event_date) or not time_value:
        return None
    hour, minute = [int(part) for part in time_value.split(":")]
    return datetime(event_date.year, event_date.month, event_date.day, hour, minute)


def duration_minutes(event_date: pd.Timestamp, start_time: str | None, end_time: str | None) -> float:
    start_dt = time_to_datetime(event_date, start_time)
    end_dt = time_to_datetime(event_date, end_time)
    if start_dt is None or end_dt is None:
        return np.nan
    if end_dt < start_dt:
        end_dt += timedelta(days=1)
    return round((end_dt - start_dt).total_seconds() / 60, 2)


def parse_class(line: str) -> tuple[str | None, str | None, float, float, str]:
    match = re.search(r"\s(?P<letter>[ABCMX])\s*(?P<value>\d+(?:\.\d+)?)\s+(?P<sat>GOES|G\d+)\b", line)
    if not match:
        return None, None, np.nan, np.nan, "missing"
    letter = match.group("letter")
    raw_value = match.group("value")
    if "." in raw_value:
        class_value = float(raw_value)
    elif len(raw_value) >= 2:
        class_value = float(raw_value) / 10.0
    else:
        class_value = float(raw_value)
    peak_flux = CLASS_BASE_FLUX.get(letter, np.nan) * class_value
    return f"{letter}{class_value:.1f}", letter, class_value, peak_flux, "ok"


def parse_location(line: str) -> tuple[str | None, float, float, str, bool, str]:
    search_region = line[20:58]
    match = re.search(r"(?P<lat_dir>[NS])(?P<lat>\d{2})(?P<lon_dir>[EW])(?P<lon>\d{2})", search_region)
    if not match:
        return None, np.nan, np.nan, "unknown", False, "missing"
    lat = int(match.group("lat"))
    lon = int(match.group("lon"))
    latitude = lat if match.group("lat_dir") == "N" else -lat
    longitude = lon if match.group("lon_dir") == "W" else -lon
    hemisphere = "north" if latitude > 0 else "south" if latitude < 0 else "unknown"
    abs_lon = abs(longitude)
    if abs_lon <= 30:
        quality = "disk_center"
    elif abs_lon <= 60:
        quality = "mid_disk"
    else:
        quality = "limb"
    return match.group(0), float(latitude), float(longitude), hemisphere, abs_lon > 60, quality


def parse_active_region(line: str) -> tuple[str | None, bool]:
    class_match = re.search(r"\s[ABCMX]\s*\d+(?:\.\d+)?\s+(?:GOES|G\d+)\b(?P<tail>.*)$", line)
    if not class_match:
        return None, False
    tail_tokens = class_match.group("tail").split()
    for token in tail_tokens:
        if re.fullmatch(r"\d{3,5}", token):
            return token, True
    return None, False


def parse_time_fields(line: str) -> tuple[str | None, str | None, str | None, str]:
    start_chunk = line[13:18].strip()
    end_chunk = line[18:23].strip()
    peak_chunk = line[23:29].strip()
    # Some legacy rows merge start and end, for example 1512E1519.
    merged = re.match(r"^(?P<start>\d{4})[A-Za-z](?P<end>\d{4})$", start_chunk)
    if merged and not re.search(r"\d{4}", end_chunk):
        start_chunk = merged.group("start")
        end_chunk = merged.group("end")
    start_time, start_quality = clean_time_token(start_chunk)
    end_time, end_quality = clean_time_token(end_chunk)
    peak_time, peak_quality = clean_time_token(peak_chunk)

    missing_count = sum(value is None for value in [start_time, end_time, peak_time])
    if missing_count == 0 and not (start_quality or end_quality or peak_quality):
        flag = "ok"
    elif missing_count == 0:
        flag = "legacy_uncertain"
    elif missing_count < 3:
        flag = "partial"
    else:
        flag = "missing"
    return start_time, peak_time, end_time, flag


def parse_line(line: str, path: Path, raw_line_no: int, sequence: int) -> dict[str, object]:
    src_year = source_year(path)
    tokens = line.split()
    date_token = tokens[0] if tokens else ""
    event_date = parse_event_date(date_token)
    date_ok = not pd.isna(event_date)
    start_time, peak_time, end_time, time_quality_flag = parse_time_fields(line)
    xray_class_full, xray_class_letter, xray_class_value, xray_peak_flux_proxy, class_quality_flag = parse_class(line)
    location_raw, latitude_deg, longitude_deg, hemisphere, limb_flag, position_quality_flag = parse_location(line)
    noaa_active_region, has_active_region = parse_active_region(line)
    parse_status = "ok" if date_ok and class_quality_flag == "ok" else "partial"
    if not date_ok:
        parse_status = "failed"

    event_date_str = event_date.strftime("%Y-%m-%d") if date_ok else None
    date_month = event_date.strftime("%Y-%m-01") if date_ok else None
    source_year_text = str(src_year) if src_year is not None else "unknown"
    event_id = f"goes_xrs_{source_year_text}_{raw_line_no:05d}_{sequence:06d}"
    return {
        "event_id": event_id,
        "source_file": path.name,
        "source_year": src_year,
        "raw_line_no": raw_line_no,
        "event_date": event_date_str,
        "date_month": date_month,
        "start_time": start_time,
        "peak_time": peak_time,
        "end_time": end_time,
        "duration_min": duration_minutes(event_date, start_time, end_time) if date_ok else np.nan,
        "xray_class_full": xray_class_full,
        "xray_class_letter": xray_class_letter,
        "xray_class_value": xray_class_value,
        "xray_peak_flux_proxy": xray_peak_flux_proxy,
        "major_flare_flag": xray_class_letter in {"M", "X"},
        "x_flare_flag": xray_class_letter == "X",
        "location_raw": location_raw,
        "latitude_deg": latitude_deg,
        "longitude_deg": longitude_deg,
        "hemisphere": hemisphere,
        "limb_flag": limb_flag,
        "noaa_active_region": noaa_active_region,
        "has_active_region": has_active_region,
        "parse_status": parse_status,
        "time_quality_flag": time_quality_flag,
        "position_quality_flag": position_quality_flag,
        "class_quality_flag": class_quality_flag,
    }


def build_events() -> pd.DataFrame:
    rows = []
    sequence = 0
    for path in selected_report_files():
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line_no, line in enumerate(handle, start=1):
                stripped = line.rstrip("\n\r")
                if not stripped.strip() or not stripped.startswith("31777"):
                    continue
                sequence += 1
                rows.append(parse_line(stripped, path, raw_line_no, sequence))
    columns = [
        "event_id",
        "source_file",
        "source_year",
        "raw_line_no",
        "event_date",
        "date_month",
        "start_time",
        "peak_time",
        "end_time",
        "duration_min",
        "xray_class_full",
        "xray_class_letter",
        "xray_class_value",
        "xray_peak_flux_proxy",
        "major_flare_flag",
        "x_flare_flag",
        "location_raw",
        "latitude_deg",
        "longitude_deg",
        "hemisphere",
        "limb_flag",
        "noaa_active_region",
        "has_active_region",
        "parse_status",
        "time_quality_flag",
        "position_quality_flag",
        "class_quality_flag",
    ]
    return pd.DataFrame(rows, columns=columns)


def monthly_quality_flag(group: pd.DataFrame) -> str:
    if group.empty or group["parse_status"].eq("failed").all():
        return "missing"
    partial_share = (
        group["parse_status"].ne("ok")
        | group["time_quality_flag"].ne("ok")
        | group["class_quality_flag"].ne("ok")
    ).mean()
    position_missing_share = group["position_quality_flag"].isin(["missing", "invalid"]).mean()
    if partial_share > 0.25:
        return "partial_parse"
    if position_missing_share > 0.5:
        return "limited_position"
    return "ok"


def build_monthly(events: pd.DataFrame) -> pd.DataFrame:
    usable = events[events["event_date"].notna()].copy()
    usable["event_date"] = pd.to_datetime(usable["event_date"])
    usable["date_month"] = pd.to_datetime(usable["date_month"])
    usable["valid_position"] = usable["hemisphere"].isin(["north", "south"])
    grouped = dict(tuple(usable.groupby("date_month", sort=True)))
    monthly_rows = []
    for date_month in pd.date_range(LEGACY_COVERAGE_START, LEGACY_COVERAGE_END, freq="MS"):
        group = grouped.get(date_month, usable.iloc[0:0])
        north = int(group["hemisphere"].eq("north").sum())
        south = int(group["hemisphere"].eq("south").sum())
        unknown_hemi = int(group["hemisphere"].eq("unknown").sum())
        denom = north + south
        total = int(len(group))
        position_valid_count = north + south
        observed_zero_event = total == 0
        monthly_rows.append(
            {
                "date_month": date_month.strftime("%Y-%m-%d"),
                "flare_count_total": total,
                "flare_count_a": int(group["xray_class_letter"].eq("A").sum()),
                "flare_count_b": int(group["xray_class_letter"].eq("B").sum()),
                "flare_count_c": int(group["xray_class_letter"].eq("C").sum()),
                "flare_count_m": int(group["xray_class_letter"].eq("M").sum()),
                "flare_count_x": int(group["xray_class_letter"].eq("X").sum()),
                "flare_count_unknown": int(group["xray_class_letter"].isna().sum()),
                "flare_count_ge_c": int(group["xray_class_letter"].isin(["C", "M", "X"]).sum()),
                "flare_count_ge_m": int(group["xray_class_letter"].isin(["M", "X"]).sum()),
                "m_x_flare_count": int(group["xray_class_letter"].isin(["M", "X"]).sum()),
                "xray_peak_flux_sum_proxy": group["xray_peak_flux_proxy"].sum(skipna=True),
                "xray_peak_flux_max_proxy": group["xray_peak_flux_proxy"].max(skipna=True),
                "flare_days_count": int(group["event_date"].dt.date.nunique()) if total else 0,
                "active_region_count": int(group.loc[group["has_active_region"], "noaa_active_region"].nunique()),
                "flare_north_count": north,
                "flare_south_count": south,
                "flare_hemispheric_asymmetry": ((north - south) / denom) if denom else np.nan,
                "position_valid_count": position_valid_count,
                "position_valid_rate": (position_valid_count / total) if total else np.nan,
                "hemisphere_unknown_count": unknown_hemi,
                "limb_flare_share": group["limb_flag"].mean() if total else np.nan,
                "flare_parse_ok_rate": group["parse_status"].eq("ok").mean() if total else 1.0,
                "flare_time_complete_rate": group["time_quality_flag"].isin(["ok", "legacy_uncertain"]).mean()
                if total
                else 1.0,
                "flare_position_valid_rate": (position_valid_count / total) if total else np.nan,
                "flare_class_valid_rate": group["class_quality_flag"].eq("ok").mean() if total else 1.0,
                "has_flare_data": True,
                "flare_coverage_status": "observed_zero_event" if observed_zero_event else "observed_events",
                "flare_legacy_duration_warning": bool(total and group["time_quality_flag"].ne("ok").any()),
                "flare_data_quality_flag": "observed_zero_event" if observed_zero_event else monthly_quality_flag(group),
                "flare_evidence_tier": "auxiliary",
            }
        )
    return pd.DataFrame(monthly_rows)


def build_cycle_flare_features(monthly: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    phase_cols = ["date_month", "cycle_no", "cycle_phase", "months_to_cycle_peak"]
    if "cycle_phase_windowed" in master.columns:
        phase_cols.append("cycle_phase_windowed")
    merged = master[phase_cols].merge(
        monthly, on="date_month", how="left"
    )
    if "cycle_phase_windowed" not in merged.columns:
        merged["cycle_phase_windowed"] = merged["cycle_phase"]
    merged = merged[merged["cycle_no"].notna()].copy()
    merged["cycle_no"] = merged["cycle_no"].astype(int)
    rows = []
    for cycle_no, group in merged.groupby("cycle_no", sort=True):
        covered = group[group["has_flare_data"].eq(True)].copy()
        if covered.empty:
            rows.append(
                {
                    "cycle_no": cycle_no,
                    "cycle_flare_count_total": np.nan,
                    "cycle_mx_flare_count": np.nan,
                    "cycle_x_flare_count": np.nan,
                    "cycle_flare_flux_sum_proxy": np.nan,
                    "cycle_flare_flux_max_proxy": np.nan,
                    "rise_phase_mx_flare_count": np.nan,
                    "max_phase_mx_flare_count": np.nan,
                    "decline_phase_mx_flare_count": np.nan,
                    "flare_peak_lag_to_sunspot_peak_months": np.nan,
                    "cycle_flare_asymmetry_mean": np.nan,
                    "flare_coverage_months": 0,
                    "flare_cycle_quality_flag": "outside_coverage",
                }
            )
            continue

        monthly_peak = covered.loc[covered["m_x_flare_count"].fillna(0).idxmax()]
        peak_lag = monthly_peak["months_to_cycle_peak"]
        valid_asym = covered["flare_hemispheric_asymmetry"].dropna()
        partial_share = covered["flare_data_quality_flag"].isin(["partial_parse", "limited_position"]).mean()
        if partial_share > 0.5:
            quality = "partial_legacy"
        elif len(covered) < len(group):
            quality = "partial_cycle_coverage"
        else:
            quality = "ok"
        rows.append(
            {
                "cycle_no": cycle_no,
                "cycle_flare_count_total": covered["flare_count_total"].sum(skipna=True),
                "cycle_mx_flare_count": covered["m_x_flare_count"].sum(skipna=True),
                "cycle_x_flare_count": covered["flare_count_x"].sum(skipna=True),
                "cycle_flare_flux_sum_proxy": covered["xray_peak_flux_sum_proxy"].sum(skipna=True),
                "cycle_flare_flux_max_proxy": covered["xray_peak_flux_max_proxy"].max(skipna=True),
                "rise_phase_mx_flare_count": covered.loc[
                    covered["cycle_phase"].eq("rising"), "m_x_flare_count"
                ].sum(skipna=True),
                "max_phase_mx_flare_count": covered.loc[
                    covered["cycle_phase_windowed"].eq("maximum_window"), "m_x_flare_count"
                ].sum(skipna=True),
                "decline_phase_mx_flare_count": covered.loc[
                    covered["cycle_phase"].eq("declining"), "m_x_flare_count"
                ].sum(skipna=True),
                "flare_peak_lag_to_sunspot_peak_months": -peak_lag if pd.notna(peak_lag) else np.nan,
                "cycle_flare_asymmetry_mean": valid_asym.mean() if not valid_asym.empty else np.nan,
                "flare_coverage_months": int(len(covered)),
                "flare_cycle_quality_flag": quality,
            }
        )
    return pd.DataFrame(rows)


def build_monthly_outputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    events = build_events()
    monthly = build_monthly(events)
    events.to_csv(EVENT_OUTPUT, index=False, encoding="utf-8")
    monthly.to_csv(MONTHLY_OUTPUT, index=False, encoding="utf-8")
    print(f"saved {EVENT_OUTPUT}")
    print(f"events={len(events)} range={events['event_date'].min()}..{events['event_date'].max()}")
    print(f"saved {MONTHLY_OUTPUT}")
    print(f"months={len(monthly)} range={monthly['date_month'].min()}..{monthly['date_month'].max()}")
    print(monthly["flare_data_quality_flag"].value_counts().to_string())
    return events, monthly


def build_cycle_outputs() -> pd.DataFrame:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if MONTHLY_OUTPUT.exists():
        monthly = pd.read_csv(MONTHLY_OUTPUT)
    else:
        _, monthly = build_monthly_outputs()
    master_path = PROCESSED_DIR / "clean_monthly_timeseries.csv"
    if not master_path.exists():
        cycle_features = pd.DataFrame()
    else:
        master = pd.read_csv(master_path)
        cycle_features = build_cycle_flare_features(monthly, master)
    cycle_features.to_csv(CYCLE_FLARE_OUTPUT, index=False, encoding="utf-8")
    print(f"saved {CYCLE_FLARE_OUTPUT}")
    return cycle_features


def main() -> None:
    build_monthly_outputs()
    build_cycle_outputs()


if __name__ == "__main__":
    main()
