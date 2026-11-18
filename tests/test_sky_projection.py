"""Offline tests of the all-sky map helpers used for the Aitoff figure (scripts/sky_projection.py)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sky_projection as sp  # noqa: E402


def test_ra_increases_to_the_left_with_zero_at_centre():
    x = sp.ra_to_x([0.0, 90.0, 270.0, 359.0, 1.0])
    assert x[0] == pytest.approx(0.0)
    assert x[1] == pytest.approx(-np.pi / 2)   # RA 90 deg is left of centre
    assert x[2] == pytest.approx(np.pi / 2)    # RA 270 deg is right of centre
    assert x[3] == pytest.approx(np.radians(1.0))
    assert x[4] == pytest.approx(-np.radians(1.0))


def test_ra_wraps_and_stays_on_the_map():
    ra = np.array([-10.0, 360.0, 720.0 + 45.0, 180.0, 179.999])
    x = sp.ra_to_x(ra)
    assert np.all(np.abs(x) <= np.pi)
    assert x[0] == pytest.approx(np.radians(10.0))
    assert x[1] == pytest.approx(0.0)
    assert x[2] == pytest.approx(-np.radians(45.0))
    assert x[3] == pytest.approx(np.pi)        # RA 180 is on the right-hand edge
    assert x[4] == pytest.approx(-np.radians(179.999))


def test_cell_areas_cover_the_full_sky():
    for step in (1.0, 2.0, 0.5):
        _, dec_edges = sp.grid_edges(step)
        n_ra = int(round(360 / step))
        total = sp.cell_area_deg2(dec_edges, step).sum() * n_ra
        assert total == pytest.approx(sp.FULL_SKY_DEG2, rel=1e-12)
    assert sp.FULL_SKY_DEG2 == pytest.approx(41252.96, abs=0.01)


def test_cell_index_edges_and_counts_conserved():
    ra = np.array([0.0, 359.999, 360.0, 0.5, 180.0, 10.2])
    dec = np.array([-90.0, 89.999, 90.0, 0.0, -0.5, 45.7])
    i_ra, i_dec = sp.cell_index(ra, dec)
    assert list(i_ra) == [0, 359, 0, 0, 180, 10]
    assert list(i_dec) == [0, 179, 179, 90, 89, 135]
    g = sp.count_grid(i_ra, i_dec, np.ones(len(ra)))
    assert g.shape == (180, 360)
    assert g.sum() == len(ra)


def test_density_times_area_recovers_counts():
    rng = np.random.default_rng(1)
    g = rng.integers(0, 50, size=(180, 360))
    dens = sp.density_grid(g)
    _, dec_edges = sp.grid_edges(1.0)
    back = dens * sp.cell_area_deg2(dec_edges, 1.0)[:, None]
    assert np.allclose(back, g)


def test_break_at_wrap_inserts_gaps_only_at_map_edge():
    x = np.array([3.0, 3.1, -3.1, -3.0, -2.9])
    y = np.zeros_like(x)
    xb, yb = sp.break_at_wrap(x, y)
    assert len(xb) == len(x) + 1
    assert np.isnan(xb[2]) and np.isnan(yb[2])
    xs, _ = sp.break_at_wrap(np.linspace(-1, 1, 20), np.zeros(20))
    assert not np.isnan(xs).any()


def test_galactic_plane_and_ecliptic_curves():
    from astropy.coordinates import BarycentricMeanEcliptic, SkyCoord
    import astropy.units as u
    ra, dec = sp.galactic_plane_radec()
    b = SkyCoord(ra=ra * u.deg, dec=dec * u.deg).galactic.b.deg
    assert np.max(np.abs(b)) < 1e-6
    # The Galactic centre (l = b = 0) is at RA 266.40, Dec -28.94 (ICRS)
    assert ra[0] == pytest.approx(266.405, abs=0.01)
    assert dec[0] == pytest.approx(-28.936, abs=0.01)
    ra, dec = sp.ecliptic_radec()
    lat = SkyCoord(ra=ra * u.deg, dec=dec * u.deg).transform_to(BarycentricMeanEcliptic()).lat.deg
    assert np.max(np.abs(lat)) < 1e-6
    assert np.max(np.abs(dec)) == pytest.approx(23.44, abs=0.01)  # obliquity


def test_map_order_puts_each_cell_under_its_own_x():
    g = np.zeros((180, 360), dtype=int)
    for ra, dec in ((10.5, 20.5), (200.5, -30.5), (359.5, 0.5), (179.5, 60.5)):
        i_ra, i_dec = sp.cell_index([ra], [dec])
        g[i_dec[0], i_ra[0]] += 1
    x_edges, y_edges, m = sp.to_map_order(g)
    for ra, dec in ((10.5, 20.5), (200.5, -30.5), (359.5, 0.5), (179.5, 60.5)):
        xc = sp.ra_to_x(ra)
        k = np.searchsorted(x_edges, xc) - 1
        j = np.searchsorted(y_edges, np.radians(dec)) - 1
        assert m[j, k] == 1, (ra, dec)
    assert m.sum() == 4
    assert len(x_edges) == 361 and x_edges[0] == pytest.approx(-np.pi) and x_edges[-1] == pytest.approx(np.pi)


def test_vector_mean_handles_ra_wrap():
    ra, dec = sp.vector_mean_radec([359.0, 1.0], [0.0, 0.0])
    assert min(ra, 360 - ra) == pytest.approx(0.0, abs=1e-9)
    assert dec == pytest.approx(0.0, abs=1e-9)
    ra, dec = sp.vector_mean_radec([150.0, 150.2], [2.0, 2.4])
    assert ra == pytest.approx(150.1, abs=1e-3)
    assert dec == pytest.approx(2.2, abs=1e-3)
