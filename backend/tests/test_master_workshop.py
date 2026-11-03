"""Master curator visibility and safe starter defaults."""
from test_curation import client  # noqa: F401
from bt.db import session
from bt.hub.models import MasterQueue
from bt.hub.templates import TEMPLATES


def test_team_master_queue_is_profile_scoped(client):
    other = client.post('/api/profiles', json={'name': 'Other reader'}).json()['id']
    with session() as s:
        s.add(MasterQueue(profile_id=other, due_at=123, first_at=100))
        s.commit()
    mine = next(a for a in client.get('/api/hub/team').json()['agents'] if a['kind'] == 'master')
    theirs = next(a for a in client.get('/api/hub/team', headers={'X-Profile-Id': str(other)}).json()['agents'] if a['kind'] == 'master')
    assert mine['status'] == 'idle' and mine['next_run'] is None
    assert theirs['status'] == 'waiting' and theirs['next_run'] == 123


def test_templates_offer_keywords_and_bounded_meme_intake():
    templates = {t['key']: t for t in TEMPLATES}
    assert templates['news']['keywords']
    assert templates['meme-radar']['keywords']
    assert 1 <= templates['meme-radar']['max_per_run'] <= 20
