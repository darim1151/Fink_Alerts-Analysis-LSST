import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("astropy")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "crossmatch_gaia_irsa.py"


def load_module():
    spec = importlib.util.spec_from_file_location("crossmatch_gaia_irsa", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def objects(n=50):
    rng = np.random.default_rng(3)
    return pd.DataFrame({"dia_object_id": 170000000000000000 + np.arange(n, dtype=np.int64) * 7,
                         "ra_deg": rng.uniform(0, 360, n), "dec_deg": rng.uniform(-50, 10, n)})


class FakeResult:
    def __init__(self, df, status=("OK", None)):
        self.df, self.status = df, status

    def to_table(self):
        from astropy.table import Table
        return Table.from_pandas(self.df)


class FakeService:
    """Returns one Gaia row per uploaded object with an odd oid; can fail first or overflow."""

    calls = 0
    fail_first = 0
    overflow = False

    def run_sync(self, query, uploads, maxrec):
        m = load_module()
        FakeService.calls += 1
        if FakeService.fail_first > 0:
            FakeService.fail_first -= 1
            raise OSError("TLS connection closed")
        assert "TAP_UPLOAD.u" in query and "gaia_dr3_source" in query
        u = uploads["u"].to_pandas()
        u = u[u.oid % 2 == 1]
        df = pd.DataFrame({"oid": u.oid.to_numpy(np.int64)})
        for c in m.GAIA_COLUMNS:
            df[c] = (np.arange(len(u), dtype=np.int64) + u.oid.to_numpy() * 10) if c == "source_id" else 1.0
        df["ra"], df["dec"] = u.ra.to_numpy(), u.dec.to_numpy()
        return FakeResult(df, ("OVERFLOW", None) if FakeService.overflow else ("OK", None))


@pytest.fixture(autouse=True)
def reset_fake(monkeypatch):
    FakeService.calls, FakeService.fail_first, FakeService.overflow = 0, 0, False


def test_positions_shift_sample_and_order():
    m = load_module()
    t = objects(400)
    p = m.prepare_positions(t)
    assert len(p) == 400 and list(p.columns) == ["oid", "ra", "dec"]
    assert (np.diff(np.floor(p.dec.to_numpy())) >= 0).all()
    shifted = m.prepare_positions(t, shift_arcsec=60.0).set_index("oid")
    base = p.set_index("oid")
    assert np.allclose(shifted.dec - base.loc[shifted.index].dec, 60 / 3600.0)
    assert np.allclose(shifted.ra, base.loc[shifted.index].ra)
    a = m.prepare_positions(t, sample_fraction=0.25, seed=5)
    b = m.prepare_positions(t.sample(frac=1.0, random_state=2), sample_fraction=0.25, seed=5)
    assert set(a.oid) == set(b.oid) and 60 < len(a) < 140
    assert set(a.oid) != set(m.prepare_positions(t, sample_fraction=0.25, seed=6).oid)
    bad = t.copy()
    bad.loc[0, "dec_deg"] = np.nan
    with pytest.raises(ValueError):
        m.prepare_positions(bad)


def test_query_text_uses_degrees():
    m = load_module()
    q = m.query_text(3.0)
    assert "CIRCLE('ICRS', u.ra, u.dec, 0.000833333)" in q and q.startswith("SELECT u.oid, g.source_id")


def test_run_writes_chunks_resumes_and_merges(tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    src = tmp_path / "objects.parquet"
    objects(50).to_parquet(src, index=False)
    out = tmp_path / "run"
    FakeService.fail_first = 2
    s = m.run(src, out, chunk_size=20, workers=2, service_factory=FakeService, log=lambda msg: None)
    assert s["n_chunks"] == 3 and s["n_uploaded"] == 50
    odd = int((objects(50).dia_object_id % 2 == 1).sum())
    assert s["n_rows"] == odd and s["n_objects_matched"] == odd
    matches = pd.read_parquet(out / "matches.parquet")
    assert list(matches.columns) == ["oid", *m.GAIA_COLUMNS] and matches.oid.dtype == np.int64
    metas = [json.loads((out / "chunks" / f"chunk_{i:05d}.json").read_text()) for i in range(3)]
    assert sum(x["n_uploaded"] for x in metas) == 50 and max(x["attempts"] for x in metas) >= 2
    calls = FakeService.calls
    m.run(src, out, chunk_size=20, workers=2, service_factory=FakeService, log=lambda msg: None)
    assert FakeService.calls == calls                      # resumed: nothing fetched again
    victim = out / "chunks" / "chunk_00001.parquet"
    victim.write_bytes(victim.read_bytes() + b"x")         # a damaged chunk is fetched again
    s2 = m.run(src, out, chunk_size=20, workers=2, service_factory=FakeService, log=lambda msg: None)
    assert FakeService.calls == calls + 1 and s2["n_rows"] == odd
    assert pd.read_parquet(out / "matches.parquet").equals(matches)
    with pytest.raises(ValueError, match="different settings"):
        m.run(src, out, chunk_size=25, workers=2, service_factory=FakeService, log=lambda msg: None)


def test_overflow_is_never_accepted(tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    src = tmp_path / "objects.parquet"
    objects(10).to_parquet(src, index=False)
    FakeService.overflow = True
    with pytest.raises(m.TruncatedResult):
        m.run(src, tmp_path / "run", chunk_size=10, workers=1, service_factory=FakeService, log=lambda msg: None)
    assert not (tmp_path / "run" / "chunks" / "chunk_00000.parquet").exists()


def test_check_chunk_rejects_foreign_or_duplicate_rows():
    m = load_module()
    chunk = pd.DataFrame({"oid": np.array([1, 3], dtype=np.int64), "ra": [1.0, 2.0], "dec": [0.0, 0.0]})
    good = pd.DataFrame({"oid": [1, 3], **{c: [10, 11] if c == "source_id" else [0.0, 0.0] for c in m.GAIA_COLUMNS}})
    m.check_chunk(good, chunk)
    with pytest.raises(ValueError):
        m.check_chunk(good.assign(oid=[1, 5]), chunk)
    with pytest.raises(ValueError):
        m.check_chunk(good.assign(source_id=[10, 10], oid=[1, 1]), chunk)
