import pytest

from fink_lsst.contracts import (
    get_endpoint_contract,
    list_endpoints,
    optional_parameters,
    request_payload_schema,
    required_parameters,
    response_content_types,
    summarize_contracts,
)
from fink_lsst.storage import read_json


FAKE_SWAGGER = {
    "paths": {
        "/api/v1/tags": {
            "get": {
                "summary": "Tags",
                "parameters": [{"name": "output-format", "in": "query", "required": False, "type": "string"}],
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
            }
        },
        "/api/v1/statistics": {
            "post": {
                "summary": "Statistics",
                "parameters": [{"name": "date", "in": "body", "required": True, "type": "string"}],
                "responses": {"200": {"schema": {"type": "array"}}},
            }
        },
    }
}


def test_list_endpoints_and_get_contract():
    assert list_endpoints(FAKE_SWAGGER) == ["/api/v1/statistics", "/api/v1/tags"]
    assert "get" in get_endpoint_contract(FAKE_SWAGGER, "/api/v1/tags")


def test_summarize_contracts_from_fake_swagger():
    summary = summarize_contracts(FAKE_SWAGGER)
    assert {row["path"] for row in summary} == {"/api/v1/tags", "/api/v1/statistics"}
    tags = next(row for row in summary if row["path"] == "/api/v1/tags")
    assert tags["method"] == "GET"
    assert "application/json" in tags["response_content_types"]


def test_parameter_helpers():
    contract = {
        "parameters": [
            {"name": "required_id", "in": "query", "required": True, "type": "string"},
            {"name": "optional_n", "in": "query", "required": False, "type": "integer"},
        ],
        "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
    }
    assert required_parameters(contract)[0]["name"] == "required_id"
    assert optional_parameters(contract)[0]["name"] == "optional_n"
    assert response_content_types(contract) == ["application/json"]


def test_request_payload_schema_from_request_body():
    contract = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"date": {"type": "string"}}}
                }
            }
        }
    }
    assert request_payload_schema(contract)["type"] == "object"


def test_request_payload_schema_from_swagger_body_parameter():
    contract = {
        "parameters": [
            {
                "name": "payload",
                "in": "body",
                "required": True,
                "schema": {"$ref": "#/definitions/schema"},
            }
        ]
    }
    assert request_payload_schema(contract)["$ref"] == "#/definitions/schema"


def test_swagger_fixture_parses_if_present():
    fixture = "data/fixtures/fink_lsst_swagger.json"
    try:
        swagger = read_json(fixture)
    except FileNotFoundError:
        pytest.skip("No Swagger fixture captured yet")
    assert isinstance(list_endpoints(swagger), list)
