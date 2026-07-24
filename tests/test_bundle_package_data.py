import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_data_includes_nested_bundle_agents_and_skills() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = set(pyproject["tool"]["setuptools"]["package-data"]["jw"])

    assert "subagents/**/*.yaml" in patterns
    assert "subagents/**/skills/**/*" in patterns


def test_bundle_manifests_have_their_declared_skills() -> None:
    import yaml

    for bundle_name in ("core", "solar"):
        bundle_dir = ROOT / "jw" / "subagents" / bundle_name
        manifest = yaml.safe_load(
            (bundle_dir / "bundle.yaml").read_text(encoding="utf-8")
        )
        for skill_name in manifest["skills"]:
            assert (bundle_dir / "skills" / skill_name / "SKILL.md").is_file()
