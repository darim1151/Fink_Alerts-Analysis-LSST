# Fink Data Reset Workflow

This workflow resets local generated Fink data before starting a clean scientific baseline analysis. It is intended for the transition from partial full-packet stress testing to a fresh first-week LSST Light static packet study.

The reset is needed because previous local Data Transfer experiments included partial full-packet downloads, smoke deliveries, processed tables, quick-analysis outputs, figures, and reports. Keeping those artifacts in active paths risks mixing incomplete stress-test material with the next baseline analysis.

The reset removes local Fink artifacts only. It does not affect remote Fink jobs or GitHub code.

## What Is Preserved

- Source code under `src/`
- Scripts under `scripts/`
- Notebooks under `notebooks/`
- Documentation under `docs/`
- Configuration under `configs/`
- Tests under `tests/`
- Git history and `.git/`
- Project metadata such as `README.md`, `pyproject.toml`, and `requirements.txt`

## What Is Quarantined

The cleanup tool targets only these generated-data roots:

- `data/raw/data_transfer`
- `data/processed/data_transfer`
- `outputs/data_transfer`

The tool also detects root-level generated schema dumps such as `avro_schema_ftransfer_*.json` and `arrow_schema_ftransfer_*.metadata`, but it does not automatically quarantine them.

## Why Quarantine First

Quarantine is safer than immediate deletion because it removes old artifacts from active analysis paths while keeping them locally recoverable for inspection. Permanent deletion is a separate explicit command and should happen only after the reset has been verified.

## Dry Run

Run the default dry-run before moving anything:

```bash
python3 scripts/cleanup_fink_data_workspace.py --dry-run
```

This writes an audit plan under `outputs/maintenance/fink_data_cleanup_<UTC_TIMESTAMP>/`.

## Quarantine

After reviewing the dry-run plan, move generated artifacts into quarantine:

```bash
python3 scripts/cleanup_fink_data_workspace.py --quarantine
```

The quarantine directory is:

```text
outputs/maintenance/fink_data_quarantine_<UTC_TIMESTAMP>/
```

After quarantine, the tool recreates empty active directories:

- `data/raw/data_transfer`
- `data/processed/data_transfer`
- `outputs/data_transfer`

## Permanent Deletion

Do not permanently delete the quarantine until the reset has been reviewed. When approved, delete a specific quarantine directory:

```bash
python3 scripts/cleanup_fink_data_workspace.py --delete-quarantine --quarantine-dir outputs/maintenance/fink_data_quarantine_<UTC_TIMESTAMP>
```

The tool refuses deletion paths that do not look like cleanup quarantine directories directly under `outputs/maintenance`.

## Verify Clean Active Workspace

Check the active generated-data roots:

```bash
du -sh data/raw/data_transfer data/processed/data_transfer outputs/data_transfer 2>/dev/null || true
find data/raw/data_transfer -maxdepth 3 -type d | sort
find data/processed/data_transfer -maxdepth 3 -type d | sort
find outputs/data_transfer -maxdepth 3 -type d | sort
git status --short -uall
python3 scripts/check_commit_readiness.py
```

Generated raw data, processed data, Parquet outputs, figures, and quick-analysis reports should not appear as Git commit candidates.

## Next Fink Job Parameters

- Portal: `https://lsst.fink-portal.org/download`
- Survey: `LSST`
- Start date: `2026-02-25`
- Stop date: `2026-03-04`
- Filter: no filter / all alerts
- Content: `Light static packet`
- Cutouts/images/FITS: no
