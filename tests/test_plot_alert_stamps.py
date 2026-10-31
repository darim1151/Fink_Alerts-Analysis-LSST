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
                header = tan_wcs(30.0 * j).to_header() if j < 2 else fits.Header()
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
    assert summary["with_wcs"] == 12 and summary["stamp_shapes"] == ["31x31"]
    assert (out / "alert_stamps.png").stat().st_size > 10_000 and (out / "alert_stamps.pdf").exists()
    props = list(csv.DictReader(open(out / "stamp_properties.csv")))
    assert len(props) == 18 and {p["name"] for p in props} == {f"G{g}-0{j}" for g in (1, 2) for j in (1, 2, 3)}
    assert [p["n_nonfinite"] for p in props if p["kind"] == "difference" and p["name"] == "G1-01"] == ["31"]
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
