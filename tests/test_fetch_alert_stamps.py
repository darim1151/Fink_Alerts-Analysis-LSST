import csv
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "fetch_alert_stamps.py"
START, STOP = "2026-02-25", "2026-07-14"


def load_module():
    spec = importlib.util.spec_from_file_location("fetch_alert_stamps", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fits_bytes(tag=b""):
    header = (b"SIMPLE  =                    T" + tag).ljust(80) + b"END".ljust(80)
    return header.ljust(2880, b" ")


def alert(oid, sid, mjd, band, flux, err):
    return {"r:diaObjectId": int(oid), "r:diaSourceId": int(sid), "r:midpointMjdTai": mjd, "r:band": band,
            "r:psfFlux": flux, "r:psfFluxErr": err, "r:ra": 150.1, "r:dec": 2.2}


class FakeClient:
    """Answers /sources from a dict of alerts per object and /cutouts with a FITS stamp (or a bad body)."""

    def __init__(self, alerts_by_object, bad_cutouts=()):
        self.alerts, self.bad, self.calls, self.n_requests, self.base = alerts_by_object, set(bad_cutouts), [], 0, "fake"

    def post(self, path, payload):
        self.calls.append((path, payload))
        self.n_requests += 1
        if path == "/api/v1/sources":
            return json.dumps(self.alerts[payload["diaObjectId"]]).encode()
        assert path == "/api/v1/cutouts" and payload["output-format"] == "FITS"
        if payload["diaSourceId"] in self.bad:
            return b'{"status": "error"}'
        return fits_bytes(payload["kind"].encode())


def write_targets(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "group", "dia_object_id", "ra_deg", "dec_deg"])
        w.writeheader()
        w.writerows(rows)


def write_colour_table(path, counts):
    bands = "ugrizy"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["dia_object_id", "ra_deg", "dec_deg"] + [f"n_alerts_{b}" for b in bands])
        for oid, c in counts.items():
            w.writerow([oid, 1.0, 2.0] + [c.get(b, 0) for b in bands])


def test_mjd_of_cohort_window():
    m = load_module()
    assert m.mjd("1858-11-17") == 0.0
    assert m.mjd("2000-01-01") == 51544.0
    assert m.mjd(STOP) - m.mjd(START) == 139.0


def test_choice_is_highest_abs_snr_in_window_with_lowest_id_on_ties():
    m = load_module()
    oid = "170028485718835325"
    rows = [alert(oid, 11, m.mjd(START) - 0.1, "r", 1e6, 1.0),     # before the window: ignored
            alert(oid, 12, m.mjd(STOP), "r", 1e6, 1.0),            # stop is exclusive: ignored
            alert(oid, 13, m.mjd(START) + 1, "g", 100.0, 10.0),    # S/N 10
            alert(oid, 14, m.mjd(START) + 2, "r", -300.0, 10.0),   # |S/N| 30, negative difference flux
            alert(oid, 15, m.mjd(START) + 3, "i", 600.0, 20.0),    # S/N 30 tie, higher id
            alert(oid, 16, m.mjd(START) + 4, "i", 1e9, 0.0),       # zero error: unusable
            alert(oid, 17, m.mjd(START) + 5, "i", None, 1.0)]      # null flux: unusable
    alerts = m.parse_sources(json.dumps(rows).encode(), oid)
    window = m.in_window(alerts, m.mjd(START), m.mjd(STOP))
    assert [a["dia_source_id"] for a in window] == ["13", "14", "15", "16", "17"]
    assert m.choose_alert(window)["dia_source_id"] == "14"
    assert m.choose_alert([]) is None


def test_identifiers_must_be_exact_integers():
    m = load_module()
    assert m.as_id(170028485718835325) == "170028485718835325"
    assert m.as_id("170028485718835325") == "170028485718835325"
    for bad in (1.7002848571883533e17, "1.7e17", True, None, "-5"):
        with pytest.raises(ValueError):
            m.as_id(bad)
    oid = "170028485718835325"
    with pytest.raises(ValueError):   # a row for another object
        m.parse_sources(json.dumps([alert("170028485718835326", 1, 61100.0, "r", 1.0, 1.0)]).encode(), oid)
    with pytest.raises(ValueError):   # duplicate source ids
        m.parse_sources(json.dumps([alert(oid, 1, 61100.0, "r", 1.0, 1.0)] * 2).encode(), oid)
    with pytest.raises(ValueError):   # an error object instead of a list
        m.parse_sources(b'{"status": "error"}', oid)


def test_fits_check_and_count_check():
    m = load_module()
    m.check_fits(fits_bytes())
    for bad in (b"", b'{"status": "error"}', fits_bytes()[:-1], b"\x89PNG" + fits_bytes()[4:]):
        with pytest.raises(ValueError):
            m.check_fits(bad)
    assert m.count_check({"r": 2, "g": 1}, {"g": 1, "r": 2, "u": 0}) == "match"
    assert m.count_check({"r": 3}, {"r": 2, "i": 1}) == "differs (api/ours): r 3/2, i 0/1"
    assert m.count_check({"r": 3}, None) == "not checked"


def test_run_writes_stamps_and_records_failures(tmp_path):
    m = load_module()
    a, b, c = "170028485718835325", "313871013683134625", "170028526619590752"
    w0 = m.mjd(START)
    alerts = {a: [alert(a, 101, w0 + 1, "r", 500.0, 10.0), alert(a, 102, w0 + 2, "g", 50.0, 10.0),
                  alert(a, 103, w0 + 200, "r", 9e9, 1.0)],
              b: [alert(b, 201, w0 + 3, "i", 80.0, 10.0)],
              c: [alert(c, 301, w0 - 5, "r", 80.0, 10.0)]}
    targets = tmp_path / "image_targets.csv"
    write_targets(targets, [{"name": "G1-01", "group": "x", "dia_object_id": a, "ra_deg": 1, "dec_deg": 2},
                            {"name": "G1-02", "group": "x", "dia_object_id": b, "ra_deg": 1, "dec_deg": 2},
                            {"name": "G1-03", "group": "x", "dia_object_id": c, "ra_deg": 1, "dec_deg": 2}])
    table = tmp_path / "colours.csv"
    write_colour_table(table, {a: {"r": 1, "g": 1}, b: {"i": 2}, c: {}, "1": {"r": 9}})
    client = FakeClient(alerts, bad_cutouts={"201"})
    out = tmp_path / "stamps-run"
    summary = m.run(targets, out, client, START, STOP, colour_table=table, code_sha="test")

    rows = {r["name"]: r for r in csv.DictReader(open(out / "stamps.csv"))}
    assert list(rows) == ["G1-01", "G1-02", "G1-03"]
    ok = rows["G1-01"]
    assert ok["status"] == "ok" and ok["dia_source_id"] == "101" and ok["band"] == "r" and ok["snr"] == "50.00"
    assert ok["n_api_alerts"] == "3" and ok["n_api_in_window"] == "2" and ok["count_check"] == "match"
    for kind in ("science", "template", "difference"):
        path = out / ok[f"{kind}_file"]
        assert path.read_bytes() == fits_bytes(kind.capitalize().encode())
        assert ok[f"{kind}_sha256"] == m.sha256_bytes(path.read_bytes())
    assert rows["G1-02"]["status"].startswith("failed: not a FITS file")
    assert rows["G1-02"]["count_check"] == "differs (api/ours): i 1/2"
    assert rows["G1-03"]["status"] == "failed: no in-window alert with finite flux and positive error"
    assert (out / "sources" / f"G1-03_{c}.json").exists()

    assert summary["n_targets"] == 3 and summary["n_ok"] == 1 and set(summary["failures"]) == {"G1-02", "G1-03"}
    assert summary["count_mismatches"] == {"G1-02": "differs (api/ours): i 1/2"}
    assert summary == json.loads((out / "summary.json").read_text())
    assert "stamps.csv" in summary["files"] and summary["window_mjd"] == [m.mjd(START), m.mjd(STOP)]
    # 3 /sources calls, 3 cutouts for G1-01, 1 failed cutout for G1-02, nothing more for G1-03
    assert [p for p, _ in client.calls].count("/api/v1/sources") == 3 and len(client.calls) == 7
    assert all(isinstance(p["diaObjectId"], str) for path, p in client.calls if path == "/api/v1/sources")
    assert all(isinstance(p["diaSourceId"], str) for path, p in client.calls if path == "/api/v1/cutouts")

    with pytest.raises(FileExistsError):
        m.run(targets, out, client, START, STOP)


def test_run_refuses_targets_missing_from_colour_table(tmp_path):
    m = load_module()
    targets = tmp_path / "image_targets.csv"
    write_targets(targets, [{"name": "G1-01", "group": "x", "dia_object_id": "5", "ra_deg": 1, "dec_deg": 2}])
    table = tmp_path / "colours.csv"
    write_colour_table(table, {"6": {"r": 1}})
    with pytest.raises(ValueError, match="not in the colour table"):
        m.run(targets, tmp_path / "out", FakeClient({}), START, STOP, colour_table=table)
    assert not (tmp_path / "out").exists()


def test_client_retries_server_errors_but_not_client_errors(monkeypatch):
    m = load_module()
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    replies = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        assert request.get_method() == "POST" and request.get_header("Content-type") == "application/json"
        code = replies.pop(0)
        if code == 200:
            return Response(b"payload")
        raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b"body"))

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    client = m.Client("https://example.invalid/", delay=0, tries=3)
    replies[:] = [503, 502, 200]
    assert client.post("/api/v1/sources", {"diaObjectId": "1"}) == b"payload" and client.n_requests == 3
    replies[:] = [400, 200]
    with pytest.raises(RuntimeError, match="HTTP 400"):
        client.post("/api/v1/sources", {"diaObjectId": "1"})
    assert replies == [200]
    replies[:] = [503, 503, 503]
    with pytest.raises(RuntimeError, match="HTTP 503"):
        client.post("/api/v1/sources", {"diaObjectId": "1"})
