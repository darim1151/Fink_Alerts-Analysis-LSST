"""Utilities for discovering real LSST DiaObject/DiaSource identifiers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


OBJECT_ID_NAMES = {"diaobjectid", "r:diaobjectid", "dia_object_id", "objectid"}
SOURCE_ID_NAMES = {"diasourceid", "r:diasourceid", "dia_source_id"}


def extract_candidate_ids_from_payload(
    payload: Any,
    origin: str | None = None,
    source_path: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract candidate object/source IDs from an arbitrary JSON-like payload."""
    candidates = _empty_candidates()
    for path, key, value in _walk_payload(payload):
        normalized = _normalize_key(key)
        id_type: str | None = None
        if normalized in OBJECT_ID_NAMES or normalized.endswith("diaobjectid"):
            id_type = "diaObjectId"
        elif normalized in SOURCE_ID_NAMES or normalized.endswith("diasourceid"):
            id_type = "diaSourceId"
        elif "object" in normalized and "id" in normalized:
            id_type = "other_object_ids"
        elif "source" in normalized and "id" in normalized:
            id_type = "other_source_ids"
        if id_type is None or not _looks_like_id(value):
            continue
        confidence = "high" if id_type in {"diaObjectId", "diaSourceId"} else "medium"
        candidates[id_type].append(
            {
                "id_type": id_type,
                "value": str(value),
                "field": key,
                "payload_path": path,
                "origin": origin,
                "source_path": source_path,
                "confidence": confidence,
                "reason": f"Found field `{key}` in payload",
            }
        )
    return _dedupe_candidates(candidates)


def extract_candidate_ids_from_file(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load a JSON fixture and extract candidate identifiers from it."""
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return extract_candidate_ids_from_payload(
        payload,
        origin=fixture_path.name,
        source_path=str(fixture_path),
    )


def merge_candidate_ids(candidates_list: list[dict[str, list[dict[str, Any]]]]) -> dict[str, list[dict[str, Any]]]:
    """Merge multiple candidate-ID dictionaries with de-duplication."""
    merged = _empty_candidates()
    for candidates in candidates_list:
        for key in merged:
            merged[key].extend(candidates.get(key, []))
    return _dedupe_candidates(merged)


def select_best_candidate_id(
    candidates: dict[str, list[dict[str, Any]]],
    preferred: str = "object",
) -> dict[str, Any] | None:
    """Select the best candidate object or source ID, preferring high-confidence IDs."""
    if preferred not in {"object", "source"}:
        raise ValueError("preferred must be 'object' or 'source'")
    keys = ["diaObjectId", "other_object_ids"] if preferred == "object" else ["diaSourceId", "other_source_ids"]
    scored: list[dict[str, Any]] = []
    for key in keys:
        for item in candidates.get(key, []):
            score = 2 if item.get("confidence") == "high" else 1
            scored.append({**item, "_score": score})
    if not scored:
        return None
    selected = sorted(scored, key=lambda item: (-item["_score"], item["value"]))[0]
    selected.pop("_score", None)
    return selected


def summarize_candidate_ids(candidates: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Return compact counts and example values by candidate ID type."""
    rows = []
    for id_type, items in candidates.items():
        rows.append(
            {
                "id_type": id_type,
                "count": len(items),
                "example_values": [item["value"] for item in items[:5]],
                "sources": sorted({str(item.get("source_path") or item.get("origin")) for item in items}),
            }
        )
    return rows


def _empty_candidates() -> dict[str, list[dict[str, Any]]]:
    return {
        "diaObjectId": [],
        "diaSourceId": [],
        "other_object_ids": [],
        "other_source_ids": [],
    }


def _walk_payload(payload: Any, path: str = "$") -> list[tuple[str, str, Any]]:
    rows: list[tuple[str, str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child_path = f"{path}.{key}"
            if isinstance(value, (dict, list)):
                rows.extend(_walk_payload(value, child_path))
            else:
                rows.append((child_path, str(key), value))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            rows.extend(_walk_payload(item, f"{path}[{index}]"))
    return rows


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _looks_like_id(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, float):
        return value.is_integer() and value > 0
    text = str(value).strip()
    return bool(text) and text.lower() not in {"nan", "none", "null"}


def _dedupe_candidates(candidates: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    deduped = _empty_candidates()
    for id_type, items in candidates.items():
        seen: set[tuple[str, str | None]] = set()
        for item in items:
            key = (str(item.get("value")), item.get("field"))
            if key in seen:
                continue
            seen.add(key)
            deduped.setdefault(id_type, []).append(item)
    return deduped
