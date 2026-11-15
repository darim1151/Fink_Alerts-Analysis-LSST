"""Offline tests of the stellar-locus band in tools/colour_plot_kit/plot_colour_diagrams.py.

The band must have its 2-sigma width in BOTH colours. An earlier version drew fill_between(x, y - 2 sigma_y,
y + 2 sigma_y), which has no width in x where the locus runs vertically (reported for g-r ~ 1.3, r-i ~ 1.0)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "colour_plot_kit"))
import plot_colour_diagrams as kit  # noqa: E402


def synthetic_locus():
    """An L-shaped locus in r-i vs g-r: horizontal for g-i < 1.5, then vertical at g-r = 1.26 (as in the real file)."""
    gi = np.round(np.arange(0.5, 3.0001, 0.01), 2)
    gr = np.where(gi < 1.5, 0.2 + (gi - 0.5) * 1.06, 1.26)
    ri = np.where(gi < 1.5, 0.3 + (gi - 0.5) * 0.1, 0.4 + (gi - 1.5) * 1.0)
    d = {"g-i": gi, "g-r": gr, "grSig": np.full(len(gi), 0.048), "r-i": ri, "riSig": np.full(len(gi), 0.050)}
    for c, s in (("u-g", "ugSig"), ("i-z", "izSig"), ("z-y", "zySig")):
        d[c], d[s] = np.zeros(len(gi)), np.full(len(gi), 0.02)
    return pd.DataFrame(d)


def extent(X, Y, mask, at, axis):
    """Width of the region along x at fixed y (axis='x') or along y at fixed x (axis='y')."""
    if axis == "x":
        j = np.argmin(np.abs(Y[:, 0] - at))
        xs = X[j][mask[j]]
        return xs.min(), xs.max()
    i = np.argmin(np.abs(X[0] - at))
    ys = Y[:, i][mask[:, i]]
    return ys.min(), ys.max()


def test_vertical_part_has_two_sigma_width_in_g_minus_r():
    X, Y, mask = kit.locus_region(synthetic_locus(), "gr", "ri")
    lo, hi = extent(X, Y, mask, 1.0, "x")
    step = X[0, 1] - X[0, 0]
    assert lo == pytest.approx(1.26 - 2 * 0.048, abs=2 * step)
    assert hi == pytest.approx(1.26 + 2 * 0.048, abs=2 * step)


def test_horizontal_part_has_two_sigma_width_in_r_minus_i():
    X, Y, mask = kit.locus_region(synthetic_locus(), "gr", "ri")
    lo, hi = extent(X, Y, mask, 0.5, "y")      # g-r = 0.5 lies on the horizontal part, where r-i ~ 0.328
    step = Y[1, 0] - Y[0, 0]
    ri = 0.3 + (((0.5 - 0.2) / 1.06)) * 0.1
    assert lo == pytest.approx(ri - 2 * 0.050, abs=3 * step)
    assert hi == pytest.approx(ri + 2 * 0.050, abs=3 * step)


def test_the_old_one_dimensional_band_had_no_width_there():
    """Documents the bug: with only sigma(r-i), the band at r-i = 1.0 spans essentially one value of g-r."""
    sl = synthetic_locus()
    x, y, sy = sl["g-r"].to_numpy(), sl["r-i"].to_numpy(), sl["riSig"].to_numpy()
    inside = (y - 2 * sy <= 1.0) & (1.0 <= y + 2 * sy)
    assert x[inside].max() - x[inside].min() < 1e-9


def test_region_is_continuous_along_the_locus():
    X, Y, mask = kit.locus_region(synthetic_locus(), "gr", "ri")
    sl = synthetic_locus()
    for gr, ri in zip(sl["g-r"], sl["r-i"]):
        i = np.argmin(np.abs(X[0] - gr))
        j = np.argmin(np.abs(Y[:, 0] - ri))
        assert mask[j, i]


def test_non_positive_width_is_rejected():
    sl = synthetic_locus()
    sl.loc[10, "grSig"] = 0.0
    with pytest.raises(ValueError):
        kit.locus_region(sl, "gr", "ri")
