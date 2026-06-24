"""Registration/authentication checklist helpers."""

from __future__ import annotations


def render_registration_checklist() -> str:
    """Render manual Fink Data Transfer registration/authentication checklist."""
    return """# Fink Data Transfer Registration And Authentication Checklist

Data Transfer may require registration and authentication. Complete these steps manually through the official Fink/LSST Data Transfer portal or documented Fink channels.

## Do

- Confirm you have approved access for Rubin/LSST Data Transfer.
- Keep credentials in your password manager, keychain, or approved local auth mechanism.
- Use safe client/portal status pages if available to confirm access.
- Save only non-secret metadata, request IDs, delivery manifests, and local file inventories.
- Place delivered smoke files under `data/raw/data_transfer/smoke_delivery/`.

## Do Not

- Do not paste tokens, passwords, usernames, API keys, Kafka secrets, or auth outputs into this repository.
- Do not commit `.env` files.
- Do not save screenshots that reveal credentials.
- Do not submit a full-night request until the smoke delivery path has been inspected, ingested, and validated.

## If Access Is Slow

- REST remains useful while Data Transfer access is pending.
- Continue using REST for bounded candidate extraction and known-ID enrichment.
- Keep developing validation, dashboards, and comparison tooling against synthetic and REST-derived samples.
"""
