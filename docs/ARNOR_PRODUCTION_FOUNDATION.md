# Arnor Production Foundation (FINK-G2B)

This note records how production Fink LSST data is laid out on UW Arnor / Middle Earth and how the repository finds it. It was established in gate FINK-G2B (2026-10-04). No Light Static data has been ordered or downloaded under this layout yet.

## Data root precedence

Run manifests keep data paths relative (`data/raw/data_transfer/...`, `data/processed/data_transfer/...`, `outputs/data_transfer/...`). Those paths are resolved against one data root, chosen by a single rule (`src/fink_lsst/data_root.py`):

1. If `FINK_LSST_DATA_ROOT` is set, it is the data root. It must be a non-empty absolute path to an existing directory, must not contain `..`, and must not be a subdirectory of the Git checkout. Any violation stops the run with an error; the root is never created implicitly.
2. If it is unset, the data root is the repository checkout, which is the original local-first behaviour.

There is no config-file or CLI override, so there is only one place to look. Configs, run manifests, and source code always resolve against the checkout.

The unified runner (`scripts/run_analysis.py`) and `scripts/validate_run_manifest.py` honour the data root and print it. The runner refuses the `triage`, `inspect_raw`, and `next_action` stages while an external root is set, because those delegate to older Full Packet helper scripts that still read from the checkout. Other legacy scripts (REST extraction, smoke pipeline, Full Packet tooling) are unchanged and remain checkout-relative.

Manifest paths that are absolute must sit under the data root; the same validation rule as before now applies to the configured root. Existing manifests, including the Light Static baseline `configs/runs/full_week_light_static_2026-02-25_to_2026-03-04.yaml`, are unchanged. Pointed at Arnor they resolve to paths that do not exist there and report `raw_missing`; they describe the historical Mac-local delivery, not Arnor data.

## Arnor layout

| Purpose | Path | Notes |
|---|---|---|
| Data root | `/astro/store/shire/FINK` | mdarim:mdarim, mode 700, NFSv4 shire (not backed up) |
| Raw deliveries | `data/raw/` | immutable once accepted; `finkctl transfer -outdir` targets go here |
| Validated state | `data/validated/` | reserved; validation is recorded as manifest state, not a second copy |
| Processed runs | `data/processed/` | run-scoped (`data_transfer/runs/<run_name>/<run_id>/`) |
| Reports | `outputs/` | same role as the repo's `outputs/`; kept under that name so manifests resolve unchanged |
| Delivery manifests | `manifests/` | checksums, counts, Kafka evidence per topic |
| Logs | `logs/` | transfer and ingestion logs, credential-free |
| Cache | `cache/` | regenerable |
| Python environment | `/astro/store/shiren/mdarim/envs/fink-lsst-py311` | UW motd: conda envs on shiren |
| Temporary work | `/local/tmp/mdarim-fink-*` | UW motd: temporary files |
| Code releases | `~/opt/fink-lsst-analysis/releases/<git-sha>/` | read-only checkout of a pushed commit, same pattern as ANTARES |
| Fink credentials | `~/.finkclient/` (home, mode 700) | never on shire, never in Git |

`runs/` was not created as a top-level directory: the code already scopes runs inside `data/processed/` and `outputs/`.

## Environment

Built from the shared Miniconda base without modifying it:

```bash
export CONDA_PKGS_DIRS=/local/tmp/mdarim-fink-g2b/conda-pkgs PIP_CACHE_DIR=/local/tmp/mdarim-fink-g2b/pip-cache
/astro/apps9/opt/conda/conda/bin/conda create -p /astro/store/shiren/mdarim/envs/fink-lsst-py311 \
  --override-channels -c conda-forge python=3.11 pip
/astro/store/shiren/mdarim/envs/fink-lsst-py311/bin/python -m pip install "fink-client==12.2.0"
```

Result: Python 3.11.16, fink-client 12.2.0, `finkctl` CLI. The full package list is pinned in `configs/environments/arnor-fink-lsst-py311.pip-freeze.txt`. fink-client 12 replaces `fink_datatransfer` with `finkctl transfer` (same options) and `fink_client_register` with `finkctl auth register`.

## Working session

```bash
export PATH=/astro/store/shiren/mdarim/envs/fink-lsst-py311/bin:$PATH
export FINK_LSST_DATA_ROOT=/astro/store/shire/FINK
cd ~/opt/fink-lsst-analysis/releases/<git-sha>
PYTHONPATH=src python scripts/run_analysis.py --run-config configs/runs/<run>.yaml --stage validate_manifest
```

Long downloads run in a dedicated tmux session whose name starts with `fink-` so it never collides with the ANTARES sessions. Always pass `-nconsumers` explicitly: the default uses every logical CPU, which is 256 on this shared node.

## Authentication (user action)

fink-client 12 needs fresh registration; the v11 configuration is not migrated. The user runs this on Arnor, typing their own values:

```bash
umask 077
/astro/store/shiren/mdarim/envs/fink-lsst-py311/bin/finkctl auth register -survey lsst -username <username> -groupid <group_id> -servers <kafka_host:port>
chmod 600 ~/.finkclient/lsst_credentials.yml
```

Do not run `finkctl auth show`, which prints the credentials.

## Provenance that must live in Git

Shire is not backed up. Topic IDs, request windows, run manifests, environment pins, and validation summaries are small and must be committed (or copied to the backed-up home) so the project record survives a shire loss. Raw and processed bulk data stay on shire only.
