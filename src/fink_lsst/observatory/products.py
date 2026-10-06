"""Compact deterministic delivered-source aggregates and bounded native records."""

from __future__ import annotations

import hashlib
import json
import math

from fink_lsst.analytics.contract import SNAPSHOTS, utc_bounds

from .capabilities import rows
from .model import BrokerSnapshot, FinkEntityRecord, FinkSkyProduct, FinkTemporalProduct


def temporal(conn, basis, refs, include_utc):
    native = (
        rows(
            conn,
            """SELECT acquisition_id,population,floor(observation_mjd_tai) AS start_mjd_tai,
                       floor(observation_mjd_tai)+1 AS stop_mjd_tai,count(*) AS source_rows
                       FROM sources GROUP BY acquisition_id,population,start_mjd_tai,stop_mjd_tai
                       ORDER BY acquisition_id,start_mjd_tai,population""",
        )
        if conn
        else ()
    )
    utc = None
    if include_utc and conn:
        utc = rows(
            conn,
            """SELECT acquisition_id,utc_date::VARCHAR AS utc_date,start_mjd_tai,stop_mjd_tai,
                           delivered_rows AS source_rows,status FROM daily_coverage
                           ORDER BY acquisition_id,utc_date""",
        )
        for row in utc:
            from datetime import date, timedelta

            stop = (date.fromisoformat(row["utc_date"]) + timedelta(days=1)).isoformat()
            if (row["start_mjd_tai"], row["stop_mjd_tai"]) != utc_bounds(
                row["utc_date"], stop
            ):
                raise ValueError(
                    "catalog UTC boundaries differ from qualified Astropy/ERFA conversion"
                )
    return FinkTemporalProduct(
        "midpointMjdTai",
        "MJD",
        "TAI",
        native,
        utc,
        tuple(
            dict(
                acquisition_id=x.acquisition_id,
                start_utc=x.requested_start_utc,
                stop_utc=x.requested_stop_utc,
                interval="half-open",
                meaning="requested delivery window",
            )
            for x in basis.acquisitions
        ),
        tuple(
            dict(
                acquisition_id=x.acquisition_id,
                transport_state=x.qualification.transport_state,
                validated_delivery_rows=x.validated_delivery_rows,
                meaning="broker transport evidence",
            )
            for x in basis.acquisitions
        ),
        tuple(
            x.acquisition_id
            for x in basis.acquisitions
            if x.qualification.analytical_admission.value == "ANALYTICALLY_ADMITTED"
        ),
        refs,
        utc_conversion="analytics.contract.utc_bounds: Astropy UTC boundaries -> MJD TAI; ERFA, network disabled"
        if utc is not None
        else None,
    )


def sky(conn, refs, order):
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 6:
        raise ValueError("sky order must be an integer from 0 through 6")
    nx, ny = 4 * 2**order, 2 * 2**order
    valid = "isfinite(ra_deg) AND ra_deg>=0 AND ra_deg<360 AND isfinite(dec_deg) AND dec_deg>=-90 AND dec_deg<=90"
    cells = ()
    total, excluded = None, None
    if conn:
        total = conn.execute("SELECT count(*) FROM sources").fetchone()[0]
        excluded = conn.execute(
            "SELECT count(*) FROM sources WHERE NOT coalesce((" + valid + "),false)"
        ).fetchone()[0]
        cells = rows(
            conn,
            f"""SELECT acquisition_id,population,
                    floor(ra_deg/360*{nx})::INTEGER AS longitude_index,
                    least({ny - 1},floor((sin(radians(dec_deg))+1)/2*{ny}))::INTEGER AS sine_latitude_index,
                    count(*) AS source_rows FROM sources WHERE {valid}
                    GROUP BY acquisition_id,population,longitude_index,sine_latitude_index
                    ORDER BY acquisition_id,sine_latitude_index,longitude_index,population""",
        )
        cells = tuple(
            dict(
                **x,
                cell_id=x["sine_latitude_index"] * nx + x["longitude_index"],
                parent_cell_id=(
                    (x["sine_latitude_index"] // 2) * (nx // 2)
                    + x["longitude_index"] // 2
                )
                if order
                else None,
                ra_bounds_deg=[
                    x["longitude_index"] * 360 / nx,
                    (x["longitude_index"] + 1) * 360 / nx,
                ],
                dec_bounds_deg=[
                    math.degrees(math.asin(2 * x["sine_latitude_index"] / ny - 1)),
                    math.degrees(
                        math.asin(2 * (x["sine_latitude_index"] + 1) / ny - 1)
                    ),
                ],
            )
            for x in cells
        )
        if sum(x["source_rows"] for x in cells) + excluded != total:
            raise ValueError("sky accounting contradiction")
    return FinkSkyProduct(
        "fink_equal_area_ra_sin_dec_v1",
        order,
        "reported ra/dec degrees; frame/epoch not independently established; not HEALPix",
        cells,
        total,
        excluded,
        refs,
    )


def finite_values(value, path="", nonfinite=None):
    """Preserve snapshot shape; nonfinite numbers -> null plus exact paths."""
    nonfinite = [] if nonfinite is None else nonfinite
    if isinstance(value, float) and not math.isfinite(value):
        nonfinite.append(path)
        return None
    if isinstance(value, dict):
        return {
            k: finite_values(v, path + "." + k, nonfinite)
            for k, v in sorted(value.items())
        }
    if isinstance(value, list):
        return [
            finite_values(v, path + f"[{i}]", nonfinite) for i, v in enumerate(value)
        ]
    return value


def entities(conn, refs_by_acquisition, capabilities, sample_per_population):
    if (
        isinstance(sample_per_population, bool)
        or not isinstance(sample_per_population, int)
        or not 0 <= sample_per_population <= 100
    ):
        raise ValueError(
            "sample size must be an integer from 0 through 100 per population"
        )
    if not conn:
        return (), ()
    records, snapshots = [], []
    feature_refs = tuple(
        x.namespace + "." + x.name
        for x in capabilities.discover_dimensions()
        if x.entity_level == "source"
    )
    for population in ("DIA", "SSO", "AMBIGUOUS"):
        sample = rows(
            conn,
            """SELECT s.*,b.pred_json,b.clf_json,b.lc_features_json,b.xm_json,b.misc_json
                             FROM sources s JOIN broker_snapshots b USING(acquisition_id,source_id)
                             WHERE s.population=? ORDER BY s.source_id,s.acquisition_id LIMIT ?""",
            [population, sample_per_population],
        )
        for row in sample:
            refs = (refs_by_acquisition[row["acquisition_id"]],)
            snapshot_id = (
                row["acquisition_id"]
                + "/diaSourceId/"
                + str(row["source_id"])
                + "/broker_snapshot"
            )
            nonfinite, modules, hashes = [], {}, {}
            for module in SNAPSHOTS:
                encoded = row[module + "_json"]
                hashes[module] = (
                    hashlib.sha256(encoded.encode()).hexdigest()
                    if encoded is not None
                    else None
                )
                modules[module] = (
                    finite_values(json.loads(encoded), module, nonfinite)
                    if encoded is not None
                    else None
                )
            snapshots.append(
                BrokerSnapshot(
                    snapshot_id,
                    str(row["source_id"]),
                    row["acquisition_id"],
                    row["observation_mjd_tai"],
                    modules,
                    hashes,
                    tuple(sorted(nonfinite)),
                    refs,
                )
            )
            fields = {
                name: row[name]
                for name in (
                    "ra_deg",
                    "dec_deg",
                    "band",
                    "psf_flux",
                    "psf_flux_err",
                    "observation_mjd_tai",
                    "is_sso",
                    "raw_file",
                    "topic",
                )
            }
            fields.update(
                time_format="MJD",
                time_scale="TAI",
                photometry_unit="nJy",
                photometry_meaning="delivered PSF difference flux and uncertainty",
                nonfinite_policy="null with paths",
            )
            bad = []
            fields = finite_values(fields, "", bad)
            fields["nonfinite_paths"] = sorted(bad)
            records.append(
                FinkEntityRecord(
                    population + "_SOURCE",
                    dict(
                        diaSourceId=str(row["source_id"]),
                        raw_diaObjectId=str(row["raw_dia_object_id"])
                        if row["raw_dia_object_id"] is not None
                        else None,
                        derived_diaObject_group=str(row["dia_object_id"])
                        if row["dia_object_id"] is not None
                        else None,
                    ),
                    population,
                    fields,
                    (snapshot_id,),
                    feature_refs,
                    refs,
                )
            )
    objects = rows(
        conn,
        """SELECT d.*,list_sort(list(DISTINCT s.acquisition_id)) AS contributing_acquisitions
                         FROM derived_dia_object_summary d JOIN sources s USING(dia_object_id)
                         GROUP BY ALL ORDER BY d.dia_object_id LIMIT ?""",
        [sample_per_population],
    )
    for row in objects:
        records.append(
            FinkEntityRecord(
                "DERIVED_DIA_OBJECT",
                dict(diaObjectId=str(row["dia_object_id"])),
                "DIA",
                dict(
                    delivered_source_rows=row["delivered_source_rows"],
                    first_observation_mjd_tai=row["first_observation_mjd_tai"],
                    last_observation_mjd_tai=row["last_observation_mjd_tai"],
                    temporal_baseline=row["last_observation_mjd_tai"]
                    - row["first_observation_mjd_tai"],
                    delivered_bands=row["delivered_bands"],
                    acquisition_count=row["acquisition_count"],
                    time_format="MJD",
                    time_scale="TAI",
                    is_derived=True,
                ),
                (),
                tuple(
                    x.namespace + "." + x.name
                    for x in capabilities.discover_dimensions()
                    if x.entity_level == "derived_dia_object"
                ),
                tuple(refs_by_acquisition[a] for a in row["contributing_acquisitions"]),
            )
        )
    return tuple(
        sorted(
            records,
            key=lambda x: (
                x.entity_type,
                int(x.native_ids.get("diaSourceId", x.native_ids.get("diaObjectId"))),
            ),
        )
    ), tuple(sorted(snapshots, key=lambda x: x.snapshot_id))
