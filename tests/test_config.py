from pathlib import Path

import pytest

from fink_lsst.config import (
    DEFAULT_API_BASE_URL,
    DEFAULT_REFERENCE_API_BASE_URL,
    load_config,
)


def test_load_config_defaults():
    config = load_config()
    assert config.primary_survey == "lsst"
    assert config.api_base_url == DEFAULT_API_BASE_URL
    assert config.reference_api_base_url == DEFAULT_REFERENCE_API_BASE_URL
    assert config.base_url == DEFAULT_API_BASE_URL
    assert config.max_rows == 5
    assert config.max_smoke_rows == 5
    assert config.timeout_seconds == 60.0
    assert config.request_timeout_seconds == 60.0
    assert config.data_dir == Path("data")


def test_load_config_from_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
api:
  primary_survey: "lsst"
  api_base_url: "https://example.test"
  reference_api_base_url: "https://reference.example.test"
  request_timeout_seconds: 3
  max_smoke_rows: 2
paths:
  data_dir: "data"
  output_dir: "outputs"
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.api_base_url == "https://example.test"
    assert config.reference_api_base_url == "https://reference.example.test"
    assert config.timeout_seconds == 3
    assert config.max_rows == 2


def test_load_config_backwards_compatible_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
api:
  base_url: "https://example.test/api/v1"
  timeout_seconds: 3
  max_rows: 2
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.api_base_url == "https://example.test/api/v1"
    assert config.request_timeout_seconds == 3
    assert config.max_smoke_rows == 2


def test_load_config_warns_and_rewrites_ambiguous_lsst_url(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
api:
  primary_survey: "lsst"
  api_base_url: "https://api.fink-portal.org/api/v1"
""",
        encoding="utf-8",
    )
    with pytest.warns(UserWarning, match="ambiguous"):
        config = load_config(path)
    assert config.api_base_url == DEFAULT_API_BASE_URL
    assert config.warnings


def test_load_config_rejects_large_limit(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("api:\n  max_rows: 500\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stay small"):
        load_config(path)
