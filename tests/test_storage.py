import json

import pytest

from fink_lsst.storage import safe_artifact_path, write_manifest


def test_safe_artifact_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="relative path"):
        safe_artifact_path("../outside.json", project_root=tmp_path)


def test_write_manifest_under_outputs(tmp_path):
    (tmp_path / "outputs").mkdir()
    path = write_manifest(
        tmp_path / "outputs" / "smoke_test" / "manifest.json",
        artifacts=[{"name": "schema", "path": "data/schema.json"}],
        notes=["offline test"],
        project_root=tmp_path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["artifacts"][0]["name"] == "schema"

