#!/usr/bin/env python3
"""Render paper pages for figure-level QA."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def pdf_pages(pdf: Path) -> int:
    if not shutil.which("pdfinfo"):
        raise RuntimeError("pdfinfo not found")
    out = subprocess.check_output(["pdfinfo", str(pdf)], text=True, stderr=subprocess.STDOUT)
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("cannot determine page count")


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(max(1, int(a)), min(total, int(b)) + 1))
        else:
            n = int(part)
            if 1 <= n <= total:
                pages.add(n)
    return sorted(pages)


def page_text(pdf: Path, page: int) -> str:
    if not shutil.which("pdftotext"):
        return ""
    try:
        return subprocess.check_output(
            ["pdftotext", "-f", str(page), "-l", str(page), str(pdf), "-"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return ""


def render_page(pdf: Path, page: int, outdir: Path, dpi: int) -> Path:
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm not found")
    prefix = outdir / f"page_{page:03d}"
    subprocess.check_call(
        ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), str(pdf), str(prefix)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    matches = sorted(outdir.glob(f"page_{page:03d}-*.png"))
    return matches[-1] if matches else outdir / f"page_{page:03d}.png"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render PDF pages and flag likely figure pages for visual QA.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--outdir", type=Path, default=Path("figure_page_qa"))
    parser.add_argument("--pages", help="Comma/range page spec, e.g. 1,5-8. Defaults to all pages.")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--only-figure-pages", action="store_true", help="Render only pages whose text contains figure captions.")
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    total = pdf_pages(args.pdf)
    pages = parse_pages(args.pages, total)

    manifest = []
    for page in pages:
        text = page_text(args.pdf, page)
        has_figure = bool(re.search(r"图\s*\d+", text))
        if args.only_figure_pages and not has_figure:
            continue
        image = render_page(args.pdf, page, args.outdir, args.dpi)
        manifest.append({"page": page, "image": str(image), "has_figure_caption": has_figure})

    report = {"pdf": str(args.pdf), "total_pages": total, "rendered_pages": manifest}
    report_path = args.outdir / "manifest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
