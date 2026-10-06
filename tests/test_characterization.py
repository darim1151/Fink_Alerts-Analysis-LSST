"""Small synthetic fixtures exercise accounting, semantics and raw safety."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from astropy.time import Time

from fink_lsst import characterization as c
from fink_lsst.acquisition.states import AcquisitionState


def fixture_raw(tmp_path):
    raw = tmp_path/'data/raw/data_transfer/date_range/test/topic'
    raw.mkdir(parents=True)
    table = pa.table({
        'diaSourceId': pa.array([1, 2, 3, 3, None], type=pa.int64()),
        'diaObjectId': pa.array([100,100,None,0,900], type=pa.int64()),
        'midpointMjdTai': Time(['2026-02-25T23:59:59','2026-02-26T00:00:01','2026-02-27T00:00:00',
                               '2026-02-27T01:00:00','2026-02-28T00:00:00'],scale='utc').tai.mjd,
        'pred': pa.array([{'is_sso':False},{'is_sso':False},{'is_sso':True},{'is_sso':False},{'is_sso':True}]),
        'lc_features': pa.array([[('r',{'mean':1.0})],[('r',{'mean':2.0})],None,[],None],
                                type=pa.map_(pa.string(),pa.struct([('mean',pa.float64())]))),
        'misc': pa.array([{'firstDiaSourceMjdTaiFink':61000.0}]*5),
        'ra':[1.0,2.0,None,4.0,5.0], 'dec':[0.0]*5, 'band':['r']*5,
        'psfFlux':[1.0]*5,'psfFluxErr':[0.1]*5})
    pq.write_table(table.slice(0,2),raw/'a.parquet')
    pq.write_table(table.slice(2),raw/'b.parquet')
    schema_hash = c.read_parquet_metadata_safe(raw/'a.parquet')['schema_hash']
    receipt = dict(topic='topic',raw_dir=str(raw.relative_to(tmp_path)),parquet_files=2,readable_rows=5,
                   total_bytes=sum(p.stat().st_size for p in raw.iterdir()),schema_groups={schema_hash:{'files':2,'rows':5}})
    return raw, receipt


def test_inventory_and_nested_nulls():
    schema = pa.schema([('pred',pa.struct([('is_sso',pa.bool_())])),
                        ('lc',pa.map_(pa.string(),pa.struct([('mean',pa.float64())]))),
                        ('history',pa.list_(pa.struct([('time',pa.float64())])))])
    rows = {x['field_path']:x for x in c.inventory(schema)}
    assert rows['history']['classification'] == 'list'
    assert rows['history[].time']['coverage_unit'] == 'elements'
    assert 'lc[].value.mean' in rows
    arr = pa.array([None,{'is_sso':None},{'is_sso':False}],type=schema.field('pred').type)
    coverage = {}
    c.observe(arr,'pred',coverage)
    assert coverage['pred.is_sso'][:2] == [3,2]


def test_map_coverage():
    arr = pa.array([None,[],[('r',{'mean':None}),('g',{'mean':float('nan')})]],
                   type=pa.map_(pa.string(),pa.struct([('mean',pa.float64())])))
    coverage = {}
    c.observe(arr,'lc',coverage)
    assert coverage['lc'][:2] == [3,1]
    assert coverage['lc[].value.mean'] == [2,1,1]


@pytest.mark.parametrize('flag,object_id,expected',[
    (False,10,'DIA'),(True,None,'SSO'),(True,0,'SSO'),(True,10,'ambiguous'),
    (False,None,'ambiguous'),(False,0,'ambiguous'),(None,10,'ambiguous'),
    (None,None,'ambiguous'),(True,-1,'ambiguous'),(False,-1,'ambiguous')])
def test_composition(flag,object_id,expected):
    masks = c.classify(pa.array([flag],type=pa.bool_()),pa.array([object_id],type=pa.int64()))
    assert dict(zip(['DIA','SSO','ambiguous'],[c.ntrue(x) for x in masks])) == {
        name:int(name == expected) for name in ['DIA','SSO','ambiguous']}


def test_identifiers_and_multiplicity():
    stats = c.id_stats(pa.array([1,1,1,2,None],type=pa.int64()))
    assert (stats['total'],stats['null_count'],stats['distinct_count'],stats['duplicate_excess_rows']) == (5,1,2,2)
    assert stats['rows_in_duplicate_groups'] == 3
    mult,_ = c.multiplicity(pa.array([100,100,100,200]))
    assert (mult['unique_objects'],mult['exactly_one'],mult['more_than_one'],mult['maximum']) == (2,1,1,3)
    assert sum(x['objects']*x['alert_rows_per_object'] for x in mult['histogram']) == 4


def test_dates_tai_boundary_zeros_null_and_outside():
    tai = Time(['2026-02-25T23:59:59.9','2026-02-26T00:00:00','2026-02-28T00:00:00'],scale='utc').tai.mjd
    result = c.date_accounting(pa.array([*tai,None,float('nan')]),'2026-02-25','2026-02-28')
    # The first TAI time lies on Feb 26; UTC must still assign it to Feb 25.
    assert Time(tai[0],format='mjd',scale='tai').isot.startswith('2026-02-26')
    assert [x['rows'] for x in result['rows']] == [1,1,0]
    assert result['zero_row_dates'] == ['2026-02-27']
    assert result['out_of_window_rows'] == 1 and result['unmappable_time_rows'] == 2


def test_leap_second():
    times = Time(['2016-12-31T23:59:60','2017-01-01T00:00:00'],scale='utc').tai.mjd
    result = c.date_accounting(pa.array(times),'2016-12-31','2017-01-02')
    assert [x['rows'] for x in result['rows']] == [1,1]


def test_full_scan_read_only_and_accounting(tmp_path):
    raw,receipt = fixture_raw(tmp_path)
    before = {p.name:p.read_bytes() for p in raw.iterdir()}
    result,inv = c.scan(raw,'2026-02-25','2026-02-28',receipt)
    assert result['total_rows'] == 5
    assert result['composition']['counts'] == {'DIA':2,'SSO':1,'ambiguous':2}
    assert result['identifiers']['source']['duplicate_excess_rows'] == 1
    assert result['multiplicity']['maximum'] == 2
    assert result['repeated_object_sample']['objects'][0]['changed_feature_snapshot']
    assert result['dates']['out_of_window_rows'] == 1
    assert {p.name:p.read_bytes() for p in raw.iterdir()} == before
    assert result['raw_read_only']['before'] == result['raw_read_only']['after']
    assert next(x for x in inv if x['field_path']=='ssObjectId')['accessibility']=='ABSENT'


@pytest.mark.parametrize('change',['rows','bytes','schema'])
def test_receipt_contradiction(tmp_path,change):
    raw,receipt = fixture_raw(tmp_path)
    if change == 'rows': receipt['readable_rows'] += 1
    elif change == 'bytes': receipt['total_bytes'] += 1
    else: receipt['schema_groups'] = {'wrong':{}}
    with pytest.raises(ValueError,match='CONTRADICTION'):
        c.scan(raw,'2026-02-25','2026-02-28',receipt)


@pytest.mark.parametrize('base',['outputs','manifests'])
def test_existing_run_refused(tmp_path,base):
    target = tmp_path/base/'run'
    target.mkdir(parents=True)
    (target/'sentinel').write_text('original')
    with pytest.raises(FileExistsError): c.reserve_run(tmp_path,'run')
    assert (target/'sentinel').read_text() == 'original'
    assert not (tmp_path/('manifests' if base=='outputs' else 'outputs')/'run').exists()


def test_symlink_confinement(tmp_path):
    root = tmp_path/'root'; root.mkdir()
    other = tmp_path/'other'; other.mkdir()
    (root/'outputs').symlink_to(other,target_is_directory=True)
    with pytest.raises(ValueError): c.reserve_run(root,'run')


def mock_execution(monkeypatch,tmp_path):
    raw,receipt = fixture_raw(tmp_path)
    record = SimpleNamespace(state=AcquisitionState.DELIVERY_VALIDATED,topic='topic',
                             request=SimpleNamespace(start='2026-02-25',stop='2026-02-28'),
                             last_entry=lambda state:SimpleNamespace(evidence={'receipt':receipt}))
    monkeypatch.setattr(c.AcquisitionRegistry,'load',lambda *args:record)
    monkeypatch.setattr(c.subprocess,'check_output',lambda argv,**kwargs: '' if 'status' in argv else 'abcdef\n')
    return raw


def test_manifest_provenance_and_no_reuse(tmp_path,monkeypatch):
    mock_execution(monkeypatch,tmp_path)
    manifest = c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent,command=['test'])
    assert manifest['completion_status'] == 'PASS_WITH_LIMITATIONS'
    assert manifest['code_commit_sha'] == 'abcdef'
    assert manifest['finished_utc'] and manifest['package_versions']['astropy']
    assert manifest['output_sha256'] == c.output_digests(tmp_path/'outputs/run')
    report = (tmp_path/'outputs/run/characterization_report.md').read_text()
    assert 'zero Rubin alerts' in report and 'NOT_SUPPORTED' in report
    with pytest.raises(FileExistsError): c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent)


def test_failed_run_preserved(tmp_path,monkeypatch):
    mock_execution(monkeypatch,tmp_path)
    monkeypatch.setattr(c,'scan',lambda *args: (_ for _ in ()).throw(ValueError('failed scan')))
    with pytest.raises(ValueError,match='failed scan'): c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent)
    manifest = json.loads((tmp_path/'manifests/run/manifest.json').read_text())
    assert manifest['completion_status'] == 'FAILED' and manifest['finished_utc']
    with pytest.raises(FileExistsError): c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent)


def test_dirty_checkout_refused_before_outputs(tmp_path,monkeypatch):
    monkeypatch.setattr(c.subprocess,'check_output',lambda *args,**kwargs:' M dirty.py')
    with pytest.raises(ValueError,match='clean'): c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent)
    assert not (tmp_path/'outputs').exists()


def test_missing_critical_path_fails_closed(tmp_path):
    raw,receipt=fixture_raw(tmp_path)
    for p in raw.iterdir():
        table=pq.read_table(p).drop(['midpointMjdTai'])
        pq.write_table(table,p)
    receipt['total_bytes']=sum(p.stat().st_size for p in raw.iterdir())
    receipt['schema_groups']={c.read_parquet_metadata_safe(raw/'a.parquet')['schema_hash']:{}}
    with pytest.raises(ValueError,match='critical fields ABSENT'):
        c.scan(raw,'2026-02-25','2026-02-28',receipt)


def test_unvalidated_acquisition_fails_before_output(tmp_path,monkeypatch):
    mock_execution(monkeypatch,tmp_path)
    monkeypatch.setattr(c.AcquisitionRegistry,'load',lambda *args:SimpleNamespace(state=AcquisitionState.PLANNED))
    with pytest.raises(ValueError,match='DELIVERY_VALIDATED'):
        c.execute('acq',tmp_path,'run',repo_root=Path(__file__).parent.parent)
    assert not (tmp_path/'outputs').exists()


def test_raw_change_detected(tmp_path,monkeypatch):
    raw,receipt=fixture_raw(tmp_path)
    original=c.bounded_repeat_sample
    def changed(*args):
        result=original(*args)
        (raw/'unexpected').write_text('simulate concurrent producer change')
        return result
    monkeypatch.setattr(c,'bounded_repeat_sample',changed)
    with pytest.raises(ValueError,match='raw names/size/mtime/ctime changed'):
        c.scan(raw,'2026-02-25','2026-02-28',receipt)


def test_sample_excludes_ambiguous_sso_flag_for_same_id(tmp_path):
    raw,receipt=fixture_raw(tmp_path)
    table=pq.read_table(raw/'b.parquet')
    ids=pa.array([100,0,900],type=pa.int64())
    table=table.set_column(table.schema.get_field_index('diaObjectId'),'diaObjectId',ids)
    pq.write_table(table,raw/'b.parquet')
    receipt['total_bytes']=sum(p.stat().st_size for p in raw.iterdir())
    summary,_=c.scan(raw,'2026-02-25','2026-02-28',receipt)
    assert summary['repeated_object_sample']['objects'][0]['delivered_rows']==2
