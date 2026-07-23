"""Bundle discovery and manifest loading.

A **bundle** is a sub-directory of ``EvoScientist/subagents/`` containing:

* one or more ``<agent>.yaml`` sub-agent definitions (loaded recursively by
  ``EvoScientist.utils.load_subagents``), and
* an optional ``bundle.yaml`` manifest describing the bundle (name, version,
  description, owned agents/skills, and inter-bundle dependencies).

This module is the single entry point for answering:

* "which bundles exist?"           → :func:`discover_bundles`
* "what does bundle X declare?"    → :func:`load_bundle_manifest`
* "give me the agent dirs for      → :func:`resolve_bundle_dirs`
  bundles [core, solar] in dep order"

It performs **no** agent loading itself — that stays in
``EvoScientist.utils.load_subagents``. Keeping discovery and loading separate
lets callers inspect bundle metadata without paying the yaml-parse cost for
every agent file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Manifest filename recognised inside each bundle directory.
BUNDLE_MANIFEST = "bundle.yaml"


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """Parsed ``bundle.yaml`` for one bundle.

    All fields except ``name`` are optional so a bundle can ship a minimal
    manifest (just ``name: core``) or none at all.
    """

    name: str
    version: str = ""
    description: str = ""
    agents: tuple[str, ...] = field(default_factory=tuple)
    skills: tuple[str, ...] = field(default_factory=tuple)
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    # Directory the manifest was loaded from — used to resolve agent yamls.
    directory: Path = field(default_factory=Path)


def _parse_manifest(path: Path) -> BundleManifest:
    """Parse one ``bundle.yaml`` into a :class:`BundleManifest`."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: bundle manifest must be a mapping")
    name = data.get("name") or path.parent.name
    return BundleManifest(
        name=str(name),
        version=str(data.get("version", "")),
        description=str(data.get("description", "")),
        agents=tuple(data.get("agents") or ()),
        skills=tuple(data.get("skills") or ()),
        depends_on=tuple(data.get("depends_on") or ()),
        directory=path.parent,
    )


def discover_bundles(root: Path) -> list[BundleManifest]:
    """Return one :class:`BundleManifest` per bundle directory under *root*.

    A directory counts as a bundle if it contains at least one ``*.yaml``
    sub-agent definition **or** a ``bundle.yaml`` manifest. Directories
    starting with ``_`` or ``.`` are ignored (private / disabled).

    Bundles without a manifest get a synthetic one whose ``name`` defaults
    to the directory name — this keeps the "drop a folder of yamls in and
    it just works" path zero-config.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    bundles: list[BundleManifest] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith((".", "_")):
            continue

        manifest_path = child / BUNDLE_MANIFEST
        has_agent_yamls = any(
            p.name != BUNDLE_MANIFEST and not p.name.startswith((".", "_"))
            for p in child.glob("*.yaml")
        )
        if not manifest_path.exists() and not has_agent_yamls:
            continue

        if manifest_path.exists():
            bundles.append(_parse_manifest(manifest_path))
        else:
            bundles.append(BundleManifest(name=child.name, directory=child))
    return bundles


def load_bundle_manifest(bundle_dir: Path) -> BundleManifest:
    """Load the manifest for a single bundle directory.

    Raises ``FileNotFoundError`` if no ``bundle.yaml`` exists — use
    :func:`discover_bundles` for the tolerant path.
    """
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / BUNDLE_MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{bundle_dir}: no {BUNDLE_MANIFEST} manifest; "
            f"call discover_bundles() for the synthesised fallback"
        )
    return _parse_manifest(manifest_path)


def resolve_bundle_dirs(
    root: Path,
    bundles: list[str] | None = None,
) -> list[Path]:
    """Return the directories to pass to ``load_subagents`` for *bundles*.

    Args:
        root: The ``subagents/`` root.
        bundles: Bundle names to enable, in any order. ``None`` enables
            every discovered bundle. Dependencies declared via
            ``depends_on`` are added automatically and the result is
            topologically sorted so a bundle always loads after its deps.

    Raises:
        ValueError: On unknown bundle names or dependency cycles.
    """
    manifests = {m.name: m for m in discover_bundles(root)}
    if bundles is None:
        enabled = set(manifests)
    else:
        enabled = set(bundles)
        unknown = enabled - set(manifests)
        if unknown:
            raise ValueError(
                f"Unknown bundles {sorted(unknown)}; "
                f"available: {sorted(manifests)}"
            )

    # Expand dependencies (transitive).
    stack = list(enabled)
    while stack:
        name = stack.pop()
        manifest = manifests.get(name)
        if manifest is None:
            raise ValueError(f"Bundle {name!r} is a dependency but not present")
        for dep in manifest.depends_on:
            if dep not in enabled:
                enabled.add(dep)
                stack.append(dep)

    # Topological sort (Kahn). Stable so load order is deterministic.
    indegree: dict[str, int] = {n: 0 for n in enabled}
    edges: dict[str, set[str]] = {n: set() for n in enabled}
    for name in enabled:
        for dep in manifests[name].depends_on:
            if dep in enabled:
                edges[dep].add(name)
                indegree[name] += 1

    queue = sorted(n for n, d in indegree.items() if d == 0)
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for nxt in sorted(edges[node]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
                queue.sort()

    if len(ordered) != len(enabled):
        remaining = sorted(set(enabled) - set(ordered))
        raise ValueError(f"Bundle dependency cycle involving: {remaining}")

    return [manifests[n].directory for n in ordered]
