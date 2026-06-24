import pytest

from fink_lsst.api import FinkApiClient, FinkApiError
from fink_lsst.config import FinkConfig


def test_normalize_base_url_adds_api_version():
    assert (
        FinkApiClient.normalize_base_url("https://api.lsst.fink-portal.org")
        == "https://api.lsst.fink-portal.org/api/v1"
    )


def test_normalize_base_url_keeps_existing_api_version():
    assert (
        FinkApiClient.normalize_base_url("https://api.lsst.fink-portal.org/api/v1/")
        == "https://api.lsst.fink-portal.org/api/v1"
    )


def test_normalize_base_url_rejects_ambiguous_generic_host():
    with pytest.raises(FinkApiError, match="Ambiguous"):
        FinkApiClient.normalize_base_url("https://api.fink-portal.org/api/v1")


def test_build_url_accepts_endpoint_forms():
    client = FinkApiClient(FinkConfig())
    assert client.build_url("schema") == "https://api.lsst.fink-portal.org/api/v1/schema"
    assert client.build_url("/api/v1/tags") == "https://api.lsst.fink-portal.org/api/v1/tags"
    assert client.build_url("https://example.test/custom") == "https://example.test/custom"


def test_build_root_url_for_swagger():
    client = FinkApiClient(FinkConfig())
    assert client.build_root_url("/swagger.json") == "https://api.lsst.fink-portal.org/swagger.json"


def test_request_json_or_diagnostic_handles_non_json():
    class FakeResponse:
        status_code = 200
        text = "documentation response"
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("no json")

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            self.url = url
            self.params = params
            self.timeout = timeout
            return FakeResponse()

    session = FakeSession()
    client = FinkApiClient(FinkConfig(), session=session)
    diagnostic = client.request_json_or_diagnostic(
        "GET",
        "schema",
        params={"endpoint": "sources"},
    )
    assert diagnostic["ok"] is False
    assert diagnostic["http_ok"] is True
    assert diagnostic["parsed_json"] is False
    assert diagnostic["content_type"].startswith("text/html")
    assert diagnostic["response_preview"] == "documentation response"
    assert session.params == {"endpoint": "sources"}
