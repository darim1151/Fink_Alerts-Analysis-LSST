# Fink Data Transfer Registration And Authentication

Data Transfer may require registration and authentication before files can be delivered. This repository does not store credentials and does not submit jobs automatically.

## Manual Access Steps

- Visit the official Fink/LSST Data Transfer portal or documented Fink access channel.
- Request or confirm Rubin/LSST Data Transfer access.
- Keep credentials in a password manager, keychain, or approved local auth store.
- Use only documented, safe status pages or commands to confirm access.

## Do Not Save

- Tokens
- API keys
- Passwords
- Usernames tied to credentials
- Kafka secrets
- `.env` files
- Authentication command outputs
- Screenshots that reveal secrets

## Safe Artifacts To Save

- Non-secret request drafts
- Non-secret request IDs
- Delivery manifests without auth fields
- Local file inventories
- Schema summaries
- Validation reports

## After Access Is Approved

1. Run `python scripts/prepare_data_transfer_smoke_request.py`.
2. Review `outputs/data_transfer/request_drafts/SMOKE_MANUAL_PORTAL_CHECKLIST.md`.
3. Submit only a tiny smoke request manually if the portal supports it.
4. Place delivered files under `data/raw/data_transfer/smoke_delivery/`.
5. Run `python scripts/run_data_transfer_smoke_ingestion.py`.
6. Review validation before considering any full-night request.

REST remains useful for bounded/candidate extraction and known-ID enrichment while registration or delivery is pending.
