"""Conservative capability discovery: presence never validates an estimator."""

from __future__ import annotations

from .model import CapabilityState as C
from .model import FinkCapabilityManifest, FinkFeatureDefinition

BASE = (
    (
        "source_id",
        "source",
        "int64",
        None,
        "unique delivered diaSourceId; wire IDs are decimal strings",
        "identity",
    ),
    (
        "population",
        "source",
        "string",
        None,
        "DIA/SSO/AMBIGUOUS packet discriminator combinations; not physical truth",
        "categorical",
    ),
    (
        "observation_mjd_tai",
        "source",
        "float64",
        "d",
        "midpointMjdTai; native MJD TAI effective mid-visit time",
        "temporal",
    ),
    (
        "ra_deg",
        "source",
        "float64",
        "deg",
        "reported source right ascension; finite range [0,360)",
        "spatial",
    ),
    (
        "dec_deg",
        "source",
        "float64",
        "deg",
        "reported source declination; finite range [-90,90]",
        "spatial",
    ),
    (
        "band",
        "source",
        "string",
        None,
        "delivered Rubin band label; safe laboratory values u/g/r/i/z/y",
        "categorical",
    ),
    (
        "psf_flux",
        "source",
        "float64",
        "nJy",
        "delivered PSF difference flux between visit and template; no independent calibration claim",
        "measurement",
    ),
    (
        "psf_flux_err",
        "source",
        "float64",
        "nJy",
        "delivered PSF difference-flux uncertainty; usable values finite and positive",
        "measurement",
    ),
    (
        "delivered_source_rows",
        "derived_dia_object",
        "uint64",
        None,
        "number of admitted DIA source rows grouped by positive native diaObjectId",
        "multiplicity",
    ),
    (
        "temporal_baseline",
        "derived_dia_object",
        "float64",
        "d",
        "last minus first admitted observation_mjd_tai; delivery-window baseline",
        "temporal",
    ),
    (
        "delivered_bands",
        "derived_dia_object",
        "list<string>",
        None,
        "sorted distinct bands represented by admitted DIA source rows",
        "categorical",
    ),
)

UNAVAILABLE = {
    "lc_feature_estimators": "broker lc_features are per-band summaries; estimator/physical semantics not established by characterization",
    "calibrated_class_probability": "model scores are outputs; calibration as probabilities is not established",
    "astrophysical_class": "classifier prediction is not independently established astrophysical truth",
    "complete_detection_history": "Light Static delivery is not a qualified complete-history product",
    "forced_photometry": "no qualified forced-photometry or upper-limit relation",
    "canonical_dia_object": "only derived delivered-source grouping exists",
    "sso_identity_orbits": "no qualified SSO identity/orbit linkage",
    "sdss_color": "no qualified SDSS-like color estimator",
    "rms": "no qualified variability estimator",
    "chi_square": "no qualified variability estimator",
    "skewness": "no qualified variability estimator",
    "variability_amplitude": "no qualified variability estimator",
    "survey_footprint": "delivered source density does not establish survey footprint",
}


def rows(conn, sql, parameters=None):
    cursor = conn.execute(sql, parameters or [])
    names = [x[0] for x in cursor.description]
    return tuple(dict(zip(names, row)) for row in cursor.fetchall())


def registry(provenance_refs, conn=None, inventories=(), characterized_coverage=()):
    admitted = conn is not None
    conditions = {
        "ra_deg": "isfinite(ra_deg) AND ra_deg>=0 AND ra_deg<360",
        "dec_deg": "isfinite(dec_deg) AND dec_deg>=-90 AND dec_deg<=90",
        "band": "band IN ('u','g','r','i','z','y')",
        "psf_flux": "isfinite(psf_flux)",
        "psf_flux_err": "isfinite(psf_flux_err) AND psf_flux_err>0",
    }
    # One projected source pass for exact coverage, rather than a full external
    # Parquet scan for each discoverable dimension.
    scalar_counts, snapshot_counts, object_count = {}, {}, 0
    if admitted:
        expressions = ["count(*) AS total"]
        for name, level, *_ in BASE:
            if level == "source":
                expressions.extend(
                    [
                        f"count(*) FILTER (WHERE {name} IS NULL) AS {name}_nulls",
                        f"count(*) FILTER (WHERE {conditions.get(name, 'true')}) AS {name}_usable",
                    ]
                )
        scalar_counts = rows(conn, "SELECT " + ",".join(expressions) + " FROM sources")[
            0
        ]
        object_count = conn.execute(
            "SELECT count(*) FROM derived_dia_object_summary"
        ).fetchone()[0]
        snapshot_counts = rows(
            conn,
            "SELECT "
            + ",".join(
                f"count(*) FILTER (WHERE {module}_json IS NOT NULL) AS {module}"
                for module in ("pred", "clf", "lc_features", "xm", "misc")
            )
            + " FROM broker_snapshots",
        )[0]
    total = scalar_counts.get("total")
    evidence = (
        "analysis_contract_v1",
        "data/fixtures/schema/fink_lsst_schema_sources.json",
        *provenance_refs,
    )
    entries = []

    def add(
        name,
        namespace,
        level,
        dtype,
        unit,
        definition,
        role=None,
        coverage=None,
        state=None,
        reason=None,
        module=None,
    ):
        state = state or (C.AVAILABLE if admitted else C.UNAVAILABLE)
        entries.append(
            FinkFeatureDefinition(
                name,
                namespace,
                level,
                dtype,
                unit,
                "source-time broker snapshot"
                if namespace.startswith("fink.broker")
                else "delivered cohort window"
                if level == "derived_dia_object"
                else "observation MJD TAI",
                definition,
                tuple(provenance_refs),
                coverage or dict(denominator=None, status="NOT_MEASURED"),
                state,
                reason
                or (
                    "qualified delivered measurements; no complete-history claim"
                    if admitted
                    else "metadata fixture has no admitted scientific rows"
                ),
                evidence,
                role,
                module,
            )
        )

    for name, level, dtype, unit, definition, role in BASE:
        coverage = None
        state = None
        if admitted and level == "source":
            usable = scalar_counts[name + "_usable"]
            nulls = scalar_counts[name + "_nulls"]
            coverage = dict(
                denominator=total,
                denominator_kind="source_rows",
                null_rows=nulls,
                usable_rows=usable,
                excluded_rows=total - usable,
                predicate=conditions.get(name, "catalog source/time invariants"),
            )
            state = (
                C.AVAILABLE
                if usable == total and total
                else C.PARTIALLY_QUALIFIED
                if usable
                else C.UNAVAILABLE
            )
            if name.startswith("psf_flux") and usable:
                state = C.PARTIALLY_QUALIFIED
        elif admitted:
            count = object_count
            coverage = dict(
                denominator=count,
                denominator_kind="derived_DIA_groups",
                usable_rows=count,
            )
            state = C.AVAILABLE if count else C.UNAVAILABLE
        add(
            name,
            "fink.native" if level == "source" else "fink.derived",
            level,
            dtype,
            unit,
            definition,
            role,
            coverage,
            state,
        )

    paths = {}
    for inventory in inventories:
        for item in inventory:
            path = item["field_path"]
            if path.split(".")[0].split("[")[0] in (
                "pred",
                "clf",
                "lc_features",
                "xm",
                "misc",
            ):
                paths.setdefault(path, set()).add(item["arrow_type"])
    # The module shells are explicit even if the corresponding broker struct is absent.
    for module in ("pred", "clf", "lc_features", "xm", "misc"):
        paths.setdefault(module, {"JSON snapshot"})
    for path in sorted(paths):
        root = path.split(".")[0].split("[")[0]
        is_estimator = root == "lc_features" and path != root
        coverage = dict(denominator=None, status="NOT_MEASURED")
        matching = [x for x in characterized_coverage if x["field_path"] == path]
        if matching:
            coverage = dict(
                denominator=sum(x["observed_count"] for x in matching),
                denominator_kind=matching[0].get("coverage_unit", "rows"),
                null_count=sum(x["null_count"] for x in matching),
                nonfinite_count=sum(x["nonfinite_count"] for x in matching),
                scope="characterization coverage, preserves row/element denominators",
            )
            coverage["usable_values"] = (
                coverage["denominator"]
                - coverage["null_count"]
                - coverage["nonfinite_count"]
            )
        if admitted and path == root:
            present = snapshot_counts[root]
            coverage = dict(
                denominator=total,
                denominator_kind="source_snapshots",
                nonnull_rows=present,
                null_rows=total - present,
            )
        state = C.UNAVAILABLE if not admitted or is_estimator else C.PARTIALLY_QUALIFIED
        if admitted and (
            coverage.get("nonnull_rows") == 0 or coverage.get("usable_values") == 0
        ):
            state = C.UNAVAILABLE
        primitive = paths[path] <= {
            "bool",
            "int32",
            "int64",
            "float",
            "double",
            "string",
            "large_string",
        }
        if (
            admitted
            and primitive
            and path != root
            and (not matching or snapshot_counts[root] == 0)
        ):
            state = C.UNAVAILABLE
        role = (
            "snapshot_output"
            if admitted
            and primitive
            and root in ("clf", "pred")
            and "." in path
            and "[" not in path
            else None
        )
        add(
            path,
            "fink.broker." + root,
            "broker_snapshot",
            "|".join(sorted(paths[path])),
            None,
            "delivered broker field "
            + path
            + " retained at source time; schema/coverage evidence only",
            role,
            coverage,
            state,
            "estimator semantics not qualified"
            if is_estimator
            else "no measured usable snapshot coverage"
            if admitted and state == C.UNAVAILABLE
            else "snapshot values only; physical validity/calibration not established"
            if admitted
            else None,
            dict(
                module=root,
                field_path=path,
                model_identity="only values present in source-time module payload; not inferred from schema versions",
            ),
        )
    for name, reason in sorted(UNAVAILABLE.items()):
        add(
            name,
            "fink.unsupported",
            "unqualified",
            "unknown",
            None,
            reason,
            state=C.UNAVAILABLE,
            reason=reason,
        )
    return FinkCapabilityManifest(
        tuple(sorted(entries, key=lambda x: (x.namespace, x.name)))
    )
