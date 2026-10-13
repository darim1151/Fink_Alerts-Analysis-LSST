# Colour plot kit (runs on your laptop)

Draws colour-colour diagrams of our Fink LSST DIA objects with the Rubin stellar locus overlaid, from a small
exported table. It needs no access to Arnor and never touches the raw alert data.

## Files

| File | What it is |
|---|---|
| `plot_colour_diagrams.py` | the plotting script |
| `requirements.txt` | the three Python packages it needs |
| `fink_dia_template_colours.csv.gz` | exported table: one row per DIA object with usable template magnitudes in ≥ 2 bands (sent separately; not in Git) |
| `RubinStellarLocus_COSMOS.txt` | stellar locus from Zeljko (sent separately) |

## One-time setup in VS Code (macOS)

1. Make a folder, e.g. `~/fink_colour_plots`, and put all four files in it.
2. In VS Code choose **File → Open Folder…** and open that folder.
3. Open a terminal with **Terminal → New Terminal**, then run these three lines one at a time:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. If VS Code asks which Python interpreter to use, pick the one inside `.venv`.

Next time, just open the folder and run `source .venv/bin/activate` in the terminal first.

## Making plots

Run these in the VS Code terminal. Each writes a PNG and a PDF into `figures/`.

```bash
# all DIA objects (grey) with the stellar locus (red band)
python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt

# highlight a subsample in blue, e.g. the 661 g-i > 2 objects of the earlier figures
python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt --select "has_gri and gi > 2" --name red_objects

# one row per SIMBAD group, or per Fink label
python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt --facet simbad_group
python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt --facet fink_cats_label

# the g-i > 2 objects split by Fink label (grey = all g-i > 2 objects)
python plot_colour_diagrams.py --data fink_dia_template_colours.csv.gz --locus RubinStellarLocus_COSMOS.txt --select "has_gri and gi > 2" --facet fink_cats_label --name red_by_label
```

Options:
- `--select` takes any pandas condition on the table columns, e.g. `"has_gri and gi > 2"`, `"gaia_parallax_mas / gaia_parallax_err_mas > 5"`, `'simbad_group == "QSO / AGN"'` or `'field == "Deep drilling fields"'`.
- `--facet` takes any column name.
- By default, Galactic bulge and plane fields are left out, because colours are not corrected for extinction. Add `--include-dusty` to keep them.
- `--out` sets the output folder and `--name` the file name.

The four panels are g−r vs u−g, r−i vs g−r, i−z vs r−i and z−y vs i−z. The grey map shows all objects that have the
two colours: log density plus contours enclosing 50/80/95% of them. The red band is the locus mean ± 2σ, drawn exactly
as in Zeljko's example (x = locus colour, y = next colour ± 2 × its sigma). Blue points are the selected subsample.
Each panel prints how many objects it shows.

## Table columns

| Column | Meaning |
|---|---|
| `dia_object_id` | Rubin DIA object ID |
| `ra_deg`, `dec_deg` | mean position of the object's delivered alerts |
| `field` | observing programme with most of the object's alerts (deep drilling, low-dust WFD, M49, bulge, plane, …) |
| `n_alerts_<band>` | number of delivered alerts in that band |
| `mag_<band>`, `magerr_<band>` | template PSF magnitude (AB) and its error; empty where the band is not usable |
| `fink_cats_code`, `fink_cats_label` | Fink CATS label at the latest alert (a classifier output, not a verified type) |
| `simbad_otype`, `simbad_group` | SIMBAD type from Fink's crossmatch within 1″ (latest successful crossmatch) and its group |
| `legacy_pstar` | Legacy Survey DR8 point-source probability from Fink's crossmatch |
| `gaia_parallax_mas`, `gaia_parallax_err_mas` | Gaia DR3 parallax and error from Fink's crossmatch |

Derived in the script: `ug`, `gr`, `ri`, `iz`, `zy` (adjacent colours), `gi`, and `has_ugr`, `has_gri`, `has_riz`, `has_izy`
(true when all three bands are usable). Use `has_gri` with `gi`: 1,772 more objects have usable g and i but not r,
so `"gi > 2"` alone gives 2,433 objects, not the 661 in the earlier figures.

## How the magnitudes are made

Every Rubin alert is one detection in one band at one time. Each alert also carries `templateFlux`: forced PSF
photometry, in that band, on the difference-imaging template at the DIA object's position. The template is a coadd
of earlier images, so it is a static reference. For each object and band we take the median `templateFlux` over all
its alerts in that band. We keep the band if that flux is positive and the median S/N is ≥ 5, and convert it to an AB
magnitude: `m = 31.4 − 2.5 log10(flux / nJy)`.

A colour therefore combines the static templates of two bands. It is not a simultaneous measurement, and a band is
available only if the object alerted in it. Nothing here is coadd CModel photometry. For galaxies, the template PSF
flux is measured at the object's position, not over the whole galaxy. These steps happen upstream, in the export on
Arnor; the kit only plots the result.
