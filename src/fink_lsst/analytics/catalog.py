"""Immutable cohort catalogs: external source views and compact provenance/QC."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState
from fink_lsst.bulk_transfer.raw_audit import discover_raw_files, read_parquet_metadata_safe
from fink_lsst.characterization import raw_snapshot, utc_now, write_json, output_digests
from fink_lsst.data_root import REPO_ROOT, DATA_ROOT_ENV, resolve_data_root, storage_base, confine_tree, validate_path_component
from .contract import CONTRACT_ID, SNAPSHOTS, analytical_contract, schema_compatibility, utc_bounds


class AdmissionError(ValueError):
    """Required semantics cannot safely join this cohort."""


class ContradictionError(AdmissionError):
    """Current analytical measurements contradict accepted evidence."""


@dataclass
class AcquisitionInput:
    acquisition_id: str
    topic: str
    start: str
    stop: str
    profile: str
    raw: Path
    receipt: dict
    log_entry_sha256: str
    groups: list
    compatibility: dict
    snapshot: dict
    characterization: dict | None = None
    linkage: dict = field(default_factory=dict)

    def provenance(self):
        return dict(acquisition_id=self.acquisition_id, topic=self.topic, start=self.start, stop=self.stop,
                    profile=self.profile, packet='Light static packet', state='DELIVERY_VALIDATED', raw_path=str(self.raw),
                    validated_rows=self.receipt['readable_rows'], expected_rows=self.receipt['reconciliation']['expected_topic_messages'],
                    schema_groups=self.receipt['schema_groups'], delivery_receipt_canonical_sha256=digest_json(self.receipt),
                    acquisition_log_entry_sha256=self.log_entry_sha256, raw_stat_fingerprint=self.snapshot['stat_fingerprint'],
                    characterization_linkage=self.linkage)


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare_acquisition(record, data_root, reference=None):
    if record.state != AcquisitionState.DELIVERY_VALIDATED:
        raise AdmissionError('DELIVERY_VALIDATED state required')
    if record.request.science_profile != 'lsst_light_static_all_alerts_v1' or record.request.survey != 'lsst':
        raise AdmissionError('packet/profile incompatible with analysis_contract_v1')
    receipt = record.last_entry(AcquisitionState.DELIVERY_VALIDATED).evidence['receipt']
    if receipt['topic'] != record.topic:
        raise ContradictionError('receipt topic differs from registry identity')
    raw = confine_tree(Path(data_root)/receipt['raw_dir'], storage_base(data_root, 'data/raw/data_transfer'))
    if not raw.is_dir():
        raise AdmissionError('validated raw path is missing')
    before = raw_snapshot(raw)
    if before['parquet_files'] != receipt['parquet_files'] or before['total_bytes'] != receipt['total_bytes']:
        raise ContradictionError('raw file/byte counts differ from accepted receipt')
    files = [p for p in discover_raw_files(raw) if p.suffix.lower() == '.parquet']
    accepted_hashes = set(receipt['schema_groups'])
    if not files or not accepted_hashes:
        raise AdmissionError('no accepted Parquet schemas')
    # Common one-schema path reuses accepted audit, inspecting only one header.
    # A multi-schema receipt needs explicit per-file grouping to avoid permissive unions.
    by_hash, schemas = {}, {}
    inspect_files = files if len(accepted_hashes) > 1 else files[:1]
    for p in inspect_files:
        meta = read_parquet_metadata_safe(p)
        if not meta['readable'] or meta['schema_hash'] not in accepted_hashes:
            raise ContradictionError('observed schema contradicts delivery audit')
        key = meta['schema_hash']
        by_hash.setdefault(key, []).append(str(p))
        schemas.setdefault(key, pq.read_schema(p))
    if len(accepted_hashes) == 1:
        by_hash[next(iter(by_hash))] = [str(p) for p in files]
    if set(by_hash) != accepted_hashes:
        raise ContradictionError('accepted schema group is missing')
    groups, evidence = [], []
    for key in sorted(by_hash):
        check = schema_compatibility(schemas[key])
        if check['status'] == 'INCOMPATIBLE':
            raise AdmissionError('; '.join(check['errors']))
        groups.append(dict(schema_hash=key, schema=schemas[key], files=by_hash[key]))
        evidence.append(dict(schema_hash=key, **check))
    compatibility = dict(contract_id=CONTRACT_ID, acquisition_id=record.acquisition_id,
                         status='COMPATIBLE_WITH_LIMITATIONS', groups=evidence,
                         source_identity_evidence='pending full analytical query qualification')
    characterization, linkage = None, {}
    if reference:
        validate_path_component(reference['run_id'], 'characterization run_id')
        out = confine_tree(storage_base(data_root,'outputs')/reference['run_id'],storage_base(data_root,'outputs'))
        meta = confine_tree(storage_base(data_root,'manifests')/reference['run_id'],storage_base(data_root,'manifests'))
        summary_path, manifest_path = out/'characterization_summary.json', meta/'manifest.json'
        payload = summary_path.read_bytes()
        expected = reference['summary_sha256']
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ContradictionError('characterization summary digest differs from cohort pin')
        characterization = json.loads(payload)
        manifest = json.loads(manifest_path.read_text())
        if manifest['completion_status'] not in ('PASS', 'PASS_WITH_LIMITATIONS'):
            raise AdmissionError('characterization did not finish successfully')
        if any(x['acquisition_id'] != record.acquisition_id or x['topic'] != record.topic for x in (characterization, manifest)):
            raise ContradictionError('characterization acquisition/topic binding differs')
        if manifest['input_raw_path'] != str(raw) or manifest['canonical_delivery_facts'] != receipt:
            raise ContradictionError('characterization raw/receipt binding differs')
        if manifest['output_sha256']['characterization_summary.json'] != expected:
            raise ContradictionError('characterization manifest digest differs')
        if manifest['code_commit_sha'] != characterization['code_commit_sha'] or manifest['code_commit_sha'] != reference['code_sha']:
            raise ContradictionError('characterization code revision differs from cohort pin')
        if manifest['raw_read_only']['after'] != before:
            raise ContradictionError('raw stat fingerprint differs from accepted characterization')
        source = characterization['identifiers']['source']
        if source['null_count'] or source['duplicate_excess_rows'] or source['distinct_count'] != receipt['readable_rows']:
            raise AdmissionError('characterization does not establish unique non-null source identity')
        compatibility['source_identity_evidence'] = 'qualified characterization; independently rechecked by catalog queries'
        linkage = dict(run_id=reference['run_id'], summary_path=str(summary_path), summary_sha256=expected,
                       manifest_path=str(manifest_path), manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                       code_sha=reference['code_sha'])
    return AcquisitionInput(record.acquisition_id, record.topic, record.request.start, record.request.stop,
                            record.request.science_profile, raw, receipt, record.entries[-1].entry_sha256,
                            groups, compatibility, before, characterization, linkage)


def validate_cohort(inputs):
    if not inputs:
        raise AdmissionError('cohort must contain at least one acquisition')
    if len({x.acquisition_id for x in inputs}) != len(inputs) or len({x.topic for x in inputs}) != len(inputs):
        raise AdmissionError('duplicate acquisition/topic in cohort')
    ordered = sorted(inputs, key=lambda x: x.start)
    for x in ordered:
        utc_bounds(x.start, x.stop)
    for left, right in zip(ordered, ordered[1:]):
        if right.start < left.stop:
            raise AdmissionError('overlapping requested acquisition windows are rejected by contract v1')


def load_cohort(path, data_root, repo_root=REPO_ROOT):
    path = Path(path).resolve()
    base = Path(repo_root)/'configs/analysis_cohorts'
    if base not in path.parents:
        raise AdmissionError('cohort definition must live in configs/analysis_cohorts of the clean checkout')
    definition = json.loads(path.read_text())
    if definition['contract_id'] != CONTRACT_ID:
        raise AdmissionError('unknown analytical contract')
    registry = AcquisitionRegistry(Path(repo_root)/'configs/acquisitions')
    inputs = [prepare_acquisition(registry.load(x['acquisition_id']), data_root, x.get('characterization')) for x in definition['acquisitions']]
    validate_cohort(inputs)
    return inputs, definition


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


SOURCE_COLUMNS = ['acquisition_id','topic','source_id','raw_dia_object_id','dia_object_id','population',
                  'is_sso','observation_mjd_tai','ra_deg','dec_deg','band','psf_flux','psf_flux_err',
                  'snr','science_flux','science_flux_err','template_flux','template_flux_err','reliability','raw_file']
SNAPSHOT_COLUMNS = ['acquisition_id','topic','source_id','dia_object_id','population','observation_mjd_tai'] + [p+'_json' for p in SNAPSHOTS]


def external_projection(item, group):
    fields = set(group['schema'].names)
    expressions = [f'{sql_literal(item.acquisition_id)} AS acquisition_id', f'{sql_literal(item.topic)} AS topic',
                   'diaSourceId::BIGINT AS source_id', 'diaObjectId::BIGINT AS raw_dia_object_id',
                   'CASE WHEN pred.is_sso = false AND diaObjectId > 0 THEN diaObjectId END::BIGINT AS dia_object_id',
                   "CASE WHEN pred.is_sso = false AND diaObjectId > 0 THEN 'DIA' WHEN pred.is_sso = true AND (diaObjectId IS NULL OR diaObjectId = 0) THEN 'SSO' ELSE 'AMBIGUOUS' END AS population",
                   'pred.is_sso AS is_sso','midpointMjdTai::DOUBLE AS observation_mjd_tai',
                   'ra::DOUBLE AS ra_deg','dec::DOUBLE AS dec_deg','band::VARCHAR AS band',
                   'psfFlux::DOUBLE AS psf_flux','psfFluxErr::DOUBLE AS psf_flux_err']
    names = dict(snr='snr',scienceFlux='science_flux',scienceFluxErr='science_flux_err',
                 templateFlux='template_flux',templateFluxErr='template_flux_err',reliability='reliability')
    expressions += [f'{p if p in fields else "NULL"}::DOUBLE AS {alias}' for p,alias in names.items()]
    expressions += ['filename::VARCHAR AS raw_file']
    expressions += [f'{"to_json("+p+")" if p in fields else "NULL::JSON"} AS {p}_json' for p in SNAPSHOTS]
    file_list = '[' + ','.join(sql_literal(p) for p in group['files']) + ']'
    return 'SELECT ' + ', '.join(expressions) + f' FROM read_parquet({file_list}, union_by_name=false, hive_partitioning=false, filename=true)'


def create_views(conn, inputs):
    branches = []
    for i, item in enumerate(inputs):
        for j, group in enumerate(item.groups):
            name = f'external_delivery_{i:03d}_{j:03d}'
            conn.execute(f'CREATE VIEW {name} AS ' + external_projection(item,group))
            branches.append('SELECT * FROM ' + name)
    # Each branch has an explicitly matched projection, irrespective of optional additions.
    conn.execute('CREATE VIEW delivered_alerts AS ' + ' UNION ALL '.join(branches))
    conn.execute('CREATE VIEW sources AS SELECT ' + ','.join(SOURCE_COLUMNS) + ' FROM delivered_alerts')
    conn.execute('CREATE VIEW broker_snapshots AS SELECT ' + ','.join(SNAPSHOT_COLUMNS) + ' FROM delivered_alerts')
    conn.execute("CREATE VIEW dia_source_snapshots AS SELECT * FROM delivered_alerts WHERE population='DIA'")
    conn.execute("CREATE VIEW sso_sources AS SELECT * FROM sources WHERE population='SSO'")
    conn.execute("CREATE VIEW ambiguous_sources AS SELECT * FROM sources WHERE population='AMBIGUOUS'")
    conn.execute("""CREATE VIEW derived_dia_object_summary AS SELECT dia_object_id,
        count(*) AS delivered_source_rows, min(source_id) AS representative_source_id, min(observation_mjd_tai) AS first_observation_mjd_tai,
        max(observation_mjd_tai) AS last_observation_mjd_tai, count(DISTINCT acquisition_id) AS acquisition_count,
        list_sort(list(DISTINCT band)) AS delivered_bands, true AS is_derived
        FROM sources WHERE population='DIA' GROUP BY dia_object_id""")


def peak_rss_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value/(1024*1024) if sys.platform == 'darwin' else value/1024


def measured(conn, name, sql, parameters=None):
    started = time.perf_counter()
    cur = conn.execute(sql, parameters or [])
    names = [x[0] for x in cur.description]
    rows = [dict(zip(names, row)) for row in cur.fetchall()]
    return dict(name=name, sql=sql, parameters=parameters or [], seconds=time.perf_counter()-started,
                process_peak_rss_mib=peak_rss_mib(), rows=rows)


def materialize_metadata(conn, inputs, code_sha, run_id):
    conn.execute('CREATE TABLE catalog_metadata(key VARCHAR PRIMARY KEY, value JSON)')
    conn.executemany('INSERT INTO catalog_metadata VALUES (?,?::JSON)',[(k,json.dumps(v)) for k,v in dict(
        contract_id=CONTRACT_ID,run_id=run_id,code_sha=code_sha,inputs=[x.provenance() for x in inputs],
        contract=analytical_contract(),semantics='external immutable raw; derived summaries are delivery-window measurements').items()])
    conn.execute('CREATE TABLE acquisitions(acquisition_id VARCHAR PRIMARY KEY, topic VARCHAR UNIQUE, profile VARCHAR, packet VARCHAR, requested_start DATE, requested_stop DATE, validation_state VARCHAR, raw_path VARCHAR, expected_rows UBIGINT, validated_rows UBIGINT, provenance JSON)')
    conn.executemany('INSERT INTO acquisitions VALUES (?,?,?,?,?,?,?,?,?,?,?::JSON)',[(x.acquisition_id,x.topic,x.profile,'Light static packet',x.start,x.stop,'DELIVERY_VALIDATED',str(x.raw),x.receipt['reconciliation']['expected_topic_messages'],x.receipt['readable_rows'],json.dumps(x.provenance())) for x in inputs])
    conn.execute('CREATE TABLE compatibility_evidence(acquisition_id VARCHAR PRIMARY KEY, evidence JSON)')
    conn.executemany('INSERT INTO compatibility_evidence VALUES (?,?::JSON)',[(x.acquisition_id,json.dumps(x.compatibility)) for x in inputs])
    dates = []
    for x in inputs:
        current, stop = date.fromisoformat(x.start), date.fromisoformat(x.stop)
        while current < stop:
            following = current + timedelta(days=1)
            lo, hi = utc_bounds(current.isoformat(),following.isoformat())
            dates.append((x.acquisition_id,current.isoformat(),lo,hi))
            current=following
    conn.execute('CREATE TABLE requested_date_boundaries(acquisition_id VARCHAR, utc_date DATE, start_mjd_tai DOUBLE, stop_mjd_tai DOUBLE, PRIMARY KEY(acquisition_id,utc_date))')
    conn.executemany('INSERT INTO requested_date_boundaries VALUES (?,?,?,?)',dates)


def qualify(conn, inputs):
    checks = []
    qc = measured(conn,'source_identity_and_composition',"""SELECT acquisition_id, count(*) AS total_rows,
        count(DISTINCT source_id) AS distinct_source_ids, count(*) FILTER (WHERE source_id IS NULL) AS null_source_ids,
        count(*) FILTER (WHERE source_id<=0) AS nonpositive_source_ids,
        count(*) FILTER (WHERE NOT isfinite(observation_mjd_tai) OR observation_mjd_tai IS NULL) AS invalid_times,
        count(*) FILTER (WHERE population='DIA') AS dia_rows,
        count(*) FILTER (WHERE population='SSO') AS sso_rows,
        count(*) FILTER (WHERE population='AMBIGUOUS') AS ambiguous_rows,
        count(*) FILTER (WHERE is_sso=true AND raw_dia_object_id>0) AS sso_positive_object_disagreement,
        count(*) FILTER (WHERE is_sso=false AND (raw_dia_object_id IS NULL OR raw_dia_object_id<=0)) AS dia_missing_object_disagreement,
        count(DISTINCT dia_object_id) AS distinct_dia_objects FROM sources GROUP BY acquisition_id ORDER BY acquisition_id""")
    checks.append(qc)
    for row in qc['rows']:
        item=next(x for x in inputs if x.acquisition_id==row['acquisition_id'])
        if row['total_rows'] != item.receipt['readable_rows']:
            raise ContradictionError('source count differs from validated delivery')
        if row['distinct_source_ids'] != row['total_rows'] or row['null_source_ids'] or row['nonpositive_source_ids'] or row['invalid_times']:
            raise AdmissionError('source/time identity invariant failed')
        if item.characterization:
            expected=item.characterization
            c=expected['composition']['counts']
            if (row['dia_rows'],row['sso_rows'],row['ambiguous_rows']) != (c['DIA'],c['SSO'],c['ambiguous']):
                raise ContradictionError('DIA/SSO/ambiguous counts differ from accepted G4A')
            if row['distinct_dia_objects'] != expected['multiplicity']['unique_objects']:
                raise ContradictionError('DIA object count differs from accepted G4A')
    global_ids=measured(conn,'global_source_identity','SELECT count(*) AS total_rows,count(DISTINCT source_id) AS distinct_source_ids FROM sources')
    checks.append(global_ids)
    if global_ids['rows'][0]['total_rows'] != global_ids['rows'][0]['distinct_source_ids']:
        raise AdmissionError('duplicate source IDs across cohort; no silent double-counting')
    conn.register('_qualified_source_qc', pa.Table.from_pylist(qc['rows']))
    conn.execute('CREATE TABLE source_qc AS SELECT * FROM _qualified_source_qc')
    conn.unregister('_qualified_source_qc')
    daily_sql="""SELECT b.acquisition_id,b.utc_date,b.start_mjd_tai,b.stop_mjd_tai,count(s.source_id)::UBIGINT AS delivered_rows,
        CASE WHEN count(s.source_id)=0 THEN 'zero_rows_in_validated_Fink_delivery' ELSE 'delivered_rows' END AS status
        FROM requested_date_boundaries b LEFT JOIN sources s ON b.acquisition_id=s.acquisition_id
        AND s.observation_mjd_tai>=b.start_mjd_tai AND s.observation_mjd_tai<b.stop_mjd_tai
        GROUP BY b.acquisition_id,b.utc_date,b.start_mjd_tai,b.stop_mjd_tai ORDER BY b.acquisition_id,b.utc_date"""
    started=time.perf_counter(); conn.execute('CREATE TABLE daily_coverage AS ' + daily_sql)
    checks.append(dict(name='daily_coverage_materialization',seconds=time.perf_counter()-started,process_peak_rss_mib=peak_rss_mib()))
    for item in inputs:
        daily=conn.execute('SELECT utc_date::VARCHAR,delivered_rows FROM daily_coverage WHERE acquisition_id=? ORDER BY utc_date',[item.acquisition_id]).fetchall()
        if sum(n for _,n in daily) != item.receipt['readable_rows']:
            raise AdmissionError('source times outside requested UTC window; contract v1 requires explicit resolution')
        if item.characterization and dict(daily) != {x['date']:x['rows'] for x in item.characterization['dates']['rows']}:
            raise ContradictionError('UTC daily coverage differs from accepted G4A')
    conn.execute('CREATE TABLE dia_object_multiplicity AS SELECT delivered_source_rows AS alerts_per_object,count(*)::UBIGINT AS objects FROM derived_dia_object_summary GROUP BY delivered_source_rows ORDER BY delivered_source_rows')
    for item in inputs:
        if item.characterization and len(inputs)==1:
            observed=conn.execute('SELECT alerts_per_object,objects FROM dia_object_multiplicity ORDER BY alerts_per_object').fetchall()
            expected=[(x['alert_rows_per_object'],x['objects']) for x in item.characterization['multiplicity']['histogram']]
            if observed != expected:
                raise ContradictionError('object multiplicity differs from accepted G4A')
    return checks


def query_utc(conn, relation, start, stop, acquisition_id=None):
    if relation not in {'sources','dia_source_snapshots','sso_sources','ambiguous_sources','broker_snapshots'}:
        raise ValueError('unknown time-filterable analytical relation')
    lo, hi=utc_bounds(start,stop)
    sql=f'SELECT * FROM {relation} WHERE observation_mjd_tai>=? AND observation_mjd_tai<?'
    parameters=[lo,hi]
    if acquisition_id is not None:
        sql+=' AND acquisition_id=?'; parameters.append(acquisition_id)
    return conn.execute(sql,parameters)


def benchmarks(conn, inputs):
    first=inputs[0]
    current=date.fromisoformat(first.start)
    lo,hi=utc_bounds(first.start,(current+timedelta(days=1)).isoformat())
    queries=[('total_count','SELECT count(*) AS rows FROM sources',[]),
             ('one_UTC_date','SELECT count(*) AS rows FROM sources WHERE acquisition_id=? AND observation_mjd_tai>=? AND observation_mjd_tai<?',[first.acquisition_id,lo,hi]),
             ('DIA_count',"SELECT count(*) AS rows FROM sources WHERE population='DIA'",[]),
             ('SSO_count','SELECT count(*) AS rows FROM sso_sources',[]),
             ('distinct_DIA_objects',"SELECT count(DISTINCT dia_object_id) AS objects FROM sources WHERE population='DIA'",[]),
             ('object_multiplicity','SELECT min(alerts_per_object) AS minimum,max(alerts_per_object) AS maximum,sum(objects) AS objects,sum(alerts_per_object*objects) AS DIA_rows FROM dia_object_multiplicity',[])]
    results=[measured(conn,*q) for q in queries]
    ident=conn.execute('SELECT representative_source_id FROM derived_dia_object_summary WHERE delivered_source_rows>1 ORDER BY delivered_source_rows DESC,dia_object_id LIMIT 1').fetchone()
    if ident:
        results.append(measured(conn,'representative_delivered_object_sequence',"SELECT source_id,acquisition_id,observation_mjd_tai,band,psf_flux,psf_flux_err FROM dia_source_snapshots WHERE dia_object_id=(SELECT dia_object_id FROM sources WHERE source_id=?) ORDER BY observation_mjd_tai,source_id LIMIT 8",[ident[0]]))
    results.append(measured(conn,'broker_classification_aggregation',"SELECT json_extract_string(clf_json,'$.cats_class') AS cats_class,count(*) AS source_snapshots FROM broker_snapshots GROUP BY cats_class ORDER BY source_snapshots DESC LIMIT 20"))
    results.append(measured(conn,'broker_feature_queryability',"SELECT source_id,json_extract(lc_features_json,'$.r.mean') AS r_mean,json_extract(misc_json,'$.firstDiaSourceMjdTaiFink') AS first_source_mjd_tai FROM broker_snapshots WHERE lc_features_json IS NOT NULL LIMIT 3"))
    score=measured(conn,'broker_nonfinite_score_QC',"SELECT count(*) FILTER (WHERE json_extract(clf_json,'$.cats_score') IS NOT NULL AND NOT isfinite(try_cast(json_extract(clf_json,'$.cats_score') AS DOUBLE))) AS nonfinite_cats_scores FROM broker_snapshots")
    results.append(score)
    if len(inputs)==1 and first.characterization:
        expected=next((r['nonfinite_count'] for r in first.characterization['critical_field_coverage'] if r['field_path']=='clf.cats_score'),None)
        if expected is not None and score['rows'][0]['nonfinite_cats_scores'] != expected:
            raise ContradictionError('broker snapshot nonfinite values differ from G4A')
    lookup={r['name']:r for r in results}
    expected_total=sum(x.receipt['readable_rows'] for x in inputs)
    if lookup['total_count']['rows'][0]['rows'] != expected_total:
        raise ContradictionError('analytical count view differs from qualified sources')
    expected_date=conn.execute('SELECT delivered_rows FROM daily_coverage WHERE acquisition_id=? AND utc_date=?',[first.acquisition_id,first.start]).fetchone()[0]
    if lookup['one_UTC_date']['rows'][0]['rows'] != expected_date:
        raise ContradictionError('UTC range query differs from qualified daily coverage')
    for name,col in [('DIA_count','dia_rows'),('SSO_count','sso_rows')]:
        expected=conn.execute(f'SELECT sum({col}) FROM source_qc').fetchone()[0]
        if lookup[name]['rows'][0]['rows'] != expected:
            raise ContradictionError('population view differs from qualified sources')
    expected_objects=conn.execute('SELECT sum(objects) FROM dia_object_multiplicity').fetchone()[0] or 0
    if lookup['distinct_DIA_objects']['rows'][0]['objects'] != expected_objects:
        raise ContradictionError('object query differs from qualified multiplicity')
    return results


def build_catalog(inputs, path, code_sha, run_id, temporary_dir):
    validate_cohort(inputs)
    if Path(path).exists():
        raise FileExistsError('catalog already exists; never overwrite')
    conn=duckdb.connect(str(path))
    try:
        conn.execute("SET threads=4")
        conn.execute("SET memory_limit='2GB'")
        conn.execute('SET temp_directory=' + sql_literal(temporary_dir))
        create_views(conn,inputs)
        materialize_metadata(conn,inputs,code_sha,run_id)
        checks=qualify(conn,inputs)
        results=benchmarks(conn,inputs)
        for item in inputs:
            if raw_snapshot(item.raw) != item.snapshot:
                raise ContradictionError('raw names/size/mtime/ctime changed during analytical run')
        conn.execute("INSERT INTO catalog_metadata VALUES ('qualification_status', '\"PASS\"'::JSON)")
        tables=conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
        physical={name:conn.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name, in tables}
        summary=dict(contract_id=CONTRACT_ID,source_rows=sum(x.receipt['readable_rows'] for x in inputs),
                     acquisition_count=len(inputs),source_qc=checks[0]['rows'],
                     daily_coverage=conn.execute('SELECT acquisition_id,utc_date::VARCHAR AS utc_date,delivered_rows,status FROM daily_coverage ORDER BY acquisition_id,utc_date').fetchall(),
                     physical_table_rows=physical,external_alert_rows_materialized=0,
                     views=[x[0] for x in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' AND table_type='VIEW' ORDER BY table_name").fetchall()],
                     object_multiplicity=next(x['rows'][0] for x in results if x['name']=='object_multiplicity'),
                     raw_read_only='VERIFIED: names/size/mtime/ctime unchanged; read APIs only',
                     G4A_reconciliation='VERIFIED for every linked characterization',
                     recommendation='SCALE_READY_WITH_LIMITATIONS')
        conn.execute('CHECKPOINT')
    finally:
        conn.close()
    summary['catalog_bytes']=Path(path).stat().st_size
    return summary,dict(status='PASS',qualification_checks=checks,benchmarks=results,
                        benchmark_context='single bounded pass in builder process; warm/uncold filesystem cache, cumulative process high-water RSS; no extrapolated tens-of-millions guarantee',
                        duckdb_threads=4,duckdb_memory_limit='2GB',process_peak_rss_mib=peak_rss_mib())


def reserve_run(root,run_id):
    validate_path_component(run_id,'run_id')
    paths=[confine_tree(storage_base(root,base)/run_id,storage_base(root,base)) for base in ('data/processed','outputs','manifests')]
    if any(p.exists() for p in paths):
        raise FileExistsError('run ID exists; no analytical evidence is overwritten')
    for p in paths:
        p.parent.mkdir(parents=True,exist_ok=True); p.mkdir()
    return paths


def open_catalog(path,data_root):
    path=confine_tree(path,storage_base(data_root,'data/processed'))
    conn=duckdb.connect(str(path),read_only=True)
    try:
        metadata=dict(conn.execute('SELECT key,value FROM catalog_metadata').fetchall())
        if json.loads(metadata['contract_id']) != CONTRACT_ID:
            raise AdmissionError('unsupported stored analytical contract')
        if json.loads(metadata.get('qualification_status', 'null')) != 'PASS':
            raise AdmissionError('catalog qualification incomplete or failed')
        for item in json.loads(metadata['inputs']):
            raw=confine_tree(item['raw_path'],storage_base(data_root,'data/raw/data_transfer'))
            if raw_snapshot(raw)['stat_fingerprint'] != item['raw_stat_fingerprint']:
                raise ContradictionError('catalog input raw stat fingerprint changed')
    except Exception:
        conn.close(); raise
    return conn


def layer_report(summary,smoke,inputs):
    qc=summary['source_qc']
    lines=['# G4B external-Parquet analytical foundation','',
           '**Decision: SCALE_READY_WITH_LIMITATIONS**','',
           f"VERIFIED: {summary['source_rows']:,} delivered sources from {len(inputs)} acquisition(s); zero raw alert rows materialized. Catalog size {summary['catalog_bytes']:,} bytes.",'',
           '## Physical storage and scientific relations','',
           '`acquisitions`, contract/catalog metadata, compatibility evidence, requested UTC/TAI boundaries, source QC, daily coverage and the compact multiplicity histogram are physical tables. The source, DIA, SSO, ambiguous, broker snapshot and DERIVED object-summary relations are external views. External views pin an explicit file list, never a glob that can silently admit later files.','',
           'One source row is one delivered current measurement, qualified by unique `diaSourceId`. `diaObjectId` groups DIA detections only. SSO zero object IDs are normalized to no DIA identity. Classification/features/context retain their source/time provenance in `broker_snapshots` and `dia_source_snapshots`; no timeless classification row is created. A derived object summary counts delivered rows and bands and retains first/last TAI time; it is not a canonical Rubin/Fink record.','',
           '## Time and daily coverage','',
           'Authoritative times remain `observation_mjd_tai`. Astropy converts UTC boundaries to TAI before half-open filtering. Every requested date is present. Zero counts mean `zero_rows_in_validated_Fink_delivery`, with no Rubin completeness inference.','',
           '## Compatibility and future acquisitions','',
           'A checked-in cohort lists accepted acquisition IDs and optional pinned characterization references. Registry replay, profile/type/semantic checks and exact source/time queries gate admission. Compatible optional additions have explicit inventories and per-schema projections. Required drift or new identity/history representations fail closed; no permissive union-by-name is used. Overlapping requested windows and duplicate source IDs across deliveries are rejected. Month 2 can join a new cohort/run without source changes after these checks. Existing catalogs/runs remain unchanged.','',
           '## Qualification','',
           '| Acquisition | Sources | Distinct sources | DIA | SSO | Ambiguous | DIA objects |','|---|---:|---:|---:|---:|---:|---:|']
    for r in qc:
        lines.append('| '+ ' | '.join(str(r[k]) for k in ('acquisition_id','total_rows','distinct_source_ids','dia_rows','sso_rows','ambiguous_rows','distinct_dia_objects'))+' |')
    lines+=['','G4A linkage and exact daily/multiplicity/snapshot checks are recorded in the manifest and compatibility linkage. Raw stat fingerprints remain unchanged.','',
            '## Bounded performance observations','',
            '| Query | Seconds |','|---|---:|']
    for r in smoke['benchmarks']:
        lines.append(f"| {r['name']} | {r['seconds']:.4f} |")
    lines+=['',f"Process high-water RSS: {smoke['process_peak_rss_mib']:.1f} MiB. Four DuckDB threads; 2GB DuckDB memory limit. This is a bounded cache-influenced benchmark, not a cold-cache or tens-of-millions certification.",'',
            '## Science and remaining limitations','',
            'STRONGLY SUPPORTED: delivered-source counts, sky/time selection, DIA grouping, delivery-window detection sequences, cadence/band summaries, broker-snapshot aggregation and future coordinate enrichment. Photometric quality, classification validity, feature definitions/nonfinite values and crossmatch accuracy require separate scientific qualification.','',
            'NOT_SUPPORTED by this baseline: full historical lightcurves, forced photometry/upper limits, canonical object records or SSO identity/orbit analysis. These have documented extension interfaces and no populated enrichment tables. DuckDB JSON preserves NaN/Infinity tokens; finite-value filtering is required before scientific score/feature analyses and standard JSON export.','',
            'The same Light Static baseline is appropriate for Month 2 only for these explicitly scoped families. It does not resolve G4A missing-date/completeness questions or provide the absent enrichment. Schema/identity/time/overlap contradictions stop later admission. No acquisition is performed by this gate.','',
            'Reference: [DuckDB external Parquet views](https://duckdb.org/docs/lts/data/parquet/overview). Measured results above come from this run.','',
            'G4B ANALYTICAL FOUNDATION: PASS_WITH_LIMITATIONS','']
    return '\n'.join(lines)


def execute(cohort_path,data_root,run_id,repo_root=REPO_ROOT,temporary_base=None,command=None):
    repo_root=Path(repo_root).resolve()
    root=resolve_data_root({DATA_ROOT_ENV:str(data_root)},repo_root)
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo_root),*args],text=True).strip()
    if git('status','--porcelain'):
        raise AdmissionError('real analytical execution requires clean committed code/cohort')
    git('ls-files','--error-unmatch',str(Path(cohort_path).resolve().relative_to(repo_root)))
    inputs,definition=load_cohort(cohort_path,root,repo_root)
    processed,outputs,metadata=reserve_run(root,run_id)
    versions={p:importlib.metadata.version(p) for p in ('duckdb','pyarrow','numpy','astropy','pyerfa')}
    sha=git('rev-parse','HEAD')
    started=time.monotonic()
    manifest=dict(run_id=run_id,contract_id=CONTRACT_ID,cohort=definition,cohort_sha256=digest_json(definition),
                  code_commit_sha=sha,python_version=platform.python_version(),package_versions=versions,
                  command=command or [sys.executable,*sys.argv],started_utc=utc_now(),finished_utc=None,
                  completion_status='RUNNING',acquisitions=[x.provenance() for x in inputs],
                  processed_dir=str(processed),output_dir=str(outputs),artifact_sha256={})
    write_json(metadata/'manifest.json',manifest)
    try:
        with tempfile.TemporaryDirectory(prefix='mdarim-fink-g4b-',dir=temporary_base) as temporary:
            summary,smoke=build_catalog(inputs,processed/'analytics.duckdb',sha,run_id,temporary)
        readiness=dict(decision='SCALE_READY_WITH_LIMITATIONS',admission_status='COMPATIBLE_WITH_LIMITATIONS',
                       operational=True,raw_rows_copied=0,future_cohort_without_source_changes=True,
                       overlap_policy='reject',source_key_policy='global uniqueness requalified',
                       schema_policy=analytical_contract()['schema_drift'],
                       same_Light_Static_for_Month2='appropriate only for delivered detections, DIA grouping and evolving broker snapshots',
                       enriched_science_requires_separate_product=True,
                       benchmark_observations=dict(catalog_bytes=summary['catalog_bytes'],peak_rss_mib=smoke['process_peak_rss_mib'],
                                                  query_seconds={x['name']:x['seconds'] for x in smoke['benchmarks']}))
        write_json(outputs/'analytical_contract.json',analytical_contract())
        write_json(outputs/'catalog_summary.json',summary)
        write_json(outputs/'scale_readiness.json',readiness)
        write_json(outputs/'query_smoke_tests.json',smoke)
        write_json(outputs/'compatibility_linkage.json',[dict(acquisition_id=x.acquisition_id,compatibility=x.compatibility,characterization_linkage=x.linkage) for x in inputs])
        (outputs/'analytical_layer_report.md').write_text(layer_report(summary,smoke,inputs))
        manifest.update(completion_status='PASS_WITH_LIMITATIONS',raw_read_only=[dict(acquisition_id=x.acquisition_id,before=x.snapshot,after=raw_snapshot(x.raw)) for x in inputs])
    except Exception as exc:
        manifest.update(completion_status='FAILED',error_type=type(exc).__name__,error=str(exc))
        raise
    finally:
        manifest.update(finished_utc=utc_now(),runtime_seconds=time.monotonic()-started,
                        artifact_sha256={**{str(outputs/p):h for p,h in output_digests(outputs).items()},
                                         **{str(processed/p):h for p,h in output_digests(processed).items()}})
        write_json(metadata/'manifest.json',manifest,exclusive=False)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    check=sub.add_parser('check',help='read-only acquisition compatibility')
    check.add_argument('--acquisition-id',required=True); check.add_argument('--data-root',required=True,type=Path)
    build=sub.add_parser('build',help='freeze one or many validated acquisitions into a new external-view catalog')
    build.add_argument('--cohort',required=True,type=Path); build.add_argument('--data-root',required=True,type=Path)
    build.add_argument('--run-id',required=True); build.add_argument('--temporary-base',type=Path)
    args=parser.parse_args()
    if args.action=='check':
        root=resolve_data_root({DATA_ROOT_ENV:str(args.data_root)})
        try:
            item=prepare_acquisition(AcquisitionRegistry(REPO_ROOT/'configs/acquisitions').load(args.acquisition_id),root)
            print(json.dumps(item.compatibility,indent=2)); return 0
        except (ValueError,OSError) as exc:
            print(json.dumps(dict(status='INCOMPATIBLE',reason=str(exc)),indent=2)); return 1
    print(json.dumps(execute(args.cohort,args.data_root,args.run_id,temporary_base=args.temporary_base),indent=2))
    return 0
