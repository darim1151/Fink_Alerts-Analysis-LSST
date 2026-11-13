"""Small, dependency-light helpers for all-sky maps in an Aitoff projection (equatorial coordinates).

Astronomical convention: right ascension increases to the LEFT, with RA = 0 deg at the centre of the map.
Matplotlib's "aitoff" axes take longitude in radians in [-pi, pi] increasing to the right, so a source at RA is drawn at
x = -wrap(RA), where wrap() maps RA into [-180, 180) deg. Unit-tested in tests/test_sky_projection.py.
"""
from __future__ import annotations

import numpy as np

FULL_SKY_DEG2 = 4 * np.pi * (180 / np.pi) ** 2  # 41,252.96 deg^2


def ra_to_x(ra_deg):
    """Map RA (deg, any range) to the Aitoff x coordinate in radians, RA increasing to the left, RA = 0 at the centre."""
    ra = np.mod(np.asarray(ra_deg, dtype=float), 360.0)
    wrapped = np.where(ra >= 180.0, ra - 360.0, ra)  # [-180, 180)
    return -np.radians(wrapped)


def dec_to_y(dec_deg):
    return np.radians(np.asarray(dec_deg, dtype=float))


def grid_edges(step_deg=1.0):
    """RA edges 0..360 and Dec edges -90..90 for a regular grid of cells step_deg on a side."""
    n_ra = int(round(360.0 / step_deg))
    n_dec = int(round(180.0 / step_deg))
    return np.linspace(0.0, 360.0, n_ra + 1), np.linspace(-90.0, 90.0, n_dec + 1)


def cell_index(ra_deg, dec_deg, step_deg=1.0):
    """Integer (i_ra, i_dec) of the cell containing each position. RA = 360 wraps to 0; Dec = +90 goes in the top row."""
    n_ra = int(round(360.0 / step_deg))
    n_dec = int(round(180.0 / step_deg))
    i_ra = np.floor(np.mod(np.asarray(ra_deg, dtype=float), 360.0) / step_deg).astype(int) % n_ra
    i_dec = np.clip(np.floor((np.asarray(dec_deg, dtype=float) + 90.0) / step_deg).astype(int), 0, n_dec - 1)
    return i_ra, i_dec


def cell_area_deg2(dec_edges_deg, step_ra_deg):
    """Solid angle (deg^2) of the cells in each Dec row: dRA * (sin dec_hi - sin dec_lo), converted to deg^2."""
    d = np.radians(np.asarray(dec_edges_deg, dtype=float))
    return np.radians(step_ra_deg) * (np.sin(d[1:]) - np.sin(d[:-1])) * (180 / np.pi) ** 2


def count_grid(i_ra, i_dec, counts, step_deg=1.0):
    """Accumulate counts into an (n_dec, n_ra) array."""
    n_ra = int(round(360.0 / step_deg))
    n_dec = int(round(180.0 / step_deg))
    g = np.zeros((n_dec, n_ra), dtype=np.int64)
    np.add.at(g, (np.asarray(i_dec), np.asarray(i_ra)), np.asarray(counts, dtype=np.int64))
    return g


def density_grid(counts_grid, step_deg=1.0):
    """Counts per deg^2 in each cell."""
    _, dec_edges = grid_edges(step_deg)
    area = cell_area_deg2(dec_edges, step_deg)[:, None]
    return counts_grid / area


def break_at_wrap(x, y):
    """Insert NaN between consecutive points whose x jumps by more than pi (where a curve crosses the map edge)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    jumps = np.where(np.abs(np.diff(x)) > np.pi)[0]
    return np.insert(x, jumps + 1, np.nan), np.insert(y, jumps + 1, np.nan)


def galactic_plane_radec(b_deg=0.0, n=1441):
    """RA, Dec (deg, ICRS) of the curve of constant Galactic latitude b, ordered by Galactic longitude."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    l = np.linspace(0.0, 360.0, n)
    c = SkyCoord(l=l * u.deg, b=np.full(n, b_deg) * u.deg, frame="galactic").icrs
    return c.ra.deg, c.dec.deg


def ecliptic_radec(n=1441):
    """RA, Dec (deg, ICRS) of the ecliptic (barycentric mean ecliptic, latitude 0)."""
    from astropy.coordinates import BarycentricMeanEcliptic, SkyCoord
    import astropy.units as u
    lon = np.linspace(0.0, 360.0, n)
    c = SkyCoord(lon=lon * u.deg, lat=np.zeros(n) * u.deg, frame=BarycentricMeanEcliptic()).icrs
    return c.ra.deg, c.dec.deg


def to_map_order(grid):
    """Reorder an (n_dec, n_ra) grid indexed by RA cell (RA increasing from 0) into the left-to-right column order of
    the Aitoff map, where x runs from -pi (RA 180 deg, approached from below) through 0 (RA 0) to +pi. Returns the x
    edges (radians), y edges (radians) and the reordered grid, ready for pcolormesh."""
    n_dec, n_ra = grid.shape
    step = 360.0 / n_ra
    half = n_ra // 2
    cols = [(half - 1 - k) % n_ra for k in range(n_ra)]
    x_edges = np.radians(np.linspace(-180.0, 180.0, n_ra + 1))
    y_edges = np.radians(np.linspace(-90.0, 90.0, n_dec + 1))
    assert abs(step * n_ra - 360.0) < 1e-9
    return x_edges, y_edges, grid[:, cols]


def vector_mean_radec(ra_deg, dec_deg):
    """Mean direction of a set of positions (robust to the RA = 0/360 wrap). Returns (ra, dec) in deg."""
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    x, y, z = np.mean(np.cos(dec) * np.cos(ra)), np.mean(np.cos(dec) * np.sin(ra)), np.mean(np.sin(dec))
    return float(np.mod(np.degrees(np.arctan2(y, x)), 360.0)), float(np.degrees(np.arctan2(z, np.hypot(x, y))))
