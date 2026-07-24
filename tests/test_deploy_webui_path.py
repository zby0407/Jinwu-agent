from pathlib import Path

from EvoScientist.deploy.webui import _WEBUI_DIST


def test_webui_dist_points_to_embedded_frontend() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    assert _WEBUI_DIST == repository_root / "webui" / "dist" / "server.js"
