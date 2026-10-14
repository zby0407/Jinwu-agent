"""Load hash-preserved scientific helpers without workstation paths."""

import hashlib
import importlib.util
import json
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def inventory() -> dict:
    return json.loads((PACKAGE / "asset_inventory.json").read_text(encoding="utf-8"))


def load(name: str):
    allowed = {
        "available24_data",
        "available24_model",
        "available24_live",
        "independent_label_audit",
    }
    if name not in allowed:
        raise ValueError("Unregistered scientific module")
    rows = {r["path"]: r["sha256"] for r in inventory()["science_source_files"]}
    for relative, expected in rows.items():
        path = PACKAGE / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen scientific implementation differs: " + relative)
    path = PACKAGE / "_frozen" / (name + ".py")
    spec = importlib.util.spec_from_file_location("jw_flare_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
