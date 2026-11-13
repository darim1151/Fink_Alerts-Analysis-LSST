# Science notebooks: Fink LSST alert analysis

Six notebooks reproduce every figure, table and check of the five-window analysis
(alerts 2026-02-25 → 2026-07-13). They call the same functions that made the delivered figures, in
`scripts/alert_colour_diagrams.py`, `scripts/alert_science_suite.py`, `scripts/red_objects_and_red_bands.py`,
`scripts/export_colour_plot_table.py`, `scripts/fink_checks.py`, `scripts/paper_sample_figures.py` and
`tools/colour_plot_kit/plot_colour_diagrams.py`.
Run on the same catalog, they give the same numbers.

| Notebook | What it makes | Needs | Time |
|---|---|---|---|
| `01_template_colours` | per-object template photometry; colour figures 1–3 | catalog | ~3 min |
| `02_science_suite` | S1–S3, C1, K1–K2, L1, V1–V2, D1–D2, Q1, O1 and tables | 01 | ~8 min |
| `03_red_objects_and_red_bands` | R0–R3, D1, Z1–Z2 (g−i > 2 objects, i/z/y diagrams) | 01, 02 | ~1 min |
| `04_stellar_locus` | L1–L6 with the Rubin stellar locus; Gaia-star residuals; exported colour table | 01, 02, locus file | ~1 min |
| `05_checks_and_audits` | raw re-derivation, crossmatch failures, RR Lyrae and labels, RTN-099 u conversion, magnitude assembly | 01, 02 | ~5 min |
| `06_paper_additions` | P1–P3 sample description and SIMBAD-matched vs unmatched magnitudes; T1–T3 same-night difference-flux (transient) colours; per-figure counts | 01, 02, 04, locus file | ~2 min |

Run them in order. Each one writes into its own new folder under `OUTPUT_ROOT/RUN_ID/`. Nothing is ever overwritten,
and nothing is written into the raw data, the catalog or the manifests.

## Where they run

The alerts and the catalog live on the data host (Arnor), so the notebooks run there. VS Code on your laptop
connects to it.

1. Install the VS Code extensions **Remote - SSH**, **Python** and **Jupyter**.
2. Press **F1 → Remote-SSH: Connect to Host… → arnor**, then **File → Open Folder…** and open the code folder on Arnor
   (for example `/astro/store/shiren/mdarim/fink-analysis-code/<commit>`).
3. Open `notebooks/science/01_template_colours.ipynb`. Under **Select Kernel → Python Environments**, choose the
   notebook environment `/astro/store/shiren/mdarim/envs/fink-lsst-notebooks-py311/bin/python`. It has the same
   package versions as the environment that made the delivered figures, plus Jupyter support.
4. Click **Run All**. Do the same for 02, 03, 04, 05 and 06.

Without VS Code, the same notebooks run in a terminal on Arnor:

```bash
cd notebooks/science
/astro/store/shiren/mdarim/envs/fink-lsst-analysis-g4b-py311/bin/python run_notebook.py --all
```

## Configuration

All settings are in `_setup.py`, at the top (or in the environment variables named there):

| Setting | Meaning |
|---|---|
| `CATALOG` | the analytical catalog to analyse (opened read-only) |
| `LOCUS_FILE` | the Rubin COSMOS stellar-locus file |
| `RUN_ID` | name of this run; use a new name for every full run |
| `OUTPUT_ROOT` | where run folders are created (default `/astro/store/shire/FINK/outputs`) |

## When Rubin delivers more alerts

The notebooks read everything from the catalog: the list of raw files, the cohort dates, the populations and the
counts. Nothing in them is tied to the current five deliveries.

1. Acquire and validate the new Fink delivery with the existing pipeline. This step is gated and needs explicit
   approval; the notebooks never contact Fink.
2. Add its acquisition to a new cohort file under `configs/analysis_cohorts/` and build a new analytical catalog:
   `scripts/build_analytical_catalog.py build --cohort <new cohort> --data-root /astro/store/shire/FINK --run-id <new id>`.
3. In `_setup.py`, set `CATALOG` to the new `analytics.duckdb` and `RUN_ID` to a new name.
4. Run notebooks 01 → 06.

The "reference values" printed in the notebook text are for the current cohort. A larger cohort gives larger numbers.

## Reproducibility notes

- The per-object table has a fixed row order (by DIA object ID).
- Object mean positions are averages computed in parallel by DuckDB and can differ between runs by about 1e-13 degrees.
  Nothing downstream depends on that level.
- Random numbers (bootstrap intervals in K2) use a fixed seed.
- Each `summary.json` records the code version, input SHA256s and package versions.
