from types import SimpleNamespace
import pytest
from src.design.detailing import (DRAWING_POLICY_ID, DRAWING_POLICY_REVISION_R9_ID,
    DetailingError, PhysicalRow, make_detailing_engine)

def test_r9_rejects_four_main_layers_in_final_check():
    engine = make_detailing_engine(detailing_policy_id=DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R9_ID)
    face = SimpleNamespace(rows=tuple(PhysicalRow(2, 16) for _ in range(4)))
    region = SimpleNamespace(name='left', top=face, bottom=face)
    with pytest.raises(DetailingError, match='POLICY_MAX_MAIN_BAR_LAYERS_EXCEEDED'):
        engine._validate_revision_layout(SimpleNamespace(name='beam'), [region])
def test_lookup_configuration_and_factory_are_explicit():
    from src.config import load_config
    from src.design.detailing import make_fresh_detailing_engine, DRAWING_POLICY_ID
    from src.design.offline_style import DRAWING_POLICY_REVISION_R8_ID, DRAWING_POLICY_REVISION_R9_ID
    # The example's accepted default enables R9 candidate lookup.
    assert load_config('config.example.yaml').design.candidate_lookup == 'auto'
    assert load_config('config.example.yaml', candidate_lookup='auto').design.candidate_lookup == 'auto'
    assert make_fresh_detailing_engine(DRAWING_POLICY_ID).detailing_policy_revision_id == DRAWING_POLICY_REVISION_R8_ID
    engine = make_fresh_detailing_engine(DRAWING_POLICY_ID, candidate_lookup='auto')
    assert engine.detailing_policy_revision_id == DRAWING_POLICY_REVISION_R9_ID
    assert engine.candidate_lookup_provider is not None
def test_candidate_lookup_cli_modes():
    from src.cli import _parser
    assert _parser().parse_args(['--candidate-lookup', 'auto']).candidate_lookup == 'auto'
    assert _parser().parse_args(['--candidate-lookup', 'off']).candidate_lookup == 'off'
    with pytest.raises(SystemExit):
        _parser().parse_args(['--candidate-lookup', 'unknown'])
def test_replay_preserves_recorded_revision_when_config_enables_lookup(tmp_path, monkeypatch):
    import argparse, json
    from src import cli
    source = tmp_path / 'old.json'
    source.write_text(json.dumps({'metadata': {'detailing_policy_revision_id': 'historic'}}), encoding='utf8')
    seen = {}
    def replay(payload, **kwargs):
        seen.update(kwargs)
        return payload
    monkeypatch.setattr(cli, 'redetail_payload', replay)
    monkeypatch.setattr(cli, 'load_config', lambda *a, **kw: SimpleNamespace(design=SimpleNamespace(
        candidate_lookup='auto', layout_dictionary_path=None, rebar_workers=1)))
    args = argparse.Namespace(replay_payload=source, output_dir=tmp_path/'out', config=None,
        detailing_policy=None, allow_policy_revision_change=False, layout_dictionary=None,
        rebar_workers=None, candidate_lookup=None)
    cli._replay_payload(args)
    assert 'engine' not in seen
    assert 'allow_policy_revision_change' not in seen
def test_r9_parallel_dispatch_orders_long_lines_and_preserves_rows(monkeypatch):
    from concurrent.futures import Future
    from src import rebar_workbook as w
    submitted = []
    class Executor:
        def __init__(self, max_workers, **kwargs):
            submitted.append(('workers', max_workers))
        def submit(self, function, job):
            key, indices, inputs, contexts = job
            submitted.append((key[-1], inputs))
            f = Future()
            f.set_result({'line_key':key, 'indices':indices, 'contexts':contexts,
                'initial_results':inputs, 'results':inputs, 'design_errors':{}, 'coordination_errors':{},
                'worker_pid':1, 'started_at_epoch':0.0, 'ended_at_epoch':0.01, 'elapsed_seconds':0.01})
            return f
        def shutdown(self, **kwargs): pass
    monkeypatch.setattr(w, 'ProcessPoolExecutor', Executor)
    monkeypatch.setattr(w, '_worker_engine_spec', lambda e: {})
    monkeypatch.setattr(w, '_prepare_candidate_lookup', lambda *args: None)
    monkeypatch.setattr(w.os, 'cpu_count', lambda: 8)
    contexts=[({'story':'S', 'beam_type':'PB', 'beam_line':line}, None) for line in ('short','long','long','long','tie','tie','short2','tie2','tie2')]
    inputs=list(range(len(contexts)))
    engine=SimpleNamespace(coordination_solver_revision_id='coordination_20261031_indexed_candidates_v1', layout_dictionary_bank=None)
    initial, final, _, _, workers=w._dispatch_beam_line_work(inputs,contexts,engine,workers=2)
    assert submitted[0] == ('workers',2)
    assert [x[0] for x in submitted[1:]] == ['long','tie','tie2','short','short2']
    assert submitted[1][1] == (1,2,3)
    assert initial == inputs and final == inputs and workers == 2
def test_configured_auto_respects_legacy_and_explicit_dictionary(tmp_path):
    import yaml
    from pathlib import Path
    from src.config import load_config
    config = yaml.safe_load(Path('config.example.yaml').read_text(encoding='utf8'))
    config['design']['candidate_lookup'] = 'auto'
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf8')
    assert load_config(path, detailing_policy_id='legacy').design.candidate_lookup == 'off'
    assert load_config(path, layout_dictionary_path=tmp_path/'bank.json').design.candidate_lookup == 'off'
    assert load_config(path, detailing_policy_id='legacy', candidate_lookup='auto').design.candidate_lookup == 'auto'
    assert load_config(path, layout_dictionary_path=tmp_path/'bank.json', candidate_lookup='auto').design.candidate_lookup == 'auto'
def test_implicit_r9_replay_prepares_lookup_without_switching_revision(tmp_path, monkeypatch):
    from src import rebar_workbook as w
    from src.design.detailing import DRAWING_POLICY_ID, make_fresh_detailing_engine
    from src.design.lookup_catalog import LookupCatalog
    engine=make_fresh_detailing_engine(DRAWING_POLICY_ID,candidate_lookup='auto')
    payload={'metadata': {'schema_version':w.PAYLOAD_SCHEMA_VERSION, 'detailing_policy_id':DRAWING_POLICY_ID,
        'detailing_policy_revision_id':engine.detailing_policy_revision_id,
        'detailing_policy_parameters':engine.detailing_policy_parameters, 'ranking_mode':engine.ranking_mode},
        'raw_rows':[], 'review_rows':[], 'final_rows':[]}
    seen={}
    def dispatch(inputs, contexts, active, *, workers, on_line_complete=None):
        seen['engine']=active
        return [], [], {}, {}, 1
    monkeypatch.setattr(w,'_dispatch_beam_line_work',dispatch)
    result=w.redetail_payload(payload,candidate_lookup_output_dir=tmp_path)
    assert isinstance(seen['engine'].candidate_lookup_provider,LookupCatalog)
    assert seen['engine'].candidate_lookup_output_dir == tmp_path
    assert result['metadata']['detailing_policy_revision_id'] == engine.detailing_policy_revision_id
    for policy in ('legacy',):
        assert w._resolve_detailing_engine(None,policy).candidate_lookup_provider is None
    from src.design.offline_style import DRAWING_POLICY_REVISION_R8_ID
    assert w._resolve_detailing_engine(None,DRAWING_POLICY_ID,
        detailing_policy_revision_id=DRAWING_POLICY_REVISION_R8_ID).candidate_lookup_provider is None
def test_explicit_lookup_off_does_not_silently_replay_recorded_r9(tmp_path):
    import argparse, json
    from src import cli
    from src.design.offline_style import DRAWING_POLICY_REVISION_R9_ID
    source=tmp_path/'r9.json'
    source.write_text(json.dumps({'metadata':{'detailing_policy_revision_id':DRAWING_POLICY_REVISION_R9_ID}}),encoding='utf8')
    args=argparse.Namespace(replay_payload=source,output_dir=tmp_path/'out',config=None,
        detailing_policy=None,allow_policy_revision_change=False,layout_dictionary=None,
        rebar_workers=None,candidate_lookup='off')
    with pytest.raises(ValueError,match='requires --detailing-policy and --allow-policy-revision-change'):
        cli._replay_payload(args)
