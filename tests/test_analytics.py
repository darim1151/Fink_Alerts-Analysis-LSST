"""Synthetic qualification of cohort semantics, external views and admission."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from astropy.time import Time

from fink_lsst.analytics.contract import analytical_contract, schema_compatibility, utc_bounds
from fink_lsst.analytics import catalog as c
from fink_lsst.acquisition.states import AcquisitionState as S


def make_input(tmp_path,name='a',start='2026-02-25',stop='2026-02-28',offset=0,extra=False,omit=(),object_id=123):
    raw=tmp_path/'data/raw/data_transfer'/name; raw.mkdir(parents=True)
    times=Time([start+'T23:59:59.9',(Time(start).datetime.date()).isoformat()+'T00:00:00',start+'T12:00:00',start+'T12:30:00',start+'T13:00:00'],scale='utc').tai.mjd
    table=pa.table({
        'diaSourceId':pa.array([1+offset,2+offset,3+offset,4+offset,5+offset],type=pa.int64()),
        'diaObjectId':pa.array([object_id,object_id,0,0,999],type=pa.int64()),
        'midpointMjdTai':pa.array(times,type=pa.float64()),
        'pred':pa.array([{'is_sso':False,'new_flag':True},{'is_sso':False,'new_flag':False},{'is_sso':True,'new_flag':False},{'is_sso':False,'new_flag':False},{'is_sso':True,'new_flag':False}]),
        'ra':pa.array([1.]*5,type=pa.float64()),'dec':pa.array([2.]*5,type=pa.float64()),'band':['r','g','r','r','r'],
        'psfFlux':pa.array([10.,20.,30.,40.,50.],type=pa.float32()), 'psfFluxErr':pa.array([1.]*5,type=pa.float32()),
        'clf':pa.array([{'cats_class':1,'cats_score':0.1},{'cats_class':2,'cats_score':0.2},{'cats_class':0,'cats_score':float('nan')},{'cats_class':0,'cats_score':0.3},{'cats_class':0,'cats_score':0.4}]),
        'lc_features':pa.array([[('r',{'mean':1.})],[('r',{'mean':2.})],[],[],[]],type=pa.map_(pa.string(),pa.struct([('mean',pa.float64())]))),
        'misc':pa.array([{'firstDiaSourceMjdTaiFink':60000.}]*5)})
    if extra: table=table.append_column('future_optional',pa.array(['new']*5))
    if omit: table=table.drop(list(omit))
    pq.write_table(table,raw/"one'quoted.parquet")
    schema=table.schema; key=c.read_parquet_metadata_safe(raw/"one'quoted.parquet")['schema_hash']
    receipt=dict(topic='topic_'+name,raw_dir=str(raw.relative_to(tmp_path)),parquet_files=1,readable_rows=5,total_bytes=(raw/"one'quoted.parquet").stat().st_size,
                 reconciliation={'expected_topic_messages':5},schema_groups={key:{'files':1,'rows':5}})
    record=SimpleNamespace(acquisition_id='acq_'+name,topic='topic_'+name,state=S.DELIVERY_VALIDATED,
                           request=SimpleNamespace(start=start,stop=stop,science_profile='lsst_light_static_all_alerts_v1',survey='lsst'),
                           last_entry=lambda state:SimpleNamespace(evidence={'receipt':receipt}),entries=[SimpleNamespace(entry_sha256='evidence-sha')])
    return c.prepare_acquisition(record,tmp_path),record


def build(tmp_path,inputs):
    return c.build_catalog(inputs,tmp_path/'analytics.duckdb','code-sha','synthetic',str(tmp_path))


def test_single_external_catalog_entity_and_snapshot_semantics(tmp_path):
    item,_=make_input(tmp_path)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in item.raw.iterdir()}
    summary,smoke=build(tmp_path,[item])
    assert summary['source_rows']==5 and summary['external_alert_rows_materialized']==0
    assert smoke['status']=='PASS'
    assert all(n<100 for n in summary['physical_table_rows'].values())
    con=duckdb.connect(str(tmp_path/'analytics.duckdb'),read_only=True)
    assert con.execute('select count(*) from sources').fetchone()[0]==5
    assert con.execute('select count(*) from dia_source_snapshots').fetchone()[0]==2
    assert con.execute('select count(*) from sso_sources').fetchone()[0]==1
    assert con.execute('select count(*) from ambiguous_sources').fetchone()[0]==2
    assert con.execute('select dia_object_id from sso_sources').fetchone()[0] is None
    assert con.execute('select raw_dia_object_id from sso_sources').fetchone()[0]==0
    assert con.execute('select dia_object_id,delivered_source_rows,is_derived from derived_dia_object_summary').fetchone()==(123,2,True)
    assert con.execute("select json_extract(clf_json,'$.cats_class') from dia_source_snapshots order by source_id").fetchall()==[('1',),('2',)]
    assert con.execute("select json_extract(lc_features_json,'$.r.mean') from dia_source_snapshots order by source_id").fetchall()==[('1.0',),('2.0',)]
    assert con.execute('select delivered_rows from daily_coverage order by utc_date').fetchall()==[(5,),(0,),(0,)]
    assert con.execute("select status from daily_coverage where delivered_rows=0 limit 1").fetchone()[0]=='zero_rows_in_validated_Fink_delivery'
    assert len(c.query_utc(con,'sources','2026-02-25','2026-02-26').fetchall())==5
    con.close()
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in item.raw.iterdir()}==before


def test_multiple_compatible_acquisitions_and_optional_addition(tmp_path):
    a,_=make_input(tmp_path,name='a')
    b,_=make_input(tmp_path,name='b',start='2026-02-28',stop='2026-03-03',offset=100,extra=True,omit=('clf',))
    assert b.compatibility['groups'][0]['additional_fields']
    summary,_=build(tmp_path,[a,b])
    assert summary['source_rows']==10 and summary['acquisition_count']==2
    con=duckdb.connect(str(tmp_path/'analytics.duckdb'),read_only=True)
    assert con.execute('select count(distinct acquisition_id) from sources').fetchone()[0]==2
    assert con.execute('select delivered_source_rows,acquisition_count from derived_dia_object_summary').fetchone()==(4,2)
    assert con.execute("select count(*) from broker_snapshots where acquisition_id='acq_b' and clf_json is null").fetchone()[0]==5
    con.close()


@pytest.mark.parametrize('field,type_', [('diaSourceId',pa.int32()),('midpointMjdTai',pa.float32()),('pred',pa.struct([('is_sso',pa.string())]))])
def test_required_type_drift_incompatible(tmp_path,field,type_):
    item,_=make_input(tmp_path)
    schema=item.groups[0]['schema']
    altered=pa.schema([pa.field(f.name,type_ if f.name==field else f.type) for f in schema])
    assert schema_compatibility(altered)['status']=='INCOMPATIBLE'


def test_missing_required_field_rejected(tmp_path):
    with pytest.raises(c.AdmissionError,match='diaSourceId'):
        make_input(tmp_path,omit=('diaSourceId',))


def test_new_identity_or_history_requires_contract_review(tmp_path):
    item,_=make_input(tmp_path)
    schema=item.groups[0]['schema'].append(pa.field('prvDiaSources',pa.list_(pa.int64())))
    assert schema_compatibility(schema)['status']=='INCOMPATIBLE'


def test_overlap_default_reject(tmp_path):
    a,_=make_input(tmp_path,name='a')
    b,_=make_input(tmp_path,name='b',start='2026-02-26',stop='2026-03-01',offset=100)
    with pytest.raises(c.AdmissionError,match='overlapping'): build(tmp_path,[a,b])
    assert not (tmp_path/'analytics.duckdb').exists()


def test_duplicate_acquisition_reject(tmp_path):
    a,_=make_input(tmp_path)
    with pytest.raises(c.AdmissionError,match='duplicate acquisition'): build(tmp_path,[a,a])


def test_duplicate_source_across_nonoverlapping_cohort_reject(tmp_path):
    a,_=make_input(tmp_path,name='a')
    b,_=make_input(tmp_path,name='b',start='2026-02-28',stop='2026-03-03')
    with pytest.raises(c.AdmissionError,match='duplicate source IDs across cohort'): build(tmp_path,[a,b])


@pytest.mark.parametrize('values',[[1,1,3,4,5],[1,None,3,4,5],[0,2,3,4,5]])
def test_source_identity_query_qualification(tmp_path,values):
    item,_=make_input(tmp_path)
    path=next(item.raw.glob('*.parquet'))
    table=pq.read_table(path)
    table=table.set_column(0,'diaSourceId',pa.array(values,type=pa.int64()))
    pq.write_table(table,path)
    item.snapshot=c.raw_snapshot(item.raw)
    with pytest.raises(c.AdmissionError,match='source/time identity'): build(tmp_path,[item])


def test_UTC_TAI_leap_second_boundaries():
    lo,hi=utc_bounds('2016-12-31T23:59:60Z','2017-01-01T00:00:00Z')
    assert (hi-lo)*86400==pytest.approx(1.,abs=1e-6)
    lo,hi=utc_bounds('2016-12-31','2017-01-01')
    assert (hi-lo)*86400==pytest.approx(86401.,abs=1e-6)
    leap=Time('2016-12-31T23:59:60',scale='utc').tai.mjd
    assert lo<=leap<hi
    midnight=Time('2017-01-01T00:00:00',scale='utc').tai.mjd
    assert midnight==hi


@pytest.mark.parametrize('start,stop',[('2026-02-25+01:00','2026-02-26'),('2026-02-26','2026-02-25'),('not-time','2026-02-26')])
def test_bad_UTC_boundaries_fail(start,stop):
    with pytest.raises(ValueError): utc_bounds(start,stop)


@pytest.mark.parametrize('base',['data/processed','outputs','manifests'])
def test_run_no_overwrite(tmp_path,base):
    p=tmp_path/base/'run';p.mkdir(parents=True);(p/'sentinel').write_text('original')
    with pytest.raises(FileExistsError): c.reserve_run(tmp_path,'run')
    assert (p/'sentinel').read_text()=='original'


def test_catalog_no_overwrite(tmp_path):
    item,_=make_input(tmp_path)
    p=tmp_path/'analytics.duckdb';p.write_text('original')
    with pytest.raises(FileExistsError): build(tmp_path,[item])
    assert p.read_text()=='original'


def test_output_confinement(tmp_path):
    root=tmp_path/'root';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    (root/'outputs').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError): c.reserve_run(root,'run')


def test_unvalidated_and_wrong_profile_rejected(tmp_path):
    item,record=make_input(tmp_path)
    record.state=S.PLANNED
    with pytest.raises(c.AdmissionError,match='DELIVERY_VALIDATED'): c.prepare_acquisition(record,tmp_path)
    record.state=S.DELIVERY_VALIDATED;record.request.science_profile='other'
    with pytest.raises(c.AdmissionError,match='profile incompatible'): c.prepare_acquisition(record,tmp_path)


def test_raw_stat_change_detected(tmp_path):
    item,_=make_input(tmp_path)
    (item.raw/'unexpected').write_text('concurrent mutation')
    with pytest.raises(c.ContradictionError,match='raw names/size'): build(tmp_path,[item])


def test_characterization_contradiction_preserved(tmp_path):
    item,_=make_input(tmp_path)
    item.characterization={'composition':{'counts':{'DIA':3,'SSO':0,'ambiguous':2}}}
    with pytest.raises(c.ContradictionError,match='accepted G4A'): build(tmp_path,[item])


def test_readonly_open_checks_raw(tmp_path):
    item,_=make_input(tmp_path)
    processed=tmp_path/'data/processed/run';processed.mkdir(parents=True)
    c.build_catalog([item],processed/'analytics.duckdb','code','run',str(tmp_path))
    con=c.open_catalog(processed/'analytics.duckdb',tmp_path)
    assert con.execute('select count(*) from sources').fetchone()[0]==5
    with pytest.raises(duckdb.InvalidInputException): con.execute('create table forbidden(x int)')
    con.close()
    (item.raw/'changed').write_text('mutation')
    with pytest.raises(c.ContradictionError,match='fingerprint changed'): c.open_catalog(processed/'analytics.duckdb',tmp_path)


def test_contract_does_not_mutate():
    value=analytical_contract();value['required_fields']['diaSourceId'].append('string')
    assert analytical_contract()['required_fields']['diaSourceId']==['int64']


def mock_execute(tmp_path,monkeypatch):
    item,_=make_input(tmp_path)
    definition={'contract_id':'analysis_contract_v1','acquisitions':[{'acquisition_id':item.acquisition_id}]}
    monkeypatch.setattr(c,'load_cohort',lambda *args:([item],definition))
    monkeypatch.setattr(c.subprocess,'check_output',lambda argv,**kwargs: '' if 'status' in argv else 'code-sha')
    return item


def test_run_manifest_artifacts_and_raw_provenance(tmp_path,monkeypatch):
    item=mock_execute(tmp_path,monkeypatch)
    repo=Path(__file__).resolve().parents[1]
    manifest=c.execute(repo/'configs/analysis_cohorts/month1.json',tmp_path,'run',repo_root=repo,temporary_base=tmp_path)
    assert manifest['completion_status']=='PASS_WITH_LIMITATIONS'
    assert manifest['finished_utc'] and manifest['code_commit_sha']=='code-sha'
    assert manifest['package_versions']['duckdb']=='1.4.4'
    assert manifest['raw_read_only'][0]['before']==manifest['raw_read_only'][0]['after']
    for name,digest in manifest['artifact_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    assert len(manifest['artifact_sha256'])==7
    assert (tmp_path/'outputs/run/analytical_contract.json').exists()
    with pytest.raises(FileExistsError): c.execute(repo/'configs/analysis_cohorts/month1.json',tmp_path,'run',repo_root=repo)


def test_failed_execution_retains_evidence(tmp_path,monkeypatch):
    mock_execute(tmp_path,monkeypatch)
    monkeypatch.setattr(c,'build_catalog',lambda *args: (_ for _ in ()).throw(c.ContradictionError('synthetic contradiction')))
    repo=Path(__file__).resolve().parents[1]
    with pytest.raises(c.ContradictionError): c.execute(repo/'configs/analysis_cohorts/month1.json',tmp_path,'run',repo_root=repo,temporary_base=tmp_path)
    manifest=json.loads((tmp_path/'manifests/run/manifest.json').read_text())
    assert manifest['completion_status']=='FAILED' and manifest['error_type']=='ContradictionError'
    assert manifest['finished_utc'] and (tmp_path/'data/processed/run').exists()


def test_raw_symlink_escape_rejected(tmp_path):
    item,record=make_input(tmp_path)
    for p in item.raw.iterdir(): p.unlink()
    item.raw.rmdir()
    outside=tmp_path/'outside';outside.mkdir()
    item.raw.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError): c.prepare_acquisition(record,tmp_path)


def test_SQL_UTC_filter_leap_and_exact_midnight():
    con=duckdb.connect()
    con.execute('create table sources(source_id bigint,observation_mjd_tai double)')
    times=Time(['2016-12-31T23:59:59.9','2016-12-31T23:59:60','2017-01-01T00:00:00'],scale='utc').tai.mjd
    con.executemany('insert into sources values (?,?)',[(i,float(t)) for i,t in enumerate(times)])
    assert [x[0] for x in c.query_utc(con,'sources','2016-12-31T23:59:60Z','2017-01-01T00:00:00Z').fetchall()]==[1]
    assert len(c.query_utc(con,'sources','2016-12-31','2017-01-01').fetchall())==2
    assert len(c.query_utc(con,'sources','2017-01-01','2017-01-02').fetchall())==1
    con.close()


def test_failed_catalog_cannot_be_admitted_by_readonly_open(tmp_path):
    item,_=make_input(tmp_path)
    path=next(item.raw.glob('*.parquet'))
    table=pq.read_table(path).set_column(0,'diaSourceId',pa.array([1,1,3,4,5],type=pa.int64()))
    pq.write_table(table,path)
    item.snapshot=c.raw_snapshot(item.raw)
    processed=tmp_path/'data/processed/run';processed.mkdir(parents=True)
    catalog=processed/'analytics.duckdb'
    with pytest.raises(c.AdmissionError,match='source/time identity'):
        c.build_catalog([item],catalog,'code','run',str(tmp_path))
    assert catalog.exists()
    with pytest.raises(c.AdmissionError,match='qualification incomplete or failed'):
        c.open_catalog(catalog,tmp_path)
