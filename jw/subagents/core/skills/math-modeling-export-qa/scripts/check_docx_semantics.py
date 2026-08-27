#!/usr/bin/env python3
"""Lightweight DOCX semantic QA for contest papers."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}

RAW_FORMULA_RE = re.compile(
    r"(\\frac|\\sum|\\beta|\\alpha|\\begin\{|\\end\{|\$\$|\\\[|\\\]|_\{|"
    r"\^\{|I_conf|I_exp|x_nom|x_rob|arg\s+max|Top5|Quantile)"
)


def paragraph_text(p: ET.Element) -> str:
    return "".join(t.text or "" for t in p.findall(".//w:t", NS)).strip()


def style_id(p: ET.Element) -> str:
    pstyle = p.find("./w:pPr/w:pStyle", NS)
    return pstyle.attrib.get(f"{{{NS['w']}}}val", "") if pstyle is not None else ""


def is_code_like(text: str, style: str) -> bool:
    if "code" in style.lower() or "source" in style.lower():
        return True
    code_hits = sum(
        token in text
        for token in ["import ", "def ", "class ", "return ", "np.", "pd.", "for ", "while ", "from ", "plt."]
    )
    return code_hits >= 2 or (len(text) > 120 and text.count("{") + text.count("}") + text.count("_") > 8)


def read_xml(z: zipfile.ZipFile, name: str) -> str:
    try:
        return z.read(name).decode("utf-8", errors="ignore")
    except KeyError:
        return ""


def analyze(docx: Path) -> dict:
    with zipfile.ZipFile(docx) as z:
        document_xml = read_xml(z, "word/document.xml")
        styles_xml = read_xml(z, "word/styles.xml")
    root = ET.fromstring(document_xml.encode("utf-8"))

    omml_count = len(root.findall(".//m:oMath", NS)) + len(root.findall(".//m:oMathPara", NS))
    paras = root.findall(".//w:body/w:p", NS)

    raw_formula_paras = []
    caption_counts = {"figures": 0, "tables": 0}
    toc_field = "TOC" in document_xml and "instrText" in document_xml
    internal_links = "w:hyperlink" in document_xml and "w:anchor" in document_xml

    for idx, p in enumerate(paras):
        text = paragraph_text(p)
        if not text:
            continue
        if text.startswith("图 "):
            caption_counts["figures"] += 1
        if text.startswith("表 "):
            caption_counts["tables"] += 1
        has_math = p.find(".//m:oMath", NS) is not None or p.find(".//m:oMathPara", NS) is not None
        if not has_math and RAW_FORMULA_RE.search(text) and not is_code_like(text, style_id(p)):
            raw_formula_paras.append({"paragraph_index": idx, "text": text[:220]})

    color_values = sorted(set(re.findall(r'w:color\s+w:val="([^"]+)"', document_xml + "\n" + styles_xml)))
    non_black_colors = [c for c in color_values if c.upper() not in {"000000", "AUTO"}]

    blocking = []
    if raw_formula_paras:
        blocking.append("raw_formula_like_body_text")
    if non_black_colors:
        blocking.append("non_black_direct_colors")
    if not (toc_field or internal_links):
        blocking.append("no_toc_field_or_internal_links_detected")

    return {
        "docx": str(docx),
        "paragraphs": len(paras),
        "omml_nodes": omml_count,
        "raw_formula_like_paragraphs": raw_formula_paras,
        "colors": color_values,
        "non_black_colors": non_black_colors,
        "toc_field_detected": toc_field,
        "internal_links_detected": internal_links,
        "caption_counts": caption_counts,
        "blocking": blocking,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check DOCX semantic issues common in contest papers.")
    parser.add_argument("docx", type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON report path.")
    parser.add_argument("--no-strict", action="store_true", help="Always exit 0; still print findings.")
    args = parser.parse_args()

    report = analyze(args.docx)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if args.no_strict or not report["blocking"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
