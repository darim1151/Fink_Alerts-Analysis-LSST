"""Configuration loading for local Fink smoke tests."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_PRIMARY_SURVEY = "lsst"
DEFAULT_API_BASE_URL = "https://api.lsst.fink-portal.org"
DEFAULT_REFERENCE_API_BASE_URL = "https://api.ztf.fink-portal.org"
AMBIGUOUS_API_BASE_URL = "https://api.fink-portal.org"

# Backward-compatible alias retained for older imports/tests.
DEFAULT_BASE_URL = DEFAULT_API_BASE_URL


@dataclass(frozen=True)
class FinkConfig:
    """Runtime configuration for small public Fink API checks."""

    primary_survey: str = DEFAULT_PRIMARY_SURVEY
    api_base_url: str = DEFAULT_API_BASE_URL
    reference_api_base_url: str = DEFAULT_REFERENCE_API_BASE_URL
    request_timeout_seconds: float = 60.0
    max_smoke_rows: int = 5
    data_dir: Path = Path("data")
    output_dir: Path = Path("outputs")
    cache_responses: bool = True
    smoke_test: Mapping[str, Any] | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def base_url(self) -> str:
        """Backward-compatible alias for `api_base_url`."""
        return self.api_base_url

    @property
    def timeout_seconds(self) -> float:
        """Backward-compatible alias for `request_timeout_seconds`."""
        return self.request_timeout_seconds

    @property
    def max_rows(self) -> int:
        """Backward-compatible alias for `max_smoke_rows`."""
        return self.max_smoke_rows

    def validate(self) -> "FinkConfig":
        """Validate values that would make local smoke tests unsafe or ambiguous."""
        if self.primary_survey not in {"lsst", "ztf"}:
            raise ValueError("api.primary_survey must be 'lsst' or 'ztf'")
        if not self.api_base_url.startswith(("http://", "https://")):
            raise ValueError("api.api_base_url must start with http:// or https://")
        if self.reference_api_base_url and not self.reference_api_base_url.startswith(("http://", "https://")):
            raise ValueError("api.reference_api_base_url must start with http:// or https://")
        if self.request_timeout_seconds <= 0:
            raise ValueError("api.request_timeout_seconds must be positive")
        if self.max_smoke_rows <= 0:
            raise ValueError("api.max_smoke_rows must be positive")
        if self.max_smoke_rows > 100:
            raise ValueError("api.max_smoke_rows must stay small for this phase")
        if not self.data_dir:
            raise ValueError("paths.data_dir is required")
        if not self.output_dir:
            raise ValueError("paths.output_dir is required")
        return self


def load_config(path: str | Path | None = None) -> FinkConfig:
    """Load a YAML configuration file and apply conservative defaults."""
    raw: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Config file must contain a YAML mapping")
        raw = loaded

    api = raw.get("api", {}) or {}
    paths = raw.get("paths", {}) or {}
    smoke_test = raw.get("smoke_test", {}) or {}

    primary_survey = str(api.get("primary_survey", DEFAULT_PRIMARY_SURVEY)).lower()
    api_base_url = str(
        api.get("api_base_url", api.get("base_url", DEFAULT_API_BASE_URL))
    ).rstrip("/")
    reference_api_base_url = str(
        api.get("reference_api_base_url", DEFAULT_REFERENCE_API_BASE_URL)
    ).rstrip("/")
    config_warnings: list[str] = []
    if primary_survey == "lsst" and _is_ambiguous_base_url(api_base_url):
        message = (
            "Config used the ambiguous generic Fink API base URL. "
            "Using the LSST-specific API base https://api.lsst.fink-portal.org instead."
        )
        warnings.warn(message, UserWarning, stacklevel=2)
        config_warnings.append(message)
        api_base_url = DEFAULT_API_BASE_URL

    config = FinkConfig(
        primary_survey=primary_survey,
        api_base_url=api_base_url,
        reference_api_base_url=reference_api_base_url,
        request_timeout_seconds=float(
            api.get("request_timeout_seconds", api.get("timeout_seconds", 60.0))
        ),
        max_smoke_rows=int(api.get("max_smoke_rows", api.get("max_rows", 5))),
        data_dir=Path(paths.get("data_dir", "data")),
        output_dir=Path(paths.get("output_dir", "outputs")),
        cache_responses=bool(api.get("cache_responses", True)),
        smoke_test=smoke_test,
        warnings=tuple(config_warnings),
    )
    return config.validate()


def _is_ambiguous_base_url(url: str) -> bool:
    """Return true for the generic Fink API host that is not survey-specific."""
    cleaned = url.rstrip("/")
    return cleaned == AMBIGUOUS_API_BASE_URL or cleaned == f"{AMBIGUOUS_API_BASE_URL}/api/v1"
