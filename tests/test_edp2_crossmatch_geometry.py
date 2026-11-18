"""Offline tests of the geometry helpers in scripts/edp2_crossmatch_rsp.py (no RSP access needed)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edp2_crossmatch_rsp as ex  # noqa: E402


def square(ra0, dec0, half_deg):
    """A sky 'square' with great-circle edges: vertices at +-half in the gnomonic plane about (ra0, dec0)."""
    c = ex._unit(np.array([ra0]), np.array([dec0]))[0]
    east = np.cross([0, 0, 1.0], c)
    east /= np.linalg.norm(east)
    north = np.cross(c, east)
    t = np.tan(np.radians(half_deg))
    verts = []
    for x, y in ((-t, -t), (t, -t), (t, t), (-t, t)):
        v = c + x * east + y * north
        v /= np.linalg.norm(v)
        verts.append((np.degrees(np.arctan2(v[1], v[0])) % 360, np.degrees(np.arcsin(v[2]))))
    return np.array(verts)


def test_angsep_known_values():
    assert ex.angsep_arcsec(10.0, 0.0, 10.0, 1.0) == pytest.approx(3600.0, rel=1e-12)
    assert ex.angsep_arcsec(359.9999, 0.0, 0.0001, 0.0) == pytest.approx(0.72, rel=1e-9)
    assert ex.angsep_arcsec(0.0, 89.9, 180.0, 89.9) == pytest.approx(720.0, rel=1e-9)


def test_offset_positions_move_exactly_and_isotropically():
    rng = np.random.default_rng(3)
    ra = rng.uniform(0, 360, 20000)
    dec = np.degrees(np.arcsin(rng.uniform(-0.99, 0.99, 20000)))
    ra2, dec2 = ex.offset_positions(ra, dec, 30.0, seed=1)
    d = ex.angsep_arcsec(ra, dec, ra2, dec2)
    assert np.max(np.abs(d - 30.0)) < 1e-6
    assert ((ra2 >= 0) & (ra2 < 360)).all()
    # isotropy: mean displacement vector in the local frame is ~0
    dra = ((ra2 - ra + 180) % 360 - 180) * np.cos(np.radians(dec)) * 3600
    ddec = (dec2 - dec) * 3600
    assert abs(dra.mean()) < 0.5 and abs(ddec.mean()) < 0.5
    # same seed -> same offsets
    ra3, dec3 = ex.offset_positions(ra, dec, 30.0, seed=1)
    assert np.array_equal(ra2, ra3) and np.array_equal(dec2, dec3)


def test_parse_polygons():
    p = ex.parse_polygons(["POLYGON ICRS 1 2 3 4 5 6 7 8", "POLYGON ICRS 10 20 30 40 50 60 70 80"])
    assert p.shape == (2, 4, 2)
    assert p[1, 2].tolist() == [50.0, 60.0]
    with pytest.raises(ValueError):
        ex.parse_polygons(["POLYGON ICRS 1 2 3 4 5 6 7 8", "POLYGON ICRS 1 2 3 4 5 6"])


@pytest.mark.parametrize("ra0,dec0", [(150.1, 2.2), (0.05, -30.0), (359.95, 10.0), (60.0, -48.0), (200.0, 85.0)])
def test_inside_patches_square(ra0, dec0):
    half = 0.09
    poly = square(ra0, dec0, half)[None]
    # points just inside / just outside each edge, built in the same gnomonic frame
    c = ex._unit(np.array([ra0]), np.array([dec0]))[0]
    east = np.cross([0, 0, 1.0], c)
    east /= np.linalg.norm(east)
    north = np.cross(c, east)
    t = np.tan(np.radians(half))
    pts, expect = [], []
    for f, ok in ((0.0, True), (0.98, True), (1.02, False), (1.5, False)):
        for x, y in ((f * t, 0), (-f * t, 0), (0, f * t), (0, -f * t), (f * t, f * t)):
            v = c + x * east + y * north
            v /= np.linalg.norm(v)
            pts.append((np.degrees(np.arctan2(v[1], v[0])) % 360, np.degrees(np.arcsin(v[2]))))
            expect.append(ok)
    pts = np.array(pts)
    got = ex.inside_patches(pts[:, 0], pts[:, 1], poly, np.array([ra0]), np.array([dec0]))
    assert got.tolist() == expect


def test_inside_patches_picks_the_right_patch_among_many():
    cen = [(10 + 0.18 * i, -5 + 0.18 * j) for i in range(5) for j in range(5)]
    poly = np.stack([square(r, d, 0.09) for r, d in cen])
    cra, cdec = np.array(cen).T
    # a point in the gap far outside all patches, and points at every centre
    got = ex.inside_patches(np.r_[cra, 20.0], np.r_[cdec, 20.0], poly, cra, cdec)
    assert got[:-1].all() and not got[-1]
