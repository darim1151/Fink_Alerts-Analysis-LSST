"""Offline evidence-chain qualification; scientific rows here are synthetic."""

import json
import shutil
from pathlib import Path

import duckdb
import pytest
from _acquisition_fakes import FakeKafkaConsumer, FakePortal, TopicPartition
from astropy.time import Time
from test_acquisition_handoff_evidence import _identified
from test_analytics import make_input

from fink_lsst import characterization as ch
from fink_lsst.acquisition.handoff import build_transfer_plan
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.analytics import catalog as c
from fink_lsst.observatory import adapter as a
from fink_lsst.observatory.model import (
    AnalyticalAdmission as A,
)
from fink_lsst.observatory.model import (
    CapabilityState as C,
)
from fink_lsst.observatory.model import (
    CharacterizationState as H,
)
from fink_lsst.observatory.model import (
    Qualification,
)
from fink_lsst.observatory.products import sky, temporal
from fink_lsst.observatory.qualification import (
    file_sha256,
    qualification,
    read_definition,
)
from fink_lsst.observatory.serialization import (
    SharedContractUnbound,
    canonical_bytes,
    native_artifact,
    serialize_observatory_bundle_v1,
)

REPO = Path(__file__).resolve().parents[1]
CODE_SHA = "a" * 40


@pytest.fixture
def qualified(tmp_path, monkeypatch):
    """Real native builders over a fully replayable fake delivery, no live services."""
    repo = tmp_path / "repo"
    root = tmp_path / "FINK"
    root.mkdir()
    o, record = _identified(
        tmp_path / "transport", start="2026-02-25", stop="2026-02-28"
    )
    record = o.observe_producer(
        record,
        FakePortal(producer_log=f"Data available at topic: {record.topic}\nEnd."),
    )
    record = o.record_topic_metadata(
        record,
        FakeKafkaConsumer({record.topic: [5]}),
        topic_partition_factory=TopicPartition,
    )
    record = o.record_transfer_started(
        record, build_transfer_plan(record, root), release="synthetic"
    )
    item, _ = make_input(tmp_path / "sample")
    raw = root / record.request.expected_raw_dir(record.topic)
    raw.mkdir(parents=True)
    shutil.copy2(next(item.raw.glob("*.parquet")), raw / "synthetic.parquet")
    record = o.record_transfer_result(
        record, exit_code=0, terminal_committed=5, terminal_lag=0, log_sha256="f" * 64
    )
    record = o.record_delivery_validation(record, data_root=root)
    shutil.copytree(o.registry.root, repo / "configs/acquisitions")
    (repo / "configs/analysis_cohorts").mkdir()
    (repo / "data/fixtures/schema").mkdir(parents=True)
    shutil.copy2(
        REPO / "data/fixtures/schema/fink_lsst_schema_sources.json",
        repo / "data/fixtures/schema",
    )
    # Only the Git cleanliness/identity is synthetic; evidence replay, scans,
    # characterization, source invariants, hashes and read-only reopening are real.
    monkeypatch.setattr(
        c.subprocess,
        "check_output",
        lambda argv, **kw: "" if "status" in argv else CODE_SHA,
    )
    ch.execute(record.acquisition_id, root, "characterization", repo_root=repo)
    cohort = repo / "configs/analysis_cohorts/synthetic.json"
    definition = dict(
        contract_id="analysis_contract_v1",
        cohort_name="synthetic",
        acquisitions=[
            dict(
                acquisition_id=record.acquisition_id,
                characterization=dict(
                    run_id="characterization",
                    code_sha=CODE_SHA,
                    summary_sha256=file_sha256(
                        root / "outputs/characterization/characterization_summary.json"
                    ),
                ),
            )
        ],
    )
    cohort.write_text(json.dumps(definition))
    c.execute(cohort, root, "analysis", repo_root=repo, temporary_base=tmp_path)
    return dict(
        repo=repo,
        root=root,
        cohort=cohort,
        definition=definition,
        catalog=root / "data/processed/analysis/analytics.duckdb",
        record=record,
        manifest=root / "manifests/analysis/manifest.json",
        raw=raw,
    )


def export(env, **kwargs):
    return a.build_from_catalog(
        env["catalog"], env["root"], env["cohort"], env["repo"], **kwargs
    )


def test_scientific_products_preserve_native_populations_and_snapshots(qualified):
    model = export(qualified, include_utc=True)
    assert model.product_kind == "SCIENTIFIC_DERIVED"
    assert model.authority == "DERIVED / NON-AUTHORITATIVE"
    assert (
        model.basis.acquisitions[0].qualification.analytical_admission
        == A.ANALYTICALLY_ADMITTED
    )
    assert (
        model.basis.acquisitions[0].qualification.characterization_state
        == H.CHARACTERIZED
    )
    kinds = [x.entity_type for x in model.entities]
    assert kinds.count("DIA_SOURCE") == 2
    assert kinds.count("SSO_SOURCE") == 1
    assert kinds.count("AMBIGUOUS_SOURCE") == 2
    assert kinds.count("DERIVED_DIA_OBJECT") == 1
    sso = next(x for x in model.entities if x.entity_type == "SSO_SOURCE")
    assert sso.native_ids["raw_diaObjectId"] == "0"
    assert sso.native_ids["derived_diaObject_group"] is None
    obj = next(x for x in model.entities if x.entity_type == "DERIVED_DIA_OBJECT")
    assert obj.fields["is_derived"] and obj.fields["delivered_source_rows"] == 2
    assert obj.fields["delivered_bands"] == ["g", "r"]
    assert len(model.broker_snapshots) == 5
    assert len({s.snapshot_id for s in model.broker_snapshots}) == 5
    changed = [
        s.modules["clf"]["cats_class"]
        for s in model.broker_snapshots
        if s.native_source_id in ("1", "2")
    ]
    assert changed == [1, 2]


def test_native_time_and_qualified_utc_distribution(qualified):
    model = export(qualified, include_utc=True)
    assert (
        model.temporal.native_scale == "TAI"
        and model.temporal.native_field == "midpointMjdTai"
    )
    assert sum(x["source_rows"] for x in model.temporal.observation_bins_tai) == 5
    assert [x["source_rows"] for x in model.temporal.utc_date_bins] == [5, 0, 0]
    assert (
        model.temporal.utc_date_bins[1]["status"]
        == "zero_rows_in_validated_Fink_delivery"
    )
    assert model.temporal.utc_conversion and "Astropy" in model.temporal.utc_conversion
    assert export(qualified).temporal.utc_date_bins is None
    assert all(x.fields.get("time_scale") == "TAI" for x in model.entities)


def test_snapshot_nonfinite_is_explicit_and_json_finite(qualified):
    model = export(qualified)
    snapshot = next(x for x in model.broker_snapshots if x.native_source_id == "3")
    assert snapshot.modules["clf"]["cats_score"] is None
    assert snapshot.nonfinite_paths == ("clf.cats_score",)
    assert len(snapshot.original_module_sha256["clf"]) == 64
    payload = json.loads(canonical_bytes(native_artifact(model)))
    assert payload["shared_contract_binding"] == "UNBOUND"
    assert payload["payload"]["authority"] == "DERIVED / NON-AUTHORITATIVE"
    with pytest.raises(ValueError):
        canonical_bytes({"bad": float("nan")})


def test_unavailable_estimators_and_discovery(qualified):
    model = export(qualified)
    caps = {x.name: x for x in model.capabilities.entries}
    for name in (
        "lc_feature_estimators",
        "lc_features[].value.mean",
        "rms",
        "chi_square",
        "skewness",
        "variability_amplitude",
        "sdss_color",
        "calibrated_class_probability",
        "survey_footprint",
    ):
        assert caps[name].qualification == C.UNAVAILABLE
    assert caps["psf_flux"].qualification == C.PARTIALLY_QUALIFIED
    assert caps["psf_flux"].unit == "nJy"
    assert caps["clf.cats_score"].qualification == C.PARTIALLY_QUALIFIED
    assert caps["clf.cats_score"].coverage["nonfinite_count"] == 1
    assert caps["lc_features[].value.mean"].coverage["denominator_kind"] == "elements"
    names = {x.name for x in model.features}
    assert "temporal_baseline" in names and "delivered_source_rows" in names
    assert "clf.cats_score" in names and "rms" not in names
    assert "lc_features[].value.mean" not in names
    assert caps["clf.cats_score"].module_provenance["module"] == "clf"


def test_determinism_provenance_and_no_input_mutation(qualified):
    root = qualified["root"]
    before = {str(p): file_sha256(p) for p in root.rglob("*") if p.is_file()}
    first, second = export(qualified), export(qualified)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert before == {str(p): file_sha256(p) for p in root.rglob("*") if p.is_file()}
    p = first.provenance[0]
    assert p.acquisition_id == qualified["record"].acquisition_id
    assert p.cohort_name == "synthetic" and len(p.cohort_sha256) == 64
    assert p.characterization["run_id"] == "characterization"
    assert p.characterization["verification"] == "ARTIFACTS_VERIFIED"
    assert p.input_build_id == "analysis" and p.input_build_code_sha == CODE_SHA
    assert len(p.input_catalog_sha256) == len(p.analysis_contract_sha256) == 64
    assert p.adapter_code_sha == CODE_SHA
    refs = {p.provenance_id for p in first.provenance}
    for product in (
        first.temporal,
        first.sky,
        *first.entities,
        *first.broker_snapshots,
        *first.capabilities.entries,
    ):
        assert product.provenance_refs and set(product.provenance_refs) <= refs


@pytest.mark.parametrize(
    "field,value",
    [
        ("completion_status", "RUNNING"),
        ("completion_status", "FAILED"),
        ("finished_utc", None),
        ("contract_id", "other"),
        ("run_id", "other"),
        ("code_commit_sha", "invalid"),
        ("cohort_sha256", "0" * 64),
        ("processed_dir", "/outside"),
        ("acquisitions", []),
        ("artifact_sha256", {}),
    ],
)
def test_bad_build_evidence_fails_closed(qualified, field, value):
    path = qualified["manifest"]
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(c.AdmissionError):
        export(qualified)


def test_catalog_hash_mismatch_rejected(qualified):
    with qualified["catalog"].open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(c.AdmissionError, match="artifact hash differs"):
        export(qualified)


def test_characterization_artifact_mismatch_rejected(qualified):
    (
        qualified["root"] / "outputs/characterization/characterization_summary.json"
    ).write_text("{}")
    with pytest.raises(c.ContradictionError, match="digest differs"):
        export(qualified)


def test_changed_raw_rejected(qualified):
    (qualified["raw"] / "new-file").write_text("unexpected")
    with pytest.raises(c.ContradictionError):
        export(qualified)


def test_uncharacterized_cohort_refused(qualified):
    definition = qualified["definition"]
    definition["acquisitions"][0].pop("characterization")
    qualified["cohort"].write_text(json.dumps(definition))
    with pytest.raises(c.AdmissionError, match="pinned characterization"):
        export(qualified)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(contract_id="unknown"),
        lambda d: d.update(acquisitions=[]),
        lambda d: d["acquisitions"].append(d["acquisitions"][0]),
        lambda d: d["acquisitions"][0]["characterization"].update(summary_sha256="bad"),
    ],
)
def test_malformed_cohort_refused(qualified, mutate):
    mutate(qualified["definition"])
    qualified["cohort"].write_text(json.dumps(qualified["definition"]))
    with pytest.raises(c.AdmissionError):
        read_definition(qualified["cohort"], qualified["repo"])


def test_malformed_registry_evidence_refused(qualified):
    receipt = (
        qualified["repo"]
        / "configs/acquisitions"
        / qualified["record"].acquisition_id
        / "evidence/delivery_1.json"
    )
    receipt.write_text("{}")
    with pytest.raises(ValueError):
        export(qualified)


def test_current_committed_metadata_never_becomes_science(monkeypatch):
    monkeypatch.setattr(a, "code_identity", lambda repo: CODE_SHA)
    model = a.metadata_fixture()
    assert model.product_kind == "METADATA / CONTRACT FIXTURE"
    assert not model.entities and not model.broker_snapshots and not model.features
    assert not model.sky.cells and not model.temporal.observation_bins_tai
    assert model.sky.input_rows is None and model.sky.excluded_position_rows is None
    assert model.temporal.utc_date_bins is None
    assert all(x.qualification == C.UNAVAILABLE for x in model.capabilities.entries)
    states = [x.qualification for x in model.basis.acquisitions]
    assert [x.transport_state for x in states] == ["DELIVERY_VALIDATED"] * 3 + [
        "PRODUCER_COMPLETE"
    ]
    assert states[0].characterization_state == H.REFERENCE_PINNED
    assert states[0].analytical_admission == A.COHORT_DECLARED_NOT_VERIFIED
    assert all(x.analytical_admission == A.NOT_ADMITTED for x in states[1:])
    assert [x.validated_delivery_rows for x in model.basis.acquisitions] == [
        1658642,
        76133,
        266496,
        None,
    ]
    assert canonical_bytes(model) == canonical_bytes(a.metadata_fixture())


def test_qualification_requires_distinct_evidence_levels():
    record = AcquisitionRegistry(REPO / "configs/acquisitions").list_records()[0]
    pin = json.loads((REPO / "configs/analysis_cohorts/month1.json").read_text())[
        "acquisitions"
    ][0]["characterization"]
    assert qualification(record).analytical_admission == A.NOT_ADMITTED
    assert qualification(record, pin).characterization_state == H.REFERENCE_PINNED
    assert (
        qualification(record, pin, verified=True).characterization_state
        == H.CHARACTERIZED
    )
    assert (
        qualification(record, pin, verified=True, admitted=True).analytical_admission
        == A.ANALYTICALLY_ADMITTED
    )
    with pytest.raises(ValueError):
        qualification(record, pin, admitted=True)
    with pytest.raises(ValueError):
        Qualification(
            "PRODUCER_COMPLETE",
            H.CHARACTERIZED,
            A.ANALYTICALLY_ADMITTED,
            C.AVAILABLE,
            "invalid",
            ("e",),
        )
    with pytest.raises(ValueError):
        Qualification(
            "DELIVERY_VALIDATED",
            H.NOT_ESTABLISHED,
            A.NOT_ADMITTED,
            C.AVAILABLE,
            "invalid",
            ("e",),
        )


def test_equal_area_hierarchy_and_invalid_positions():
    with duckdb.connect() as conn:
        conn.execute(
            "CREATE TABLE sources(acquisition_id VARCHAR,population VARCHAR,ra_deg DOUBLE,dec_deg DOUBLE)"
        )
        conn.executemany(
            "INSERT INTO sources VALUES (?,?,?,?)",
            [
                ("a", "DIA", 0.0, -90.0),
                ("a", "SSO", 359.99, 90.0),
                ("a", "AMBIGUOUS", 180.0, 0.0),
                ("a", "DIA", 360.0, 0.0),
                ("a", "SSO", None, 0.0),
                ("a", "SSO", 0.0, float("nan")),
            ],
        )
        child = sky(conn, ("p",), 3)
        parent = sky(conn, ("p",), 2)
    assert child.input_rows == 6 and child.excluded_position_rows == 3
    assert sum(x["source_rows"] for x in child.cells) == 3
    assert {(x["population"], x["parent_cell_id"]) for x in child.cells} == {
        (x["population"], x["cell_id"]) for x in parent.cells
    }
    assert "not survey footprint" in child.meaning


@pytest.mark.parametrize("order", [-1, 7, True, 1.5])
def test_bad_spatial_resolution_refused(order):
    with pytest.raises(ValueError):
        sky(None, (), order)


def test_utc_bins_use_tai_leap_second_boundaries():
    with duckdb.connect() as conn:
        conn.execute(
            "CREATE TABLE sources(acquisition_id VARCHAR,population VARCHAR,observation_mjd_tai DOUBLE)"
        )
        values = Time(
            ["2016-12-31T23:59:60", "2017-01-01T00:00:00"], scale="utc"
        ).tai.mjd
        conn.executemany(
            "INSERT INTO sources VALUES (?,?,?)",
            [("a", "DIA", float(t)) for t in values],
        )
        conn.execute(
            "CREATE TABLE daily_coverage(acquisition_id VARCHAR,utc_date DATE,start_mjd_tai DOUBLE,stop_mjd_tai DOUBLE,delivered_rows BIGINT,status VARCHAR)"
        )
        for start, stop in [("2016-12-31", "2017-01-01"), ("2017-01-01", "2017-01-02")]:
            lo, hi = c.utc_bounds(start, stop)
            conn.execute(
                "INSERT INTO daily_coverage VALUES (?,?,?,?,?,?)",
                ["a", start, lo, hi, 1, "delivered_rows"],
            )
        from fink_lsst.observatory.model import FinkObservatoryBasis

        product = temporal(conn, FinkObservatoryBasis(None, None, ()), (), True)
        assert [x["source_rows"] for x in product.utc_date_bins] == [1, 1]
        assert (
            product.utc_date_bins[0]["stop_mjd_tai"]
            - product.utc_date_bins[0]["start_mjd_tai"]
        ) * 86400 == pytest.approx(86401.0, abs=1e-6)
        conn.execute("UPDATE daily_coverage SET start_mjd_tai=start_mjd_tai-1")
        with pytest.raises(ValueError, match="UTC boundaries"):
            temporal(conn, FinkObservatoryBasis(None, None, ()), (), True)


def test_shared_contract_stays_closed(qualified):
    with pytest.raises(SharedContractUnbound, match="identity/version/SHA256"):
        serialize_observatory_bundle_v1(export(qualified))


def test_read_only_catalog_cannot_mutate_inputs(qualified):
    with c.open_catalog(qualified["catalog"], qualified["root"]) as conn:
        with pytest.raises(duckdb.InvalidInputException):
            conn.execute("CREATE TABLE forbidden(x INT)")


def test_catalog_completion_marker_is_required_even_with_matching_hash(qualified):
    with duckdb.connect(str(qualified["catalog"])) as conn:
        conn.execute("DELETE FROM catalog_metadata WHERE key='qualification_status'")
    manifest = json.loads(qualified["manifest"].read_text())
    manifest["artifact_sha256"][str(qualified["catalog"])] = file_sha256(
        qualified["catalog"]
    )
    qualified["manifest"].write_text(json.dumps(manifest))
    with pytest.raises(c.AdmissionError, match="qualification incomplete"):
        export(qualified)


def test_later_validated_deliveries_stay_outside_science(qualified):
    later = AcquisitionRegistry(REPO / "configs/acquisitions").list_records()[1:]
    for record in later:
        shutil.copytree(
            record.directory,
            qualified["repo"] / "configs/acquisitions" / record.acquisition_id,
        )
    model = export(qualified)
    assert len(model.basis.acquisitions) == 4
    assert all(
        x.qualification.analytical_admission == A.NOT_ADMITTED
        for x in model.basis.acquisitions[1:]
    )
    assert all(x.input_build_id is None for x in model.provenance[1:])
    assert {x["acquisition_id"] for x in model.temporal.observation_bins_tai} == {
        qualified["record"].acquisition_id
    }
    assert sum(x["source_rows"] for x in model.sky.cells) == 5


def test_bounded_samples_can_be_disabled_without_changing_aggregates(qualified):
    model = export(qualified, sample_per_population=0)
    assert not model.entities and not model.broker_snapshots
    assert sum(x["source_rows"] for x in model.sky.cells) == 5
    with pytest.raises(ValueError):
        export(qualified, sample_per_population=101)


def test_large_native_ids_remain_exact_decimal_strings(tmp_path):
    item, _ = make_input(tmp_path, offset=2**53)
    c.build_catalog(
        [item], tmp_path / "catalog.duckdb", CODE_SHA, "synthetic", str(tmp_path)
    )
    from fink_lsst.observatory.capabilities import registry
    from fink_lsst.observatory.products import entities

    with duckdb.connect(str(tmp_path / "catalog.duckdb"), read_only=True) as conn:
        records, _ = entities(
            conn, {item.acquisition_id: "p"}, registry(("p",), conn), 5
        )
    source = next(x for x in records if x.entity_type == "DIA_SOURCE")
    assert source.native_ids["diaSourceId"] == str(2**53 + 1)
    assert json.loads(canonical_bytes(source))["native_ids"]["diaSourceId"] == str(
        2**53 + 1
    )


def test_concurrent_raw_change_fails_before_returning_product(qualified, monkeypatch):
    original = a.sky

    def changed(*args):
        result = original(*args)
        (qualified["raw"] / "unexpected").write_text("concurrent change")
        return result

    monkeypatch.setattr(a, "sky", changed)
    with pytest.raises(c.AdmissionError, match="changed during"):
        export(qualified)


def test_dirty_code_refused_before_reading_scientific_inputs(qualified, monkeypatch):
    monkeypatch.setattr(
        c.subprocess, "check_output", lambda *args, **kw: " M changed.py"
    )
    with pytest.raises(c.AdmissionError, match="clean committed"):
        export(qualified)


def test_cli_writes_only_new_derived_output_and_never_reuses_run(
    qualified, monkeypatch
):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "export_native", REPO / "scripts/export_observatory_bundle.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "REPO_ROOT", qualified["repo"])
    monkeypatch.setattr(cli, "resolve_data_root", lambda *args: qualified["root"])
    monkeypatch.setattr(
        cli, "build_from_catalog", lambda *args, **kwargs: export(qualified)
    )
    args = [
        "science",
        "--data-root",
        str(qualified["root"]),
        "--catalog",
        str(qualified["catalog"]),
        "--cohort",
        str(qualified["cohort"]),
        "--export-run-id",
        "observatory",
    ]
    assert cli.main(args) == 0
    path = qualified["root"] / "outputs/observatory/fink_observatory_native.json"
    saved = path.read_bytes()
    assert json.loads((path.parent / "sha256.json").read_bytes())[
        path.name
    ] == file_sha256(path)
    with pytest.raises(FileExistsError):
        cli.main(args)
    assert path.read_bytes() == saved
    with pytest.raises(ValueError):
        cli.main(args[:-1] + ["../raw"])


def test_schema_only_or_all_null_model_field_is_unavailable(qualified):
    from fink_lsst.observatory.capabilities import registry

    inventory = [[{"field_path": "clf.cats_score", "arrow_type": "float"}]]
    with c.open_catalog(qualified["catalog"], qualified["root"]) as conn:
        entries = registry(("p",), conn, inventory, ()).entries
        assert (
            next(x for x in entries if x.name == "clf.cats_score").qualification
            == C.UNAVAILABLE
        )
        covered = registry(
            ("p",),
            conn,
            inventory,
            [
                {
                    "field_path": "clf.cats_score",
                    "observed_count": 5,
                    "null_count": 5,
                    "nonfinite_count": 0,
                    "coverage_unit": "rows",
                }
            ],
        )
        assert (
            next(x for x in covered.entries if x.name == "clf.cats_score").qualification
            == C.UNAVAILABLE
        )
        assert "clf.cats_score" not in {x.name for x in covered.discover_dimensions()}
