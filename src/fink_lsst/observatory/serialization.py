"""The single rendezvous for future shared Bundle V1 serialization.

Native JSON is an internal handoff format, explicitly NOT Observatory Bundle V1.
The common serializer remains closed until the owner's exact schema is supplied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from enum import Enum

NATIVE_TO_COMMON_PROPOSAL = {
    "manifest": ["native_model_version", "product_kind", "authority", "sampling"],
    "basis": ["basis"],
    "capabilities": ["capabilities.entries"],
    "time": ["temporal"],
    "sky": ["sky"],
    "entities": ["entities", "broker_snapshots"],
    "features": ["features"],
    "provenance": ["provenance"],
}


def _native(value):
    if is_dataclass(value):
        return _native(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _native(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_native(x) for x in value]
    return value


def canonical_bytes(value):
    """Strict finite JSON, UTF-8, sorted keys; no runtime timestamps."""
    return (
        json.dumps(
            _native(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def native_artifact(model):
    payload = _native(model)
    return dict(
        format="FINK NATIVE DOMAIN HANDOFF / PROVISIONAL",
        shared_contract_binding="UNBOUND",
        payload_sha256=sha256(payload),
        payload=payload,
    )


class SharedContractUnbound(ValueError):
    pass


def serialize_observatory_bundle_v1(model):
    raise SharedContractUnbound(
        "Bundle V1 owner schema identity/version/SHA256 not supplied; native handoff only"
    )
