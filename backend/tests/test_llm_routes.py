import ast
import pathlib

from test_curation import client  # noqa: F401
from bt import llm
from bt.db import get_settings, save_setting

BT = pathlib.Path(llm.__file__).parent


def set_llm(**over):
    save_setting('llm', {**get_settings()['llm'], **over})


def test_defaults_send_grunt_work_to_worker_and_judgement_to_judge(client):
    assert llm.role_for('board_sources') == 'worker'
    assert llm.role_for('video_plan') == 'worker'
    assert llm.role_for('frontpage_review') == 'judge'
    assert llm.role_for('knowledge_chat') == 'judge'
    assert {k for k, (_s, _l, role) in llm.TASKS.items() if role == 'judge'} == {
        'video_verdict', 'memory_audit', 'knowledge_review', 'frontpage_review', 'knowledge_chat'}


def test_routes_pick_the_endpoint(client):
    set_llm(base_url='http://judge:8080/v1', model='big', worker_base_url='http://worker:8080/v1', worker_model='small',
            routes={'video_plan': 'judge', 'knowledge_chat': 'worker', 'bogus': 'worker', 'music_dj': 'nonsense'})
    assert llm._cfg('video_plan')['model'] == 'big'
    assert llm._cfg('knowledge_chat')['model'] == 'small'
    assert llm._cfg('music_dj')['model'] == 'small'  # invalid stored value falls back to the default
    assert llm._cfg('frontpage_review')['base_url'] == 'http://judge:8080/v1'
    assert llm._cfg('worker')['model'] == 'small' and llm._cfg('judge')['model'] == 'big'


def test_settings_api_lists_tasks_and_validates_routes(client):
    tasks = client.get('/api/agent/llm-routes').json()['tasks']
    assert {t['key'] for t in tasks} == set(llm.TASKS)
    assert next(t for t in tasks if t['key'] == 'frontpage_review')['default'] == 'judge'
    cur = client.get('/api/settings').json()['llm']
    assert client.put('/api/settings/llm', json={**cur, 'routes': {'nope': 'worker'}}).status_code == 400
    assert client.put('/api/settings/llm', json={**cur, 'routes': {'video_plan': 'gpu'}}).status_code == 400
    r = client.put('/api/settings/llm', json={**cur, 'routes': {'video_plan': 'judge'}})
    assert r.status_code == 200 and r.json()['routes'] == {'video_plan': 'judge'}
    assert llm.role_for('video_plan') == 'judge'


def test_every_model_call_names_a_routed_task():
    """A new LLM call must pick a TASKS key, so it shows up in the routing settings."""
    missing = []
    for path in sorted(BT.rglob('*.py')):
        if path.name == 'llm.py':
            continue
        rel = path.relative_to(BT).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, (ast.Attribute, ast.Name)):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id
            base = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ''
            is_llm = name in ('chat', 'chat_json') and base.endswith('llm')
            is_ai = name == '_call' and (base in ('ai', 'hub_ai') or (base == '' and rel == 'hub/ai.py'))
            if not (is_llm or is_ai) or (rel == 'hub/ai.py' and node.lineno < 60 and base.endswith('llm')):
                continue  # hub/ai._call itself forwards its caller's task
            role = next((k.value for k in node.keywords if k.arg == 'role'), None)
            if not isinstance(role, ast.Constant) or role.value not in llm.TASKS:
                missing.append(f'{rel}:{node.lineno}')
    assert missing == []
