"""Official monthly solar-series adapter for the scientific-data tool bundle.

Source discovery uses a maintained official catalogue, not a literature search.
Acquisition uses the shared network policy and saves the unmodified responses.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path

from .acquisition import Fetcher, acquire
from .contracts import SourceRole
from .policy import AcquisitionPolicy
from .sources import SourceCatalog

SOURCES = {
    "silso": {
        "url": "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.txt",
        "documentation": "https://www.sidc.be/SILSO/infosnmtot",
        "provider": "WDC-SILSO, Royal Observatory of Belgium",
        "field": "column 4: monthly mean total sunspot number V2.0",
        "unit": "dimensionless",
        "license": "CC BY-NC 4.0",
    },
    "noaa": {
        "url": "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json",
        "documentation": "https://www.swpc.noaa.gov/products/solar-cycle-progression",
        "provider": "NOAA SWPC; F10.7 observations from Penticton, Canada",
        "field": "f10.7: observed monthly mean (not smoothed or predicted)",
        "unit": "sfu (1e-22 W m^-2 Hz^-1)",
        "license": "NOAA public data; retain original source attribution",
    },
}


def _month(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m")
    if parsed.strftime("%Y-%m") != value:
        raise ValueError("Month must use YYYY-MM")
    return parsed.year * 12 + parsed.month - 1


def month_grid(start: str, end: str) -> list[str]:
    first, last = _month(start), _month(end)
    if last < first:
        raise ValueError("End month precedes start month")
    return [
        f"{number // 12:04d}-{number % 12 + 1:02d}" for number in range(first, last + 1)
    ]


def _value(raw: object) -> tuple[float | None, str]:
    if raw is None:
        return None, "source_null"
    value = float(raw)
    if value == -1:
        return None, "source_sentinel"
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid source value: {raw!r}")
    return value, ""


def _insert(records: dict, month: str, raw: object, locator: str) -> int:
    value, reason = _value(raw)
    if month in records:
        if records[month]["value"] != value or records[month]["reason"] != reason:
            raise ValueError(f"Conflicting duplicate month: {month}")
        records[month]["locator"] += ";" + locator
        return 1
    records[month] = {"value": value, "raw": raw, "reason": reason, "locator": locator}
    return 0


def parse_sources(
    silso: Path, noaa: Path, months: list[str]
) -> tuple[dict, dict, dict]:
    selected = set(months)
    sunspots, flux = {}, {}
    duplicates = {"silso": 0, "noaa": 0}
    for line_number, line in enumerate(
        silso.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 6:
            raise ValueError(f"Invalid SILSO row {line_number}")
        month = f"{int(fields[0]):04d}-{int(fields[1]):02d}"
        _month(month)
        if month in selected:
            duplicates["silso"] += _insert(
                sunspots, month, fields[3], f"line:{line_number}"
            )
    payload = json.loads(noaa.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("NOAA response must contain a list of monthly observations")
    for index, row in enumerate(payload):
        if not isinstance(row, dict) or "time-tag" not in row or "f10.7" not in row:
            raise ValueError(f"Invalid NOAA record {index}")
        month = row["time-tag"]
        _month(month)
        if month in selected:
            duplicates["noaa"] += _insert(flux, month, row["f10.7"], f"json:{index}")
    return sunspots, flux, duplicates


def _json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_tables(
    directory: Path, paths: dict[str, Path], start: str, end: str
) -> dict:
    months = month_grid(start, end)
    sunspots, flux, duplicates = parse_sources(paths["silso"], paths["noaa"], months)
    rows = []
    absent = {"value": None, "raw": None, "reason": "month_absent", "locator": ""}
    for month in months:
        sun, radio = sunspots.get(month, absent), flux.get(month, absent)
        rows.append(
            {
                "month": month,
                "sunspot_number": sun["value"],
                "f107_sfu": radio["value"],
                "sunspot_source": SOURCES["silso"]["url"],
                "f107_source": SOURCES["noaa"]["url"],
                "sunspot_raw": sun["raw"],
                "f107_raw": radio["raw"],
                "sunspot_locator": sun["locator"],
                "f107_locator": radio["locator"],
                "sunspot_missing_reason": sun["reason"],
                "f107_missing_reason": radio["reason"],
            }
        )
    missing = {
        key: sum(row[key] is None for row in rows)
        for key in ("sunspot_number", "f107_sfu")
    }
    pairs = [
        row
        for row in rows
        if row["sunspot_number"] is not None and row["f107_sfu"] is not None
    ]
    x = [row["sunspot_number"] for row in pairs]
    y = [row["f107_sfu"] for row in pairs]
    correlation = (
        statistics.correlation(x, y)
        if len(x) > 1 and len(set(x)) > 1 and len(set(y)) > 1
        else None
    )
    quality = {
        "status": "completed_with_missing" if any(missing.values()) else "completed",
        "rows": len(rows),
        "start_month": start,
        "end_month": end,
        "missing": missing,
        "identical_duplicates_removed": duplicates,
        "conflicting_duplicates": 0,
        "pearson": correlation,
        "paired_months": len(pairs),
        "ranges": {
            key: [min(values), max(values)] if values else None
            for key in missing
            for values in [[r[key] for r in rows if r[key] is not None]]
        },
        "interpretation": "Descriptive monthly association only; no causal inference or forecast.",
    }
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "merged_monthly.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _json(directory / "quality_report.json", quality)
    text = (
        f"# 太阳活动月度数据整合\n\n覆盖{start}至{end}，共{len(rows)}个月。\n\n"
        "数据来自WDC-SILSO月均总太阳黑子数和NOAA SWPC观测月均F10.7，未使用平滑值或预测值。"
        "月份作为唯一键，原始文件保留；每个值附有来源网址及行号或JSON记录索引。\n\n"
        f"太阳黑子数缺失{missing['sunspot_number']}个月，F10.7缺失{missing['f107_sfu']}个月。"
        f"完全相同的重复月份合计合并{sum(duplicates.values())}条；冲突值会使处理明确失败。"
        "缺失不插补，来源中的-1转换为空值并标记原因。\n\n"
        f"可配对月份为{len(pairs)}个，Pearson相关系数为{correlation}。"
        "相关性仅作描述，不能据此推断因果。\n\n"
        "## 来源与适用范围\n\n"
        "SILSO太阳黑子数为无量纲指标，使用CC BY-NC 4.0许可；"
        "F10.7单位为sfu（10⁻²² W·m⁻²·Hz⁻¹），由加拿大Penticton观测、NOAA发布。"
        "NOAA接口中的另一列黑子数也引用SIDC，不应与SILSO重复计作独立证据。"
        "本工具仅处理这两个官方月度序列，来源由内置目录定位；不代表开放网页搜索覆盖率。"
        "历史数据可能由上游修订，离线复算以本次保存的原始文件为准。\n\n"
        + "\n".join(
            f"- [{source['provider']}]({source['documentation']})"
            for source in SOURCES.values()
        )
        + "\n\n安装相同版本金乌后，运行 `python replay.py` 可从保存的原始文件重新生成数据表。\n"
    )
    (directory / "findings.md").write_text(text, encoding="utf-8")
    return quality


def integrate_solar_monthly(
    directory: Path,
    start: str,
    end: str,
    *,
    allow_network: bool = False,
    fetcher: Fetcher | None = None,
) -> dict:
    month_grid(start, end)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "sources.json").exists():
        raise ValueError("Output already contains a run; select a new directory")
    catalog = SourceCatalog()
    for name, source in SOURCES.items():
        catalog.add(
            source_id=name,
            url=source["url"],
            provider=source["provider"],
            title=source["field"],
            license=source["license"],
            role=SourceRole.CATALOG,
            discovered_via="official_solar_monthly_catalog",
        )
    report = acquire(
        catalog,
        policy=AcquisitionPolicy(
            allow_network=allow_network,
            allowed_hosts=("www.sidc.be", "services.swpc.noaa.gov"),
            max_fetches=2,
            max_bytes=5_000_000,
            timeout_seconds=45,
        ),
        workdir=directory,
        fetcher=fetcher,
    )
    _json(directory / "acquisition.json", report.to_dict())
    if len(report.fetched) != 2:
        return {
            "status": "acquisition_failed",
            "directory": str(directory),
            "sources": [result.to_dict() for result in report.results],
        }
    paths = {result.source_id: Path(result.path) for result in report.fetched}
    metadata = {
        "start_month": start,
        "end_month": end,
        "retrieved_utc": datetime.now(UTC).isoformat(),
        "discovery_method": "official_source_catalog",
        "sources": {
            name: {**source, "raw_file": paths[name].relative_to(directory).as_posix()}
            for name, source in SOURCES.items()
        },
    }
    _json(directory / "sources.json", metadata)
    quality = _write_tables(directory, paths, start, end)
    (directory / "replay.py").write_text(
        "from pathlib import Path\nfrom jw.scientific_data.solar_monthly import replay\n"
        'root = Path(__file__).resolve().parent\nreplay(root, root / "replayed")\n',
        encoding="utf-8",
    )
    return {
        **quality,
        "directory": str(directory),
        "files": [
            "merged_monthly.csv",
            "sources.json",
            "quality_report.json",
            "findings.md",
            "replay.py",
            "acquisition.json",
        ],
    }


def describe_result(result: dict) -> str:
    """Render the numerical tool receipt without another model interpretation."""
    if result["status"] not in {"completed", "completed_with_missing"}:
        reasons = result.get("error") or "; ".join(
            f"{source['source_id']}: {source.get('error') or source['status']}"
            for source in result.get("sources", [])
            if source["status"] != "ok"
        )
        return f"数据整合未完成（{result['status']}）：{reasons}。\n\n产物目录：{result.get('directory', '未创建')}"
    missing = result["missing"]
    correlation = result["pearson"]
    correlation_text = "无法计算" if correlation is None else f"{correlation:.6f}"
    directory = Path(result["directory"])
    files = "\n".join(
        f"- [{name}]({(directory / name).as_posix()})" for name in result["files"]
    )
    return (
        f"已完成 {result['start_month']} 至 {result['end_month']} 的太阳活动月度数据整合，"
        f"共 {result['rows']} 行。\n\n"
        f"太阳黑子数缺失 {missing['sunspot_number']} 个月，F10.7 缺失 {missing['f107_sfu']} 个月；"
        f"合并相同重复记录 {sum(result['identical_duplicates_removed'].values())} 条，冲突重复 0 条。"
        "缺失保留为空值，未插补。\n\n"
        f"数值范围：太阳黑子数 {result['ranges']['sunspot_number']}；"
        f"F10.7 {result['ranges']['f107_sfu']} sfu。"
        f"{result['paired_months']} 个配对月份的 Pearson 相关系数为 {correlation_text}，仅作描述，不推断因果。\n\n"
        "来源为 WDC-SILSO 月均总太阳黑子数与 NOAA SWPC 观测月均 F10.7，未使用平滑值或预测值。"
        "F10.7 来自加拿大 Penticton 观测；NOAA 接口的另一列黑子数引用 SIDC，不能将该列与 SILSO 重复计作独立证据。"
        "本次通过维护的官方来源目录取数，能力范围为这两个序列。\n\n"
        f"产物目录：`{directory}`\n\n{files}\n\n"
        "原始文件保留在 sources/；CSV 含逐值来源网址、原始值及行号或 JSON 索引，"
        "sources.json 保存下载时间、字段含义与单位。安装同版金乌后运行 `python replay.py` 可离线复算。"
    )


def replay(original: Path, output: Path) -> dict:
    metadata = json.loads((original / "sources.json").read_text(encoding="utf-8"))
    paths = {
        name: original / source["raw_file"]
        for name, source in metadata["sources"].items()
    }
    return _write_tables(output, paths, metadata["start_month"], metadata["end_month"])
