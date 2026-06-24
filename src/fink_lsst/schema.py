"""Schema snapshot and field-mapping helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .storage import write_json


def save_schema_snapshot(schema: Any, path: str | Path, project_root: str | Path | None = None) -> Path:
    """Save a raw schema payload as JSON."""
    return write_json(schema, path, project_root=project_root)


def summarize_schema_fields(schema: Any) -> pd.DataFrame:
    """Flatten a Fink schema payload into a readable DataFrame."""
    rows: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        for name, meta in schema.items():
            if isinstance(meta, dict) and _looks_like_field_group(meta):
                for field_name, field_meta in meta.items():
                    row = {"field": str(field_name), "family": name}
                    if isinstance(field_meta, dict):
                        row.update(field_meta)
                    else:
                        row["description"] = field_meta
                    rows.append(row)
            elif isinstance(meta, dict):
                row = {"field": name, "family": None}
                row.update(meta)
                rows.append(row)
            else:
                rows.append({"field": name, "family": None, "description": meta})
    elif isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("column")
                row = {"field": name}
                row.update(item)
                rows.append(row)
            else:
                rows.append({"field": str(item)})
    else:
        raise ValueError("Unsupported schema payload type")

    frame = pd.DataFrame(rows)
    if "field" in frame.columns:
        frame = frame.sort_values("field", na_position="last").reset_index(drop=True)
    return frame


def _looks_like_field_group(value: dict[str, Any]) -> bool:
    """Return true when a schema dictionary contains field-name children."""
    if not value:
        return False
    child_values = list(value.values())
    dict_children = [child for child in child_values if isinstance(child, dict)]
    if not dict_children:
        return False
    return any("type" in child or "doc" in child or "description" in child for child in dict_children)


def infer_internal_concept_map(fields: list[str]) -> dict[str, list[str]]:
    """Map available field names into preliminary internal data-model concepts."""
    lowered = {field: field.lower() for field in fields if field}

    def pick(*needles: str) -> list[str]:
        return [
            field
            for field, lower in lowered.items()
            if any(needle in lower for needle in needles)
        ]

    return {
        "object_id": pick("objectid", "diaobjectid", "diaobject", "ssobjectid"),
        "source_id": pick("candid", "diasourceid", "diasource", "alertid"),
        "ra": pick(":ra", " ra", "jradeg", "coord_ra", "ra"),
        "dec": pick(":dec", "jdedeg", "coord_dec", "declination", "dec"),
        "time": pick(":jd", "mjd", "midpointtai", "tai", "time", "timestamp"),
        "band_or_filter": pick("fid", "band", "filter"),
        "magnitude_or_flux": pick("mag", "flux", "psf", "snr"),
        "classification": pick("class", "classification", "rf_", "snn", "cats"),
        "tag": pick("tag"),
        "crossmatch_or_context": pick("xmatch", "simbad", "tns", "gaia", "host", "ssnamenr", "mpc"),
        "forced_photometry": pick("forced", "forcedsource", "diaforcedsource"),
        "cutout": pick("cutout", "stamp"),
    }


def summarize_tags(tags: Any) -> pd.DataFrame:
    """Normalize a tags/classes payload into a compact DataFrame."""
    rows: list[dict[str, Any]] = []
    if tags is None:
        return pd.DataFrame(columns=["tag"])
    if isinstance(tags, dict):
        for key, value in tags.items():
            row = {"tag": str(key)}
            if isinstance(value, dict):
                row.update(value)
            else:
                row["value"] = value
            rows.append(row)
    elif isinstance(tags, list):
        for item in tags:
            if isinstance(item, dict):
                tag = item.get("tag") or item.get("name") or item.get("class") or item.get("label")
                row = {"tag": tag}
                row.update(item)
                rows.append(row)
            else:
                rows.append({"tag": str(item)})
    else:
        rows.append({"tag": str(tags)})
    return pd.DataFrame(rows).sort_values("tag", na_position="last").reset_index(drop=True)


def internal_data_model_table(concept_map: dict[str, list[str]]) -> pd.DataFrame:
    """Create a readable internal data-model proposal table from inferred concepts."""
    concept_descriptions = {
        "object_id": "Object-level identifier fields",
        "source_id": "Alert/source-level identifier fields",
        "ra": "Right ascension fields",
        "dec": "Declination fields",
        "time": "Time, MJD, JD, or date fields",
        "band_or_filter": "Filter or band fields",
        "magnitude_or_flux": "Magnitude, flux, uncertainty, or S/N fields",
        "classification": "Classification or model-score fields",
        "tag": "Tag and quality/status fields",
        "crossmatch_or_context": "Catalog crossmatch and contextual fields",
        "forced_photometry": "Forced-photometry fields",
        "cutout": "Image cutout fields",
    }
    rows = []
    for concept, fields in concept_map.items():
        rows.append(
            {
                "internal_concept": concept,
                "description": concept_descriptions.get(concept, concept),
                "candidate_field_count": len(fields),
                "candidate_fields": ", ".join(fields[:20]),
            }
        )
    return pd.DataFrame(rows)


def describe_fink_prefix(field: str) -> str:
    """Describe common Fink field prefixes."""
    if ":" not in field:
        return "unprefixed or service-specific field"
    prefix = field.split(":", 1)[0]
    return {
        "i": "original alert/survey field",
        "d": "Fink science-module or database-added value",
        "v": "runtime-generated Fink value",
        "b": "cutout/image payload field",
        "f": "Fink/Rubin statistics, filter, or summary field",
        "key": "database index or key field",
    }.get(prefix, "unknown Fink column family")
