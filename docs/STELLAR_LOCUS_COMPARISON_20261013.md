# Stellar locus on the colour-colour diagrams

Run `stellar-locus-figures-20261013-v1`. Locus file `RubinStellarLocus_COSMOS.txt` (SHA256 `e1878b2a…a737`): median colours
of COSMOS stars with r < 23 from the Rubin DP2 catalogue, tabulated against g−i from 0.20 to 3.80 in 0.01 steps (361
rows), with robust widths 0.741 × (q75 − q25). Data: `colour-plot-kit-export-20261013-v1` (159,777 DIA objects).
Galactic bulge and plane fields are excluded because no extinction correction is applied.

## Reading the file

The file's column header is a `#` comment line. The example `pd.read_csv(..., sep='\s+', header=0)` therefore fails
on it ("Expected 12 fields in line 3, saw 13"). The kit reads the column names from that comment line. Its parse is
identical to a manual parse of all 361 rows (maximum difference 0). Along the locus, u−g decreases in 28 steps and g−r
in 7 at the red end, so those bands fold slightly where `fill_between` follows the table order. r−i, i−z and z−y
increase monotonically.

## Figures (kit commit `7230489`)

L1 all objects; L2 Gaia-parallax stars (S/N > 5); L3 by SIMBAD group; L4 by Fink label; L5 the 661 g−i > 2 objects;
L6 those objects by Fink label. Each band is the locus mean ± 2σ, drawn as in the example (x = locus colour,
y = next colour ± 2 × its sigma).

## Check: our stars against the locus

For stars with a Gaia DR3 parallax S/N > 5 and 0.20 ≤ g−i ≤ 3.80, each colour minus the locus colour at the star's own
g−i:

| Colour | Stars | Median offset | Robust scatter | Locus σ (median) |
|---|---:|---:|---:|---:|
| u−g | 13 | −0.12 | 0.22 | 0.24 |
| g−r | 578 | +0.036 | 0.086 | 0.047 |
| r−i | 578 | −0.036 | 0.086 | 0.050 |
| i−z | 224 | −0.006 | 0.093 | 0.024 |
| z−y | 12 | +0.017 | 0.014 | 0.018 |

The g−r and r−i offsets are equal and opposite because both are evaluated at fixed g−i. The g−r offset is +0.015 for
g−i < 1.5 (112 stars) and +0.042 for g−i ≥ 1.5 (466).

Our template colours of alerting stars agree with the Rubin COSMOS locus to a few hundredths of a magnitude in g−r,
r−i and i−z, and the bands agree with no visible offset. Our scatter is 2–4 times the locus width. That is expected for
variable stars measured through alert templates, and it fits the template-flux variations noted in the
magnitude-derivation note. The u−g and z−y checks rest on only 13 and 12 stars.
