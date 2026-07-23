"""Bounded metadata-only inspection for scientific container formats.

This trusted helper runs inside an isolated, network-free bubblewrap process.
It never materializes scientific arrays and emits at most one compact JSON row.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any

MAX_ITEMS = 512
MAX_ATTRIBUTES = 256
MAX_DIMENSIONS = 128
MAX_VARIABLES = 256
MAX_FITS_HDUS = 64
MAX_PARQUET_ROW_GROUPS = 10_000
MAX_PARQUET_COLUMNS = 1_000
MAX_FOOTER_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
sys.path.insert(0, "/runtime/site-packages")


class InspectionLimit(ValueError):
    pass


class DangerousReference(ValueError):
    pass


def _bounded_attrs(attrs: Any) -> dict[str, str]:
    rows: dict[str, str] = {}
    for index, key in enumerate(attrs):
        if index >= MAX_ATTRIBUTES:
            raise InspectionLimit("too many attributes")
        value = attrs[key]
        rendered = str(value)
        rows[str(key)[:200]] = rendered[:500]
    return rows


def _estimated_bytes(shape: Any, dtype: Any) -> int:
    size = int(getattr(dtype, "itemsize", 0) or 0)
    for value in shape or ():
        size *= int(value)
        if size > MAX_UNCOMPRESSED_BYTES:
            raise InspectionLimit("declared array exceeds the uncompressed-size limit")
    return size


def inspect_fits(path: Path) -> dict[str, Any]:
    from astropy.io import fits

    rows: list[dict[str, Any]] = []
    with fits.open(
        path,
        mode="readonly",
        memmap=True,
        lazy_load_hdus=True,
        do_not_scale_image_data=True,
    ) as hdus:
        if len(hdus) > MAX_FITS_HDUS:
            raise InspectionLimit("FITS contains too many HDUs")
        for index, hdu in enumerate(hdus):
            header = hdu.header
            shape = tuple(int(value) for value in (getattr(hdu, "shape", None) or ()))
            bitpix = header.get("BITPIX")
            dtype = {
                8: "uint8",
                16: "int16",
                32: "int32",
                64: "int64",
                -32: "float32",
                -64: "float64",
            }.get(bitpix)
            if shape and dtype is not None:
                import numpy as np

                _estimated_bytes(shape, np.dtype(dtype))
            rows.append(
                {
                    "index": index,
                    "name": str(getattr(hdu, "name", ""))[:100],
                    "class": type(hdu).__name__,
                    "shape": list(shape),
                    "dtype": dtype,
                    "unit": str(header.get("BUNIT", ""))[:200],
                    "time": str(
                        header.get("DATE-OBS", header.get("MJD-OBS", header.get("DATE", "")))
                    )[:200],
                    "compressed": bool(header.get("ZIMAGE", False)),
                    "key_headers": {
                        key: str(header[key])[:300]
                        for key in ("EXTNAME", "OBJECT", "TELESCOP", "INSTRUME", "TIMESYS")
                        if key in header
                    },
                }
            )
    return {"kind": "scientific_container", "format": "fits", "profile_complete": True, "hdus": rows}


def inspect_netcdf(path: Path) -> dict[str, Any]:
    import xarray as xr

    engines = ["h5netcdf", "scipy"]
    last_error: Exception | None = None
    dataset = None
    for engine in engines:
        try:
            dataset = xr.open_dataset(
                path,
                engine=engine,
                decode_cf=False,
                mask_and_scale=False,
                cache=False,
            )
            break
        except Exception as exc:
            last_error = exc
    if dataset is None:
        raise ValueError(f"NetCDF metadata cannot be opened: {type(last_error).__name__}")
    try:
        if len(dataset.sizes) > MAX_DIMENSIONS or len(dataset.variables) > MAX_VARIABLES:
            raise InspectionLimit("NetCDF metadata exceeds dimension or variable limits")
        variables = []
        for name, variable in dataset.variables.items():
            _estimated_bytes(variable.shape, variable.dtype)
            variables.append(
                {
                    "name": str(name)[:200],
                    "dimensions": [str(value)[:200] for value in variable.dims],
                    "shape": [int(value) for value in variable.shape],
                    "dtype": str(variable.dtype),
                    "unit": str(variable.attrs.get("units", ""))[:200],
                    "calendar": str(variable.attrs.get("calendar", ""))[:200],
                    "attributes": _bounded_attrs(variable.attrs),
                }
            )
        return {
            "kind": "scientific_container",
            "format": "netcdf",
            "profile_complete": True,
            "dimensions": {str(key): int(value) for key, value in dataset.sizes.items()},
            "coordinates": [str(value) for value in dataset.coords],
            "variables": variables,
            "attributes": _bounded_attrs(dataset.attrs),
        }
    finally:
        dataset.close()


def inspect_hdf5(path: Path) -> dict[str, Any]:
    import h5py

    rows: list[dict[str, Any]] = []
    with h5py.File(path, "r") as handle:
        def walk(group: Any, prefix: str, depth: int) -> None:
            if depth > 32:
                raise InspectionLimit("HDF5 nesting exceeds 32 levels")
            for name in group.keys():
                if len(rows) >= MAX_ITEMS:
                    raise InspectionLimit("HDF5 contains too many groups or datasets")
                link = group.get(name, getlink=True)
                if isinstance(link, h5py.ExternalLink):
                    raise DangerousReference("HDF5 external links are not accepted")
                obj = group.get(name, getlink=False)
                full = f"{prefix}/{name}" if prefix else f"/{name}"
                if isinstance(obj, h5py.Dataset):
                    _estimated_bytes(obj.shape, obj.dtype)
                    rows.append(
                        {
                            "path": full[:500],
                            "type": "dataset",
                            "shape": [int(value) for value in obj.shape],
                            "dtype": str(obj.dtype),
                            "attributes": _bounded_attrs(obj.attrs),
                        }
                    )
                else:
                    rows.append(
                        {
                            "path": full[:500],
                            "type": "group",
                            "attributes": _bounded_attrs(obj.attrs),
                        }
                    )
                    walk(obj, full, depth + 1)

        walk(handle, "", 0)
        return {
            "kind": "scientific_container",
            "format": "hdf5",
            "profile_complete": True,
            "root_attributes": _bounded_attrs(handle.attrs),
            "objects": rows,
        }


def inspect_parquet(path: Path) -> dict[str, Any]:
    import pyarrow.parquet as pq

    with path.open("rb") as handle:
        handle.seek(-8, 2)
        tail = handle.read(8)
    if len(tail) != 8 or tail[4:] != b"PAR1":
        raise ValueError("Parquet footer magic is invalid")
    footer_size = struct.unpack("<I", tail[:4])[0]
    if footer_size > MAX_FOOTER_BYTES:
        raise InspectionLimit("Parquet footer exceeds the metadata limit")
    parquet = pq.ParquetFile(path)
    metadata = parquet.metadata
    if metadata.num_row_groups > MAX_PARQUET_ROW_GROUPS:
        raise InspectionLimit("Parquet has too many row groups")
    if metadata.num_columns > MAX_PARQUET_COLUMNS:
        raise InspectionLimit("Parquet has too many columns")
    row_groups = [
        {
            "index": index,
            "rows": metadata.row_group(index).num_rows,
            "total_byte_size": metadata.row_group(index).total_byte_size,
        }
        for index in range(metadata.num_row_groups)
    ]
    return {
        "kind": "scientific_container",
        "format": "parquet",
        "profile_complete": True,
        "schema": str(parquet.schema_arrow)[:16000],
        "row_count": metadata.num_rows,
        "row_groups": row_groups,
        "created_by": str(metadata.created_by or "")[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--path", required=True)
    parser.add_argument("--format", required=True, choices=["fits", "netcdf", "hdf5", "parquet"])
    args = parser.parse_args()
    path = Path(args.path)
    inspectors = {
        "fits": inspect_fits,
        "netcdf": inspect_netcdf,
        "hdf5": inspect_hdf5,
        "parquet": inspect_parquet,
    }
    try:
        result = inspectors[args.format](path)
        output = {"status": "ok", **result}
    except DangerousReference as exc:
        output = {"status": "dangerous_reference", "reason": str(exc)}
    except InspectionLimit as exc:
        output = {"status": "limit_exceeded", "reason": str(exc)}
    except Exception as exc:
        output = {
            "status": "damaged_or_unsupported",
            "format": args.format,
            "reason": f"{type(exc).__name__}: {exc}"[:1000],
        }
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
