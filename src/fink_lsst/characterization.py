"""Read-only, columnar characterization of a validated Light Static delivery.

No acquisition, persistent database, or processed row export is performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from astropy.time import Time
from astropy.utils import iers

from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState
from fink_lsst.bulk_transfer.raw_audit import discover_raw_files, read_parquet_metadata_safe
from fink_lsst.data_root import (REPO_ROOT, DATA_ROOT_ENV, confine_tree, storage_base,
                                 resolve_data_root, validate_path_component)

# Discovered Month-1 mappings, explicitly verified against every new schema.
# No fallback to broker time, no guessed nested path, no SSO-ID substitution.
REQUIRED = ('diaSourceId', 'diaObjectId', 'midpointMjdTai', 'pred.is_sso')
EXPECTED = ('diaObject', 'ssObjectId', 'ssSource', 'ssObject', 'mpc_orbits',
            'prvDiaSources', 'prvDiaForcedSources', 'diaForcedSources', 'nDiaSources',
            'pixelFlags', 'pixelFlags_bad', 'pixelFlags_saturated')
SCIENCE_ROOTS = {'diaSourceId', 'diaObjectId', 'midpointMjdTai', 'ra', 'dec', 'band',
                 'snr', 'psfFlux', 'psfFluxErr', 'scienceFlux', 'scienceFluxErr',
                 'templateFlux', 'templateFluxErr', 'reliability', 'pred', 'clf',
                 'xm', 'misc', 'lc_features', 'tns_type_recomputed'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def inventory(schema):
    """Recursively inventory structs, lists and map values; no guessed fields."""
    rows = []

    def walk(field, path, element=False):
        typ = field.type
        kind = ('struct' if pa.types.is_struct(typ) else 'map' if pa.types.is_map(typ)
                else 'list' if pa.types.is_list(typ) or pa.types.is_large_list(typ)
                or pa.types.is_fixed_size_list(typ) else 'scalar')
        rows.append(dict(field_path=path, arrow_type=str(typ), classification=kind,
                         coverage_unit='elements' if element else 'rows',
                         accessibility='SCHEMA_ONLY', observed_count=None,
                         null_count=None, null_fraction=None, nonfinite_count=None))
        if kind == 'struct':
            for child in typ:
                walk(child, path + '.' + child.name, element)
        elif kind == 'map':
            walk(pa.field('key', typ.key_type), path + '[].key', True)
            walk(pa.field('value', typ.item_type), path + '[].value', True)
        elif kind == 'list':
            walk(typ.value_field, path + '[]', True)

    for field in schema:
        walk(field, field.name)
    return rows


def field_array(table, path):
    parts = path.split('.')
    value = table[parts[0]]
    for part in parts[1:]:
        value = pc.struct_field(value, part)
    return value


def observe(array, path, coverage):
    """Vectorized coverage, propagating parent nulls through struct_field."""
    entry = coverage.setdefault(path, [0, 0, 0])
    entry[0] += len(array)
    entry[1] += array.null_count
    typ = array.type
    if pa.types.is_floating(typ):
        entry[2] += int(pc.sum(pc.cast(pc.fill_null(pc.invert(pc.is_finite(array)), False), pa.int64())).as_py() or 0)
    if pa.types.is_struct(typ):
        for child in typ:
            observe(pc.struct_field(array, child.name), path + '.' + child.name, coverage)
    elif pa.types.is_map(typ):
        # flatten drops null parent maps; denominators explicitly count entries.
        if isinstance(array, pa.ChunkedArray):
            array = array.combine_chunks()
        lists = pa.ListArray.from_arrays(array.offsets, array.values, mask=pc.is_null(array))
        entries = pc.list_flatten(lists)
        observe(pc.struct_field(entries, 'key'), path + '[].key', coverage)
        observe(pc.struct_field(entries, 'value'), path + '[].value', coverage)
    elif pa.types.is_list(typ) or pa.types.is_large_list(typ) or pa.types.is_fixed_size_list(typ):
        observe(pc.list_flatten(array), path + '[]', coverage)


def classify(pred, objects):
    """Conservative product classification; an ID alone never overrides pred.

    Positive DIA IDs paired with true SSO flags are ambiguous. Null/zero IDs
    with false flags are ambiguous. Negative IDs are invalid, not SSO IDs.
    """
    positive = pc.fill_null(pc.greater(objects, 0), False)
    missing = pc.or_(pc.is_null(objects), pc.fill_null(pc.equal(objects, 0), False))
    flag_true = pc.fill_null(pred, False)
    flag_false = pc.fill_null(pc.invert(pred), False)
    dia = pc.and_(flag_false, positive)
    sso = pc.and_(flag_true, missing)
    ambiguous = pc.invert(pc.or_(dia, sso))
    return dia, sso, ambiguous


def ntrue(mask):
    return int(pc.sum(pc.cast(mask, pa.int64())).as_py() or 0)


def id_stats(values):
    valid = pc.drop_null(values)
    counts = pc.value_counts(valid)
    repeated = pc.filter(counts, pc.greater(counts.field('counts'), 1))
    return dict(total=len(values), null_count=values.null_count, distinct_count=len(counts),
                duplicate_excess_rows=len(valid) - len(counts),
                rows_in_duplicate_groups=int(pc.sum(repeated.field('counts')).as_py() or 0),
                duplicate_id_groups=len(repeated),
                anomaly_examples=repeated.slice(0, 5).to_pylist())


def multiplicity(values):
    counts = pc.value_counts(pc.drop_null(values))
    nums = counts.field('counts').to_numpy(zero_copy_only=False)
    hist = pc.value_counts(counts.field('counts')).to_pylist()
    result = dict(unique_objects=len(counts), alert_rows=len(values),
                  exactly_one=int(np.sum(nums == 1)), more_than_one=int(np.sum(nums > 1)),
                  maximum=int(nums.max()) if len(nums) else 0,
                  quantiles={str(q): float(np.quantile(nums, q)) if len(nums) else None
                             for q in (0.5, 0.9, 0.95, 0.99)},
                  histogram=sorted([dict(alert_rows_per_object=x['values'], objects=x['counts'])
                                    for x in hist], key=lambda x: x['alert_rows_per_object']))
    return result, counts


def date_accounting(values, start, stop):
    """Assign UTC dates via Astropy/ERFA TAI conversion, including leap seconds."""
    # Auto-download disabled: no runtime network or approximate UTC substitution.
    iers.conf.auto_download = False
    arr = values.to_numpy(zero_copy_only=False)
    finite = np.isfinite(arr)
    times = Time(arr[finite], format='mjd', scale='tai')
    times.precision = 9
    utc = times.utc
    # Compare in the recorded float64 TAI representation against UTC midnights
    # converted by Astropy. Formatting a round-tripped float midnight can
    # fall microseconds before the boundary; equal stored doubles stay equal.
    if finite.any():
        low = datetime.fromisoformat(str(utc.min().isot)[:10]).date() - timedelta(days=1)
        high = datetime.fromisoformat(str(utc.max().isot)[:10]).date() + timedelta(days=2)
        labels = np.array([(low + timedelta(days=i)).isoformat() for i in range((high-low).days+1)])
        edges = Time(labels, format='iso', scale='utc').tai.mjd
        dates = labels[np.searchsorted(edges, arr[finite], side='right')-1]
    else:
        dates = np.array([], dtype='U10')
    unique, counts = np.unique(dates, return_counts=True)
    observed = dict(zip(unique.tolist(), counts.tolist()))
    start_day, stop_day = datetime.fromisoformat(start).date(), datetime.fromisoformat(stop).date()
    if stop_day <= start_day:
        raise ValueError('stop must follow start')
    requested = [(start_day + timedelta(days=i)).isoformat() for i in range((stop_day-start_day).days)]
    rows = [dict(date=d, rows=observed.get(d, 0), status='delivered_rows' if observed.get(d, 0)
                 else 'zero_rows_in_validated_Fink_delivery') for d in requested]
    outside = {d: n for d, n in observed.items() if d not in requested}
    return dict(field='midpointMjdTai', representation='MJD', scale='TAI',
                conversion="astropy.time.Time(format='mjd', scale='tai').utc; ERFA leap-second table",
                time_scale_evidence='data/fixtures/schema/fink_lsst_schema_sources.json: midpointMjdTai.doc',
                rows=rows, outside_date_counts=outside, out_of_window_rows=sum(outside.values()),
                unmappable_time_rows=int((~finite).sum()), requested_date_count=len(requested),
                dates_with_rows=[r['date'] for r in rows if r['rows']],
                zero_row_dates=[r['date'] for r in rows if not r['rows']],
                earliest_mjd_tai=float(arr[finite].min()) if finite.any() else None,
                latest_mjd_tai=float(arr[finite].max()) if finite.any() else None,
                earliest_utc=str(utc.min().isot) + 'Z' if finite.any() else None,
                latest_utc=str(utc.max().isot) + 'Z' if finite.any() else None)


def raw_snapshot(raw):
    """Names/size/mtime/ctime snapshot, no payload rehashing or atime comparison."""
    files = discover_raw_files(raw)
    facts = [(str(p.relative_to(raw)), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
             for p in files]
    return dict(files=len(files), parquet_files=sum(p.suffix.lower() == '.parquet' for p in files),
                total_bytes=sum(x[1] for x in facts),
                stat_fingerprint=hashlib.sha256(json.dumps(facts).encode()).hexdigest())


def bounded_repeat_sample(dataset, counts, fields):
    """12 deterministic IDs spanning low/high multiplicity; first/last snapshots.

    This is a purposive structural sample, never a population-validity sample.
    """
    repeated = pc.filter(counts, pc.greater(counts.field('counts'), 1))
    if not len(repeated):
        return dict(status='UNKNOWN', reason='no repeated DIA objects')
    table = pa.Table.from_arrays([repeated.field('values'), repeated.field('counts')], names=['id', 'n'])
    table = table.sort_by([('n', 'ascending'), ('id', 'ascending')])
    indices = sorted(set(np.linspace(0, len(table)-1, min(12, len(table)), dtype=int).tolist()))
    ids = table.take(pa.array(indices))['id'].to_pylist()
    columns = [x for x in ('diaObjectId', 'diaSourceId', 'midpointMjdTai', 'lc_features', 'misc', 'pred') if x in fields]
    sample = dataset.to_table(columns=columns, filter=ds.field('diaObjectId').isin(ids) & (ds.field(('pred', 'is_sso')) == False), use_threads=False)
    sample = sample.sort_by([('diaObjectId', 'ascending'), ('midpointMjdTai', 'ascending'), ('diaSourceId', 'ascending')])
    examples = []
    for ident in ids:
        subset = sample.filter(pc.equal(sample['diaObjectId'], ident))
        take = sorted(set([0, min(1, len(subset)-1), max(0, len(subset)-2), len(subset)-1]))
        snapshots = []
        for row in subset.take(pa.array(take)).to_pylist():
            features = row.get('lc_features')
            snapshots.append(dict(source_id=row['diaSourceId'], mjd_tai=row['midpointMjdTai'],
                                  feature_bands=[x[0] for x in features] if features else [],
                                  feature_sha256=hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest(),
                                  first_source_mjd_tai=(row.get('misc') or {}).get('firstDiaSourceMjdTaiFink')))
        examples.append(dict(object_id=ident, delivered_rows=len(subset), snapshots=snapshots,
                             changed_feature_snapshot=len({x['feature_sha256'] for x in snapshots}) > 1))
    return dict(status='VERIFIED', sampling='12 multiplicity-spaced DIA IDs; up to first/last two alerts per ID',
                objects=examples, selected_alert_rows=len(sample),
                limitation='purposive sample; feature-window, reset, estimator and calibration semantics remain UNKNOWN')


def capabilities(paths, coverage, identifiers):
    """Assess availability separately from scientific validation and completeness."""
    def available(path):
        c = coverage.get(path)
        return path in paths and c is not None and c[0] > c[1]

    definitions = [
        ('source-level photometric analysis', 'PARTIALLY_SUPPORTED', ['psfFlux','psfFluxErr','scienceFlux','scienceFluxErr','band','snr','reliability'], 'Fluxes/uncertainties available; full quality flags and calibration validation absent.'),
        ('object-level population analysis', 'PARTIALLY_SUPPORTED', ['diaObjectId','xm','pred'], 'Group only meaningful DIA IDs; source selection and alert-weighting bias matter; no full DiaObject record.'),
        ('repeated-detection/time-domain analysis', 'SUPPORTED', ['diaObjectId','diaSourceId','midpointMjdTai','band'], 'Delivered current detections can be ordered; this is not all-history completeness.'),
        ('lightcurve reconstruction', 'PARTIALLY_SUPPORTED', ['diaObjectId','diaSourceId','midpointMjdTai','psfFlux','psfFluxErr','band'], 'Detection-only delivery-window curves; no previous detections, forced sources, upper limits or pre-window reconstruction.'),
        ('variability-feature analysis', 'PARTIALLY_SUPPORTED', ['lc_features'], 'Broker feature snapshots available; estimators/windows/sentinels/scientific validity unverified.'),
        ('broker classification/prediction analysis', 'PARTIALLY_SUPPORTED', ['clf','pred'], 'Score/label evolution is measurable; class taxonomy, sentinels and calibration require validation.'),
        ('DIA/static-object selection', 'PARTIALLY_SUPPORTED', ['pred.is_sso','diaObjectId'], 'Conservative flag plus ID selection; no independent DiaObject struct.'),
        ('Solar System Object analysis', 'PARTIALLY_SUPPORTED', ['pred.is_sso','ra','dec','midpointMjdTai'], 'Flagged population/source analysis only; ssObjectId/designation/orbits absent, so object/orbit analysis unsupported.'),
        ('basic sky-position filtering', 'SUPPORTED', ['ra','dec'], 'Coordinates available; astrometric accuracy not validated.'),
        ('temporal filtering', 'SUPPORTED', ['midpointMjdTai'], 'MJD TAI converted explicitly to UTC with Astropy.'),
        ('future crossmatch/enrichment workflows', 'PARTIALLY_SUPPORTED', ['ra','dec','diaObjectId','xm'], 'Coordinates and broker context support enrichment; association/epoch and match quality unvalidated.'),
        ('project cadence and band summaries', 'SUPPORTED', ['diaObjectId','midpointMjdTai','band'], 'Within delivered detections only; SCIENCE_PLAN.md Phase 3.'),
        ('project upper-limit evolution', 'NOT_SUPPORTED', [], 'No forced-photometry/non-detection history in actual packet; SCIENCE_PLAN.md Phase 3.'),
        ('project candidate ranking', 'PARTIALLY_SUPPORTED', ['clf','pred','lc_features','reliability'], 'Missing-data-aware exploratory ranking inputs only; no demonstrated ranking performance; SCIENCE_PLAN.md Phase 5.'),
    ]
    result = []
    for family, status, evidence, note in definitions:
        if evidence and not all(available(p) for p in evidence):
            status = 'PARTIALLY_SUPPORTED' if any(available(p) for p in evidence) else 'NOT_SUPPORTED'
        if family in ('repeated-detection/time-domain analysis', 'project cadence and band summaries') and identifiers['source']['duplicate_excess_rows']:
            status = 'PARTIALLY_SUPPORTED'
            note += ' Duplicate source IDs require resolution.'
        result.append(dict(analysis=family, status=status, evidence=evidence, interpretation=note,
                           claim_status='VERIFIED' if status == 'NOT_SUPPORTED' else 'STRONGLY SUPPORTED'))
    return result


def scan(raw, start, stop, receipt):
    before = raw_snapshot(raw)
    if before['parquet_files'] != receipt['parquet_files'] or before['total_bytes'] != receipt['total_bytes']:
        raise ValueError('CONTRADICTION: raw file/byte accounting differs from validated receipt')
    files = [str(p) for p in discover_raw_files(raw) if p.suffix.lower() == '.parquet']
    first = read_parquet_metadata_safe(files[0])
    if not first['readable'] or first['schema_hash'] not in receipt['schema_groups']:
        raise ValueError('CONTRADICTION: observed initial schema differs from accepted delivery schema')
    dataset = ds.dataset(files, format='parquet')
    schema = dataset.schema
    inv = inventory(schema)
    paths = {x['field_path'] for x in inv}
    missing = set(REQUIRED) - paths
    if missing:
        raise ValueError('Unsupported packet mapping; critical fields ABSENT: ' + ', '.join(sorted(missing)))
    history = [x['field_path'] for x in inv if x['classification'] == 'list']
    new_identity = paths & {'diaObject','ssObjectId','ssSource','ssObject','mpc_orbits'}
    if new_identity:
        raise ValueError('New independent identity fields require reviewed discriminator mapping: ' + str(sorted(new_identity)))
    if history:
        raise ValueError('New list/history schema requires semantic review before characterization: ' + str(history))
    columns = sorted(set(schema.names) & SCIENCE_ROOTS)
    coverage, chunks = {}, []
    for batch in dataset.scanner(columns=columns, batch_size=65536, use_threads=False).to_batches():
        for name in columns:
            observe(batch.column(name), name, coverage)
        chunks.append(pa.record_batch([field_array(batch,p) for p in REQUIRED], names=list(REQUIRED)))
    compact = pa.Table.from_batches(chunks)
    total = len(compact)
    if total != receipt['readable_rows']:
        raise ValueError(f'CONTRADICTION: full scan rows {total} != receipt {receipt["readable_rows"]}')
    for row in inv:
        c = coverage.get(row['field_path'])
        if c:
            row.update(accessibility='READ_FULL_DATASET', observed_count=c[0], null_count=c[1],
                       null_fraction=c[1]/c[0] if c[0] else None, nonfinite_count=c[2])
    for p in EXPECTED:
        if p not in paths:
            inv.append(dict(field_path=p, arrow_type='ABSENT', classification='ABSENT', coverage_unit='rows',
                            accessibility='ABSENT', observed_count=None, null_count=None, null_fraction=None, nonfinite_count=None))
    pred, objects = compact['pred.is_sso'], compact['diaObjectId']
    dia, sso, ambiguous = classify(pred, objects)
    comp_counts = dict(DIA=ntrue(dia), SSO=ntrue(sso), ambiguous=ntrue(ambiguous))
    composition = dict(counts=comp_counts, fractions={k:v/total for k,v in comp_counts.items()},
                       discriminator='pred.is_sso plus positive diaObjectId; null/zero object IDs treated as no static association',
                       claim_status='STRONGLY SUPPORTED',
                       independent_sso_identity='ABSENT; no diaObject struct or ssObjectId/orbit field',
                       flag_counts=pc.value_counts(pred).to_pylist(),
                       disagreements=dict(sso_flag_with_positive_object_id=ntrue(pc.and_(pc.fill_null(pred,False), pc.fill_null(pc.greater(objects,0),False))),
                                          non_sso_flag_without_positive_object_id=ntrue(pc.and_(pc.fill_null(pc.invert(pred),False),pc.invert(pc.fill_null(pc.greater(objects,0),False))))),
                       null_flag_rows=pred.null_count,
                       object_zero_rows=ntrue(pc.fill_null(pc.equal(objects,0),False)),
                       object_negative_rows=ntrue(pc.fill_null(pc.less(objects,0),False)))
    ids = dict(source=id_stats(compact['diaSourceId']), all_object_ids=id_stats(objects),
               dia_object=id_stats(compact['diaObjectId'].filter(dia)),
               dia_object_population='pred.is_sso false AND diaObjectId > 0',
               sso_identifier=dict(status='ABSENT', population=comp_counts['SSO'], null_count=None, distinct_count=None,
                                   reason='No SSO identifier/designation field; diaObjectId is not substituted.'))
    mult, counts = multiplicity(compact['diaObjectId'].filter(dia))
    dates = date_accounting(compact['midpointMjdTai'], start, stop)
    sample = bounded_repeat_sample(dataset, counts, schema.names)
    after = raw_snapshot(raw)
    if before != after:
        raise ValueError('CONTRADICTION: raw names/size/mtime/ctime changed during scan')
    accounted_dates = sum(x['rows'] for x in dates['rows']) + dates['out_of_window_rows'] + dates['unmappable_time_rows']
    if accounted_dates != total or sum(comp_counts.values()) != total or mult['alert_rows'] != ids['dia_object']['total']:
        raise ValueError('CONTRADICTION: internal reconciliation failed')
    summary = dict(total_rows=total, observed_schema_hash=first['schema_hash'],
                   schema_accessibility='VERIFIED: complete dataset read for projected scientific columns without incompatible-schema failure; accepted one-group audit retained',
                   projected_columns=columns, unscanned_columns=sorted(set(schema.names)-set(columns)),
                   critical_field_coverage=[r for r in inv if r['accessibility'] == 'READ_FULL_DATASET'],
                   raw_read_only=dict(status='VERIFIED', before=before, after=after,
                                      method='read APIs only; names/size/mtime/ctime unchanged; no payload hashes repeated'),
                   dates=dates, composition=composition, identifiers=ids, multiplicity=mult,
                   repeated_object_sample=sample,
                   history=dict(claim_status='VERIFIED', measurement_arrays='ABSENT',
                                feature_map='lc_features: per-band named scalar summaries, not time-tagged measurements',
                                first_detection_summary='misc.firstDiaSourceMjdTaiFink',
                                row_semantics='current source measurement plus broker feature/classification/context snapshots, linked by DIA object ID where meaningful',
                                historical_measurement_duplication='No embedded measurement arrays to expand; source-ID duplicates must still be checked before detection curves.',
                                full_history_reconstruction='NOT_SUPPORTED',
                                unknown=['feature history window/reset rules', 'scientific/calibration validity of feature and prediction values',
                                         'SSO identity/orbit associations', 'Rubin-level completeness'],
                                future_field_roles=dict(alert_source=['diaSourceId','midpointMjdTai','band','ra','dec','*Flux','*FluxErr','snr','reliability'],
                                                        object=['diaObjectId as grouping key; pred/clf/xm/lc_features/misc are evolving snapshots, not immutable object records'],
                                                        historical_lightcurve=['No embedded measurements; detection rows can form an explicitly window-limited curve'],
                                                        SSO=['pred.is_sso allows flag selection only; identity and orbit fields ABSENT'])),
                   capability_matrix=capabilities(paths, coverage, ids),
                   recommendation='READY_WITH_LIMITATIONS', gate_status='PASS_WITH_LIMITATIONS',
                   limitations=['Validated Fink delivery does not establish Rubin scientific completeness.',
                                'Zero rows in a requested date do not establish zero Rubin alerts.',
                                'Light Static lacks full history, forced photometry, full DIA records and SSO identity/orbits.',
                                'Broker feature and prediction availability does not establish scientific validity.'],
                   contradictions=[])
    if ids['source']['null_count'] or ids['source']['duplicate_excess_rows'] or comp_counts['ambiguous'] or dates['unmappable_time_rows']:
        summary['limitations'].append('Identifier, discriminator or time anomalies must be resolved or explicitly excluded before science.')
    return summary, inv


def reserve_run(data_root, run_id):
    validate_path_component(run_id, 'run_id')
    paths = [confine_tree(storage_base(data_root,base)/run_id, storage_base(data_root,base)) for base in ('outputs','manifests')]
    if any(p.exists() for p in paths):
        raise FileExistsError('run ID already exists; previous evidence is never reused or overwritten')
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir()  # exclusive; a reservation failure is left as partial evidence
    return paths


def write_json(path, payload, exclusive=True):
    with path.open('x' if exclusive else 'w') as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def write_csv(path, rows):
    with path.open('x', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def output_digests(output):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}


def report(summary):
    lines = ['# Validated Fink Light Static delivery characterization', '',
             'Recommendation: **' + summary['recommendation'] + '**', '',
             'Claim labels: VERIFIED = directly measured; STRONGLY SUPPORTED = interpretation supported by packet evidence; UNKNOWN = not established.', '',
             'Scientific objectives: docs/SCIENCE_PLAN.md Phases 2–5 (population counts, object evolution, classification evolution, candidate ranking).', '',
             'The full projected scan reconfirms delivery accessibility. It does not demonstrate Rubin completeness or validate photometry/classifiers.', '']
    for title, key in [('Delivery','total_rows'),('Date coverage','dates'),('DIA / SSO','composition'),
                       ('Identifier integrity','identifiers'),('Repeated-object multiplicity','multiplicity'),
                       ('Bounded repeated-object evidence','repeated_object_sample'),('History semantics and future field roles','history')]:
        lines += ['## ' + title, '', '```json', json.dumps(summary[key],indent=2,sort_keys=True), '```','']
    lines += ['## Scientific capability matrix', '', '| Analysis | Status | Field evidence | Interpretation |', '|---|---|---|---|']
    for row in summary['capability_matrix']:
        lines.append('| ' + ' | '.join([row['analysis'],row['status'],', '.join(row['evidence']) or 'ABSENT',row['interpretation']]) + ' |')
    lines += ['', '## Risks and recommendation', ''] + ['- ' + x for x in summary['limitations']]
    lines += ['', 'Analytical-layer design can proceed for current detections and evolving DIA/broker snapshots, with explicit restricted-history and missing-SSO support. Control should require a product choice for full-history/forced-photometry/SSO work before using the same product for Months 2–3.', '',
              'Fink documents distinct packet options and separately selectable history/SSO fields: [Data Transfer documentation](https://doc.lsst.fink-broker.org/services/data_transfer/) (consulted 2026-10-05). Actual-data conclusions above rely on the observed schema, not this documentation.', '',
              'G4A MONTH-1 CHARACTERIZATION: ' + summary['gate_status'], '']
    return '\n'.join(lines)


def execute(acquisition_id, data_root, run_id, repo_root=REPO_ROOT, command=None):
    repo_root = Path(repo_root).resolve()
    data_root = resolve_data_root({DATA_ROOT_ENV: str(data_root)}, repo_root)
    git = lambda *args: subprocess.check_output(['git','-C',str(repo_root),*args],text=True).strip()
    if git('status','--porcelain'):
        raise ValueError('scientific run requires a clean committed checkout')
    record = AcquisitionRegistry(repo_root/'configs/acquisitions').load(acquisition_id)
    if record.state != AcquisitionState.DELIVERY_VALIDATED:
        raise ValueError('acquisition must be DELIVERY_VALIDATED')
    receipt = record.last_entry(AcquisitionState.DELIVERY_VALIDATED).evidence['receipt']
    if receipt['topic'] != record.topic:
        raise ValueError('receipt/topic mismatch')
    raw = confine_tree(data_root/receipt['raw_dir'], storage_base(data_root,'data/raw/data_transfer'))
    if not raw.is_dir():
        raise FileNotFoundError(raw)
    output, metadata = reserve_run(data_root,run_id)
    manifest_path = metadata/'manifest.json'
    started = time.monotonic()
    manifest = dict(run_id=run_id,acquisition_id=acquisition_id,topic=record.topic,input_raw_path=str(raw),
                    canonical_delivery_facts=receipt,code_commit_sha=git('rev-parse','HEAD'),
                    acquisition_log_entry_sha256=record.entries[-1].entry_sha256 if hasattr(record, 'entries') else None,
                    python_version=platform.python_version(),
                    package_versions={p:importlib.metadata.version(p) for p in ('pyarrow','numpy','astropy','pyerfa')},
                    command=command if command is not None else sys.argv,
                    started_utc=utc_now(),finished_utc=None,completion_status='RUNNING', output_dir=str(output),
                    output_sha256={})
    write_json(manifest_path,manifest)
    try:
        summary, inv = scan(raw,record.request.start,record.request.stop,receipt)
        summary.update(run_id=run_id,acquisition_id=acquisition_id,topic=record.topic,code_commit_sha=manifest['code_commit_sha'])
        write_json(output/'characterization_summary.json',summary)
        write_csv(output/'date_counts.csv',summary['dates']['rows'])
        write_csv(output/'field_inventory.csv',inv)
        if summary['multiplicity']['histogram']:
            write_csv(output/'multiplicity_histogram.csv',summary['multiplicity']['histogram'])
        with (output/'characterization_report.md').open('x') as handle:
            handle.write(report(summary))
        manifest.update(completion_status=summary['gate_status'],raw_read_only=summary['raw_read_only'])
    except Exception as exc:
        manifest.update(completion_status='FAILED',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:
        manifest.update(finished_utc=utc_now(),runtime_seconds=time.monotonic()-started,output_sha256=output_digests(output))
        # Only this invocation's lifecycle manifest is finalized; run reservations are never reused.
        write_json(manifest_path,manifest,exclusive=False)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition-id',required=True)
    parser.add_argument('--data-root',required=True,type=Path)
    parser.add_argument('--run-id',required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.acquisition_id,args.data_root,args.run_id, command=[sys.executable, *sys.argv]),indent=2))
    return 0
