"""Versioned scientific admission contract for external Light Static analysis."""
from __future__ import annotations

import copy
import re

import pyarrow as pa
from astropy.time import Time
from astropy.utils import iers

from fink_lsst.characterization import inventory

CONTRACT_ID = 'analysis_contract_v1'
REQUIRED = {
    'diaSourceId': ['int64'], 'diaObjectId': ['int64'],
    'midpointMjdTai': ['double'], 'pred.is_sso': ['bool'],
    'ra': ['double'], 'dec': ['double'], 'band': ['string', 'large_string'],
    'psfFlux': ['float', 'double'], 'psfFluxErr': ['float', 'double'],
}
OPTIONAL_NUMERIC = {'snr', 'scienceFlux', 'scienceFluxErr', 'templateFlux', 'templateFluxErr', 'reliability'}
SNAPSHOTS = ('pred', 'clf', 'lc_features', 'xm', 'misc')
REVIEW_FIELDS = {'diaObject', 'ssObjectId', 'ssObject', 'ssSource', 'mpc_orbits', 'prvDiaSources', 'prvDiaForcedSources', 'diaForcedSources'}


def analytical_contract():
    return copy.deepcopy(dict(
        contract_id=CONTRACT_ID, version=1,
        profile='lsst_light_static_all_alerts_v1', survey='lsst', packet='Light static packet',
        required_fields=REQUIRED,
        optional_fields={p: ['float', 'double'] for p in sorted(OPTIONAL_NUMERIC)},
        evolving_broker_fields={p: 'source-time snapshot; optional except pred.is_sso' for p in SNAPSHOTS},
        known_absent_capabilities=['complete historical detections', 'forced photometry / upper limits',
                                   'canonical DiaObject record', 'SSO identity / orbits'],
        identity=dict(source='positive, non-null, globally unique diaSourceId in each admitted cohort',
                      DIA='pred.is_sso false AND diaObjectId > 0',
                      SSO='pred.is_sso true AND diaObjectId null or zero; zero is never an object identity',
                      ambiguous='all other discriminator combinations retained separately',
                      object='derived grouping key for DIA; broker values remain source-time snapshots'),
        time=dict(authoritative_field='midpointMjdTai', sql_name='observation_mjd_tai', scale='TAI', format='MJD',
                  UTC_filter='Astropy UTC boundaries -> TAI MJD; half-open numeric comparisons; no SQL offset shortcut'),
        schema_drift=dict(required='missing/type-incompatible fields reject admission',
                          optional='known numeric width changes or additive unknown fields accepted with explicit inventory; no union_by_name',
                          evolving='nested additions retained in DuckDB JSON snapshots; pred.is_sso remains strictly boolean',
                          new_identity_or_history='requires reviewed contract extension, never inferred'),
        overlap_policy='reject any overlapping requested windows; also reject duplicate source IDs across the complete cohort',
        admission='accepted registry record -> schema/profile compatibility -> exact query integrity -> new immutable cohort catalog',
        snapshot_encoding='DuckDB to_json of raw structs/maps; DuckDB NaN/Infinity tokens are preserved, not assumed RFC8259 exports; use TRY_CAST/isfinite',
        extensions=dict(historical_detections='future source-ID-linked measurements with origin/time scale',
                        forced_photometry='future measurement key/object/time/band/flux/error/non-detection provenance',
                        object_context='future versioned object/epoch/association provenance',
                        SSO_identity_orbits='future source linkage to evidence-qualified SSO key and orbit epoch'),
        extensions_populated=False,
    ))


def schema_compatibility(schema):
    rows = inventory(schema)
    paths = {r['field_path']: r['arrow_type'] for r in rows}
    errors = []
    for path, allowed in REQUIRED.items():
        if paths.get(path) not in allowed:
            errors.append(f'{path}: expected {allowed}; observed {paths.get(path, "ABSENT")}')
    for path in OPTIONAL_NUMERIC & set(paths):
        if paths[path] not in ('float', 'double'):
            errors.append(f'optional {path}: unsupported type {paths[path]}')
    for path in ('pred', 'clf', 'xm', 'misc'):
        if path in schema.names and not pa.types.is_struct(schema.field(path).type):
            errors.append(f'{path}: expected struct snapshot')
    if 'lc_features' in schema.names:
        typ = schema.field('lc_features').type
        if not (pa.types.is_map(typ) and pa.types.is_string(typ.key_type) and pa.types.is_struct(typ.item_type)):
            errors.append('lc_features: expected map<string, struct> of feature summaries')
    review = REVIEW_FIELDS & set(paths)
    if review:
        errors.append('new identity/history representation requires reviewed contract: ' + ', '.join(sorted(review)))
    optional_absent = sorted((OPTIONAL_NUMERIC | set(SNAPSHOTS)) - set(schema.names))
    known = set(REQUIRED) | OPTIONAL_NUMERIC | set(SNAPSHOTS)
    additions = [r for r in rows if r['field_path'].split('.')[0].split('[')[0] not in known]
    return dict(status='INCOMPATIBLE' if errors else 'COMPATIBLE_WITH_LIMITATIONS', errors=errors,
                required_fields={p: paths.get(p, 'ABSENT') for p in REQUIRED}, optional_absent=optional_absent,
                additional_fields=additions, field_inventory=rows,
                limitations=['Light Static has no qualified full-history/forced-photometry/SSO-identity representation'])


def utc_bounds(start, stop):
    """Convert explicit UTC ISO boundaries correctly, including leap seconds."""
    pattern = r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?)?Z?'
    if not all(isinstance(x, str) and re.fullmatch(pattern, x) for x in (start, stop)):
        raise ValueError('boundaries must be UTC ISO dates/times, optionally ending Z; timezone offsets are not accepted')
    iers.conf.auto_download = False
    times = Time([start.rstrip('Z'), stop.rstrip('Z')], format='isot', scale='utc').tai
    if not times[1] > times[0]:
        raise ValueError('stop must follow start')
    return float(times[0].mjd), float(times[1].mjd)
