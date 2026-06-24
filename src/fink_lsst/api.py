"""Small public Fink REST API client."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from .contracts import summarize_contracts
from .config import AMBIGUOUS_API_BASE_URL, DEFAULT_API_BASE_URL, FinkConfig
from .storage import safe_artifact_path, write_json


class FinkApiError(RuntimeError):
    """Readable exception for Fink API failures."""


class FinkApiClient:
    """Conservative wrapper around public/no-login Fink REST endpoints."""

    def __init__(
        self,
        config: FinkConfig | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        cache_dir: str | Path | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or FinkConfig()
        self.base_url = self.normalize_base_url(base_url or self.config.api_base_url)
        self.timeout_seconds = timeout_seconds or self.config.request_timeout_seconds
        self.session = session or requests.Session()
        self.cache_dir = Path(cache_dir) if cache_dir else None

    @staticmethod
    def normalize_base_url(base_url: str) -> str:
        """Normalize survey API base URLs to include exactly one `/api/v1` suffix."""
        cleaned = str(base_url).strip().rstrip("/")
        if cleaned in {AMBIGUOUS_API_BASE_URL, f"{AMBIGUOUS_API_BASE_URL}/api/v1"}:
            raise FinkApiError(
                "Ambiguous Fink API base URL is not allowed for LSST. "
                f"Use {DEFAULT_API_BASE_URL} instead."
            )
        if cleaned.endswith("/api/v1"):
            return cleaned
        return f"{cleaned}/api/v1"

    @property
    def api_root_url(self) -> str:
        """Return the API host root without the `/api/v1` suffix."""
        if self.base_url.endswith("/api/v1"):
            return self.base_url[: -len("/api/v1")]
        return self.base_url.rstrip("/")

    def build_url(self, endpoint: str) -> str:
        """Build a full endpoint URL from a name such as `schema` or `/api/v1/schema`."""
        endpoint_text = str(endpoint).strip()
        if endpoint_text.startswith(("http://", "https://")):
            return endpoint_text
        endpoint_text = endpoint_text.lstrip("/")
        if endpoint_text.startswith("api/v1/"):
            endpoint_text = endpoint_text.removeprefix("api/v1/")
        return urljoin(f"{self.base_url}/", endpoint_text)

    def build_root_url(self, endpoint: str) -> str:
        """Build a URL for host-root endpoints such as `/swagger.json`."""
        endpoint_text = str(endpoint).strip()
        if endpoint_text.startswith(("http://", "https://")):
            return endpoint_text
        return urljoin(f"{self.api_root_url}/", endpoint_text.lstrip("/")
        )

    def check_reachability(self) -> dict[str, Any]:
        """Check basic public API reachability with the lightweight schema endpoint."""
        probe = self.probe_endpoint("schema")
        if probe["ok"]:
            payload = probe.get("data")
            probe["records_or_keys"] = len(payload) if hasattr(payload, "__len__") else None
        probe.pop("data", None)
        return probe

    def get_schema(self, cache: bool = False) -> Any:
        """Retrieve the public field schema."""
        return self.get("schema", cache=cache)

    def get_swagger(self, cache: bool = False) -> Any:
        """Retrieve the public Swagger/OpenAPI document from `/swagger.json`."""
        return self.get("/swagger.json", cache=cache, root_endpoint=True)

    def get_contract_summary(self, swagger: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return a compact contract summary from a Swagger/OpenAPI document."""
        return summarize_contracts(swagger or self.get_swagger())

    def get_tags(self, cache: bool = False) -> Any:
        """Retrieve public Fink LSST tag definitions if exposed."""
        return self.get("tags", cache=cache)

    def get_classes(self, cache: bool = False) -> Any:
        """Retrieve the public list of Fink classes."""
        return self.get("classes", cache=cache)

    def get_openapi_spec(self, cache: bool = False) -> Any:
        """Try to retrieve an OpenAPI specification if the service exposes one."""
        for endpoint, root_endpoint in (("/openapi.json", True), ("/swagger.json", True)):
            try:
                return self.get(endpoint, cache=cache, root_endpoint=root_endpoint)
            except FinkApiError:
                continue
        raise FinkApiError("No OpenAPI specification found at /openapi.json or /swagger.json")

    def get_statistics(self, date: str | None = None, cache: bool = False) -> Any:
        """Retrieve statistics for a night, month, or year."""
        payload: dict[str, Any] = {"output-format": "json"}
        if date is not None:
            payload["date"] = date
        return self.post("statistics", payload, cache=cache)

    def statistics(self, date: str, cache: bool = False) -> Any:
        """Backward-compatible alias for `get_statistics`."""
        return self.get_statistics(date=date, cache=cache)

    def request_json_or_diagnostic(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        root_endpoint: bool = False,
    ) -> dict[str, Any]:
        """Request an endpoint and return parsed JSON or a safe diagnostic record."""
        started = time.perf_counter()
        method_upper = method.upper()
        url = self.build_root_url(endpoint) if root_endpoint else self.build_url(endpoint)
        response: requests.Response | None = None
        try:
            if method_upper == "GET":
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            elif method_upper == "POST":
                response = self.session.post(
                    url,
                    params=params,
                    json=payload or {},
                    timeout=self.timeout_seconds,
                )
            else:
                raise ValueError("method must be GET or POST")

            elapsed = time.perf_counter() - started
            status_code = response.status_code
            content_type = response.headers.get("Content-Type")
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                return _diagnostic(
                    ok=False,
                    endpoint=endpoint,
                    method=method_upper,
                    url=url,
                    payload=payload,
                    params=params,
                    elapsed_seconds=elapsed,
                    status_code=status_code,
                    error=f"non-JSON response: {exc}",
                    content_type=content_type,
                    response_preview=response.text[:200],
                    http_ok=True,
                    parsed_json=False,
                )
            return _diagnostic(
                ok=True,
                endpoint=endpoint,
                method=method_upper,
                url=url,
                payload=payload,
                params=params,
                elapsed_seconds=elapsed,
                status_code=status_code,
                content_type=content_type,
                http_ok=True,
                parsed_json=True,
                data=data,
            )
        except requests.Timeout as exc:
            return _diagnostic(
                ok=False,
                endpoint=endpoint,
                method=method_upper,
                url=url,
                payload=payload,
                params=params,
                elapsed_seconds=time.perf_counter() - started,
                status_code=response.status_code if response is not None else None,
                content_type=response.headers.get("Content-Type") if response is not None else None,
                error=f"timeout: {exc}",
                http_ok=False,
                parsed_json=False,
            )
        except requests.RequestException as exc:
            return _diagnostic(
                ok=False,
                endpoint=endpoint,
                method=method_upper,
                url=url,
                payload=payload,
                params=params,
                elapsed_seconds=time.perf_counter() - started,
                status_code=response.status_code if response is not None else None,
                content_type=response.headers.get("Content-Type") if response is not None else None,
                response_preview=response.text[:200] if response is not None else None,
                error=str(exc),
                http_ok=False,
                parsed_json=False,
            )
        except Exception as exc:  # noqa: BLE001 - probe diagnostics should not crash scripts
            return _diagnostic(
                ok=False,
                endpoint=endpoint,
                method=method_upper,
                url=url,
                payload=payload,
                params=params,
                elapsed_seconds=time.perf_counter() - started,
                status_code=response.status_code if response is not None else None,
                content_type=response.headers.get("Content-Type") if response is not None else None,
                error=str(exc),
                http_ok=False,
                parsed_json=False,
            )

    def probe_endpoint(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        root_endpoint: bool = False,
    ) -> dict[str, Any]:
        """Probe one tiny public endpoint and return structured diagnostics."""
        return self.request_json_or_diagnostic(
            method=method,
            endpoint=endpoint,
            payload=payload,
            params=params,
            root_endpoint=root_endpoint,
        )

    def probe_schema_variants(
        self,
        endpoints: list[str] | None = None,
        alternate_argument_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Probe safe `/schema` request variants and return diagnostics."""
        endpoint_names = endpoints or ["sources", "objects", "fp", "conesearch", "tags", "statistics"]
        argument_names = alternate_argument_names or ["endpoint"]
        probes = [self.probe_endpoint("schema", method="GET")]
        for endpoint_name in endpoint_names:
            probes.append(
                self.probe_endpoint(
                    "schema",
                    method="GET",
                    params={"endpoint": endpoint_name, "output-format": "json"},
                )
            )
        for argument_name in argument_names:
            for endpoint_name in endpoint_names:
                value = f"/api/v1/{endpoint_name}" if argument_name == "path" else endpoint_name
                probes.append(
                    self.probe_endpoint(
                        "schema",
                        method="POST",
                        payload={argument_name: value, "output-format": "json"},
                    )
                )
        return probes

    def probe_small_endpoint_contracts(self, include_blocks: bool = True) -> list[dict[str, Any]]:
        """Probe tiny public endpoints that do not require object/source IDs."""
        probes = [
            self.probe_endpoint("tags", method="GET"),
            self.probe_endpoint(
                "statistics",
                method="POST",
                payload={"date": "2026", "output-format": "json"},
            ),
        ]
        if include_blocks:
            probes.append(self.probe_endpoint("blocks", method="GET"))
        return probes

    def latests(
        self,
        class_name: str,
        n: int | None = None,
        columns: str | None = None,
        startdate: str | None = None,
        stopdate: str | None = None,
        cache: bool = False,
    ) -> Any:
        """Retrieve a small set of latest alerts by Fink class."""
        limit = min(int(n or self.config.max_rows), self.config.max_rows)
        payload: dict[str, Any] = {"class": class_name, "n": str(limit), "output-format": "json"}
        if columns:
            payload["columns"] = columns
        if startdate:
            payload["startdate"] = startdate
        if stopdate:
            payload["stopdate"] = stopdate
        return self.post("/latests", payload, cache=cache)

    def objects(
        self,
        object_id: str,
        columns: str | None = None,
        with_upper_limits: bool = False,
        cache: bool = False,
    ) -> Any:
        """Retrieve data for one known object with optional column restriction."""
        payload: dict[str, Any] = {"objectId": object_id, "output-format": "json"}
        if columns:
            payload["columns"] = columns
        if with_upper_limits:
            payload["withupperlim"] = "True"
        return self.post("/objects", payload, cache=cache)

    def conesearch(
        self,
        ra: float,
        dec: float,
        radius_arcsec: float,
        n: int | None = None,
        columns: str | None = None,
        cache: bool = False,
    ) -> Any:
        """Run a small cone search with radius in arcseconds."""
        if radius_arcsec <= 0 or radius_arcsec > 60:
            raise ValueError("radius_arcsec must be in the interval (0, 60]")
        payload: dict[str, Any] = {
            "ra": float(ra),
            "dec": float(dec),
            "radius": float(radius_arcsec),
            "n": str(min(int(n or self.config.max_rows), self.config.max_rows)),
            "output-format": "json",
        }
        if columns:
            payload["columns"] = columns
        return self.post("/conesearch", payload, cache=cache)

    def resolver(self, resolver: str, name: str, reverse: bool = False, cache: bool = False) -> Any:
        """Resolve a single public object name through Fink-supported resolvers."""
        if not name:
            raise ValueError("Refusing empty resolver name because it can return a large table")
        payload = {"resolver": resolver, "name": name}
        if reverse:
            payload["reverse"] = True
        return self.post("/resolver", payload, cache=cache)

    def get(self, endpoint: str, cache: bool = False, root_endpoint: bool = False) -> Any:
        """Run a GET request and parse the response."""
        return self._request("GET", endpoint, None, cache=cache, root_endpoint=root_endpoint)

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        cache: bool = False,
        root_endpoint: bool = False,
    ) -> Any:
        """Run a POST request and parse the response."""
        return self._request("POST", endpoint, payload, cache=cache, root_endpoint=root_endpoint)

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        cache: bool = False,
        root_endpoint: bool = False,
    ) -> Any:
        url = self.build_root_url(endpoint) if root_endpoint else self.build_url(endpoint)
        cache_path = self._cache_path(method, endpoint, payload) if cache and self.cache_dir else None
        if cache_path and cache_path.exists():
            with cache_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)

        try:
            if method == "GET":
                response = self.session.get(url, timeout=self.timeout_seconds)
            else:
                response = self.session.post(url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.Timeout as exc:
            raise FinkApiError(f"Fink API timeout for {url}") from exc
        except requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else None
            status_text = f" status={status}" if status is not None else ""
            raise FinkApiError(f"Fink API request failed for {url}:{status_text} {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise FinkApiError(f"Fink API returned non-JSON content for {url}") from exc

        if cache_path:
            write_json(data, cache_path)
        return data

    def _cache_path(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
    ) -> Path:
        key = json.dumps(
            {"method": method, "endpoint": endpoint, "payload": payload},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        cache_root = self.cache_dir or Path("api_cache")
        return safe_artifact_path(cache_root / f"{digest}.json", root_name="data")


def _diagnostic(
    ok: bool,
    endpoint: str,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
    elapsed_seconds: float,
    status_code: int | None,
    error: str | None = None,
    content_type: str | None = None,
    response_preview: str | None = None,
    http_ok: bool | None = None,
    parsed_json: bool | None = None,
    data: Any | None = None,
) -> dict[str, Any]:
    """Build a structured endpoint diagnostic record."""
    record = {
        "ok": ok,
        "endpoint": endpoint,
        "method": method,
        "url": url,
        "payload": payload,
        "params": params,
        "status_code": status_code,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "error": error,
        "content_type": content_type,
        "response_preview": response_preview,
        "http_ok": http_ok,
        "parsed_json": parsed_json,
    }
    if data is not None:
        record["data"] = data
    return record
