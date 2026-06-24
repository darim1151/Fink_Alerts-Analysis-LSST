"""Helpers for reading and summarizing Fink LSST Swagger/OpenAPI contracts."""

from __future__ import annotations

from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def list_endpoints(swagger: dict[str, Any]) -> list[str]:
    """Return sorted endpoint paths from a Swagger/OpenAPI document."""
    paths = swagger.get("paths", {})
    if not isinstance(paths, dict):
        return []
    return sorted(str(path) for path in paths)


def get_endpoint_contract(swagger: dict[str, Any], path: str) -> dict[str, Any]:
    """Return the raw contract object for one endpoint path."""
    paths = swagger.get("paths", {})
    if not isinstance(paths, dict):
        return {}
    contract = paths.get(path, {})
    return contract if isinstance(contract, dict) else {}


def summarize_contracts(swagger: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize paths, methods, parameters, request bodies, and response types."""
    rows: list[dict[str, Any]] = []
    for path in list_endpoints(swagger):
        contract = get_endpoint_contract(swagger, path)
        path_parameters = _as_list(contract.get("parameters"))
        for method, operation in contract.items():
            method_lower = str(method).lower()
            if method_lower not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_parameters = path_parameters + _as_list(operation.get("parameters"))
            operation_contract = {
                "parameters": operation_parameters,
                "requestBody": operation.get("requestBody"),
                "responses": operation.get("responses"),
            }
            rows.append(
                {
                    "path": path,
                    "method": method_lower.upper(),
                    "summary": operation.get("summary"),
                    "description": operation.get("description"),
                    "parameters": operation_parameters,
                    "required_parameters": required_parameters(operation_contract),
                    "optional_parameters": optional_parameters(operation_contract),
                    "request_body_schema": request_payload_schema(operation_contract),
                    "response_content_types": response_content_types(operation_contract),
                }
            )
    return rows


def required_parameters(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return parameters marked required in an operation-like contract."""
    return [
        _parameter_summary(parameter)
        for parameter in _as_list(contract.get("parameters"))
        if bool(parameter.get("required"))
    ]


def optional_parameters(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return parameters not marked required in an operation-like contract."""
    return [
        _parameter_summary(parameter)
        for parameter in _as_list(contract.get("parameters"))
        if not bool(parameter.get("required"))
    ]


def response_content_types(contract: dict[str, Any]) -> list[str]:
    """Return declared response content types from an operation-like contract."""
    responses = contract.get("responses", {})
    if not isinstance(responses, dict):
        return []
    content_types: set[str] = set()
    for response in responses.values():
        if not isinstance(response, dict):
            continue
        content = response.get("content", {})
        if isinstance(content, dict):
            content_types.update(str(content_type) for content_type in content)
        schema = response.get("schema")
        if schema is not None:
            content_types.add("application/json")
    return sorted(content_types)


def request_payload_schema(contract: dict[str, Any]) -> Any:
    """Return the declared request payload schema if one is present."""
    request_body = contract.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content", {})
        if isinstance(content, dict):
            for media_type in ("application/json", "application/*+json"):
                media = content.get(media_type)
                if isinstance(media, dict) and "schema" in media:
                    return media["schema"]
            for media in content.values():
                if isinstance(media, dict) and "schema" in media:
                    return media["schema"]

    parameters = _as_list(contract.get("parameters"))
    body_parameters = [
        parameter for parameter in parameters if parameter.get("in") in {"body", "formData"}
    ]
    if body_parameters:
        if len(body_parameters) == 1 and isinstance(body_parameters[0].get("schema"), dict):
            return body_parameters[0]["schema"]
        return [_parameter_summary(parameter) for parameter in body_parameters]
    return None


def operation_contracts(swagger: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Return operation-like contracts keyed by `(path, METHOD)`."""
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for path in list_endpoints(swagger):
        contract = get_endpoint_contract(swagger, path)
        path_parameters = _as_list(contract.get("parameters"))
        for method, operation in contract.items():
            method_lower = str(method).lower()
            if method_lower not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            parameters = path_parameters + _as_list(operation.get("parameters"))
            operations[(path, method_lower.upper())] = {
                "parameters": parameters,
                "requestBody": operation.get("requestBody"),
                "responses": operation.get("responses"),
                "summary": operation.get("summary"),
                "description": operation.get("description"),
            }
    return operations


def _parameter_summary(parameter: dict[str, Any]) -> dict[str, Any]:
    """Return a compact parameter summary resilient to Swagger/OpenAPI variants."""
    if not isinstance(parameter, dict):
        return {"name": str(parameter)}
    schema = parameter.get("schema")
    parameter_type = parameter.get("type")
    if parameter_type is None and isinstance(schema, dict):
        parameter_type = schema.get("type") or schema.get("$ref")
    return {
        "name": parameter.get("name"),
        "in": parameter.get("in"),
        "required": bool(parameter.get("required")),
        "type": parameter_type,
        "description": parameter.get("description"),
    }


def _as_list(value: Any) -> list[Any]:
    """Return a list for fields that might be absent, a dict, or already a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
