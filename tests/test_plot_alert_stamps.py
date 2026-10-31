import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

fits = pytest.importorskip("astropy.io.fits")
from astropy.wcs import WCS  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "plot_alert_stamps.py"
COLUMNS = ["name", "group", "dia_object_id", "dia_source_id", "band", "snr", "science_file", "science_sha256",
           "template_file", "template_sha256", "difference_file", "difference_sha256", "status"]


def load_module():
    spec = importlib.util.spec_from_file_location("plot_alert_stamps", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def tan_wcs(rot_deg=0.0, n=31):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [150.0, 2.0]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    s, t = 0.2 / 3600.0, np.deg2rad(rot_deg)
    w.wcs.cd = [[-s * np.cos(t), s * np.sin(t)], [s * np.sin(t), s * np.cos(t)]]
    return w


def fink_header(rot_deg, rotpa):
    """A header shaped like Fink's stamps: CRVAL/CRPIX/PC and ROTPA, but no CTYPE."""
    h = tan_wcs(rot_deg).to_header()
    del h["CTYPE1"], h["CTYPE2"]
    h["ROTPA"] = rotpa
    h["BUNIT"] = "nJy"
    return h


def make_run(tmp_path, n_groups=2, per_group=3):
    run = tmp_path / "alert-stamps-test"
    (run / "stamps").mkdir(parents=True)
    rng = np.random.default_rng(1)
    rows = []
    for g in range(n_groups):
        for j in range(per_group):
            name = f"G{g + 1}-{j + 1:02d}"
            row = {"name": name, "group": f"Group {g + 1}", "dia_object_id": str(10 + j), "dia_source_id": str(100 + j),
                   "band": "r", "snr": "12.5", "status": "ok"}
            for k, kind in enumerate(("science", "template", "difference")):
                data = rng.normal(0, 1, (31, 31))
                data[15, 15] += 50
                if kind == "difference" and j == 0:
                    data[0, :] = np.nan
                header = fink_header(30.0 * j, rotpa=30.0 * j + (40.0 if j == 1 else 0.3)) if j < 2 else fits.Header()
                if j == 1:   # this one keeps its CTYPE but its ROTPA disagrees with the WCS
                    header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
                path = run / "stamps" / f"{name}_{100 + j}_{kind}.fits"
                fits.PrimaryHDU(data.astype("float32"), header=header).writeto(path)
                row[f"{kind}_file"] = f"stamps/{path.name}"
                row[f"{kind}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(row)
    rows.append({**rows[0], "name": "G9-01", "group": "Failed", "status": "failed: test"})
    with open(run / "stamps.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    (run / "summary.json").write_text("{}\n")
    return run


def test_compass_follows_the_wcs():
    m = load_module()
    north_up = m.compass(tan_wcs(0.0), (31, 31))
    assert np.allclose(north_up["N"], [0, 1], atol=1e-6) and np.allclose(north_up["E"], [-1, 0], atol=1e-6)
    rotated = m.compass(tan_wcs(90.0), (31, 31))
    assert np.allclose(rotated["N"], [-1, 0], atol=1e-6) or np.allclose(rotated["N"], [1, 0], atol=1e-6)
    assert abs(float(np.dot(rotated["N"], rotated["E"]))) < 1e-6
    assert m.pixel_scale_arcsec(tan_wcs()) == pytest.approx(0.2)


def test_ctype_less_header_is_read_as_tan_and_checked_against_rotpa():
    m = load_module()
    h = fink_header(25.0, rotpa=25.5)
    wcs, source = m.stamp_wcs(h)
    assert source == "TAN assumed"
    ref = tan_wcs(25.0)
    for x, y in ((0, 0), (15, 15), (30, 7)):
        a, b = wcs.pixel_to_world(x, y), ref.pixel_to_world(x, y)
        assert a.separation(b).arcsec < 1e-6
    assert m.pa_of_y_axis(wcs, (31, 31)) == pytest.approx(25.0, abs=1e-3)
    assert m.dpa_from_rotpa(h, wcs, (31, 31)) == pytest.approx(-0.5, abs=1e-3)
    assert m.orientation_ok(h, wcs, (31, 31), 3.0)
    h["ROTPA"] = 300.0
    assert not m.orientation_ok(h, wcs, (31, 31), 3.0)
    assert m.stamp_wcs(fits.Header())[0] is None
    no_scale = fits.Header()
    no_scale.update(CRVAL1=150.0, CRVAL2=2.0, CRPIX1=1.0, CRPIX2=1.0)
    assert m.stamp_wcs(no_scale) == (None, "none")
    with_ctype = tan_wcs(0.0).to_header()
    assert m.stamp_wcs(with_ctype)[1] == "header"
    assert m.orientation_ok(with_ctype, m.stamp_wcs(with_ctype)[0], (31, 31), 3.0)   # no ROTPA: nothing to check


def test_difference_scale_is_symmetric_and_handles_nan():
    m = load_module()
    d = np.array([[-5.0, 1.0], [np.nan, 2.0]])
    n = m.norms(d, "difference")
    assert n.vmin == -n.vmax and n.vmax > 4.9
    s = m.norms(np.array([[1.0, 2.0], [3.0, np.nan]]), "science")
    assert s.vmin < s.vmax
    assert m.norms(np.full((2, 2), np.nan), "template").vmax == 1


def test_main_plots_checked_stamps_and_skips_failed_targets(tmp_path):
    m = load_module()
    run = make_run(tmp_path)
    out = tmp_path / "fig"
    m.main(["--stamps", str(run), "--out", str(out), "--code-sha", "test"])
    summary = json.loads((out / "summary.json").read_text())
    assert summary["n_targets"] == 6 and summary["n_stamps_checked"] == 18
    assert summary["wcs_sources"] == {"TAN assumed": 6, "header": 6, "none": 6}
    assert summary["compass_drawn"] == 2 and summary["stamp_shapes"] == ["31x31"]
    assert summary["dpa_range_deg"][0] == pytest.approx(-40.0, abs=1e-3)
    assert (out / "alert_stamps.png").stat().st_size > 10_000 and (out / "alert_stamps.pdf").exists()
    props = list(csv.DictReader(open(out / "stamp_properties.csv")))
    assert len(props) == 18 and {p["name"] for p in props} == {f"G{g}-0{j}" for g in (1, 2) for j in (1, 2, 3)}
    assert [p["n_nonfinite"] for p in props if p["kind"] == "difference" and p["name"] == "G1-01"] == ["31"]
    drawn = {p["name"] for p in props if p["kind"] == "template" and p["compass_drawn"] == "True"}
    assert drawn == {"G1-01", "G2-01"}
    with pytest.raises(FileExistsError):
        m.main(["--stamps", str(run), "--out", str(out), "--code-sha", "test"])


def test_checksum_mismatch_stops_before_writing(tmp_path):
    m = load_module()
    run = make_run(tmp_path)
    victim = next((run / "stamps").glob("G2-02_*_template.fits"))
    victim.write_bytes(victim.read_bytes()[:-2880] + b" " * 2880)
    with pytest.raises(ValueError, match="checksum mismatch"):
        m.main(["--stamps", str(run), "--out", str(tmp_path / "fig"), "--code-sha", "test"])
    assert not (tmp_path / "fig").exists()
