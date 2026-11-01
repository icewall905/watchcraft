"""Generated captions are bounded, durable and independent of YouTube captions."""
import asyncio
import io
import math
import shutil
import time
import wave
from array import array

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select

from bt import db, subtitle_jobs as jobs
from bt.db import init_db, session, save_setting
from bt.main import app
from bt.models import GeneratedSubtitles

ID = 'subvoice001'
REAL_WORKER = jobs.worker
CFG = dict(base_url='http://stt:8000/v1', model='qwen', api_key='secret', language='en', subtitles_enabled=True)


def wav_file(path, seconds=26, silent=False):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(jobs.RATE)
        block = array('h', [0 if silent else int(5000*math.sin(2*math.pi*440*n/jobs.RATE)) for n in range(jobs.RATE)])
        for _ in range(seconds): w.writeframes(block.tobytes())


@pytest.fixture(autouse=True)
def database(monkeypatch, tmp_path):
    with db.engine.begin() as c: c.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine); init_db()
    save_setting('stt', CFG)
    monkeypatch.setattr(jobs, 'DATA_DIR', tmp_path)
    yield
    SQLModel.metadata.drop_all(db.engine)


@pytest.fixture
def source(monkeypatch, tmp_path):
    path = tmp_path / 'speech.wav'; wav_file(path)
    monkeypatch.setattr(jobs, '_source', lambda *a: path)
    def normalize(src, target, beat, stop):
        shutil.copyfile(src, target)
        with wave.open(str(target), 'rb') as w: return w.getnframes()/jobs.RATE
    monkeypatch.setattr(jobs, 'normalize', normalize)
    return path


def test_queue_deduplicates_limits_work_and_requires_configuration():
    with TestClient(app) as client:
        assert client.post('/api/video/bad/subtitles').status_code == 400
        assert client.get(f'/api/video/{ID}/subtitles').json()['status'] == 'idle'
        for _ in range(2): assert client.post(f'/api/video/{ID}/subtitles').status_code == 200
        with session() as s: assert len(s.exec(select(GeneratedSubtitles)).all()) == 1
        for n in range(4): jobs.enqueue(f'subqueue{n:03}', 1)
        assert client.post('/api/video/subvoice999/subtitles').status_code == 429
        save_setting('stt', {**CFG, 'subtitles_enabled': False})
        assert client.post('/api/video/different01/subtitles').status_code == 503
        assert client.get(f'/api/video/{ID}/subtitles/generated.vtt').status_code == 404


def test_queue_controls_require_an_unlocked_profile():
    from bt.profile_pins import set_pin
    with session() as s: set_pin(s, 1, '1234'); s.commit()
    client = TestClient(app)
    assert client.get(f'/api/video/{ID}/subtitles').status_code == 403
    assert client.post(f'/api/video/{ID}/subtitles').status_code == 403


def test_lease_is_serial_and_stale_worker_cannot_overwrite_new_claim():
    jobs.enqueue(ID, 1); jobs.enqueue('subvoice002', 1)
    vid, token = jobs.claim()
    assert jobs.claim() is None
    with session() as s:
        row=s.get(GeneratedSubtitles, vid); row.lease_until=time.time()-1; s.add(row);s.commit()
    _, next_token = jobs.claim()
    assert next_token != token
    with pytest.raises(jobs.SubtitleError): jobs._update(vid, token, progress=90)


def test_generated_track_survives_reload_and_stt_being_disabled(source, monkeypatch):
    jobs.enqueue(ID, 1)
    monkeypatch.setattr(jobs, 'transcribe_chunk', lambda *a: 'A clearer alternative transcript.')
    jobs.run(*jobs.claim())
    assert jobs.status(ID)['status'] == 'ready'
    init_db()
    save_setting('stt', {**CFG, 'base_url': '', 'subtitles_enabled': False})
    client=TestClient(app)
    status=client.get(f'/api/video/{ID}/subtitles').json()
    assert status['track']['key']=='generated' and not status['enabled']
    assert status['timing_approximate'] and status['progress']==100
    response=client.get(status['track']['url'])
    assert response.status_code==200 and response.headers['content-type'].startswith('text/vtt')
    assert 'clearer alternative' in response.text and 'secret' not in response.text
    assert client.post(f'/api/video/{ID}/subtitles').json()['status']=='ready'


def test_failed_job_retries_only_unfinished_chunks(source, monkeypatch):
    count=[]
    def transcribe(*a):
        count.append(1)
        if len(count)==2: raise jobs.SubtitleError('STT temporarily unavailable')
        return 'First chunk'
    monkeypatch.setattr(jobs,'transcribe_chunk',transcribe)
    jobs.enqueue(ID,1); jobs.run(*jobs.claim())
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert row.status=='failed' and row.completed_s==12 and len(row.cues)==1
    calls=[]
    monkeypatch.setattr(jobs,'transcribe_chunk',lambda *a:calls.append(1) or 'Remaining chunk')
    jobs.enqueue(ID,1);jobs.run(*jobs.claim())
    assert jobs.status(ID)['status']=='ready' and len(calls)==2
    with session() as s: assert len(s.get(GeneratedSubtitles,ID).cues)==3


def test_changed_model_restarts_partial_track_instead_of_mixing(source,monkeypatch):
    calls=[]
    def fail(*a):
        calls.append(1)
        if len(calls)>1: raise jobs.SubtitleError('offline')
        return 'Old model words'
    monkeypatch.setattr(jobs,'transcribe_chunk',fail)
    jobs.enqueue(ID,1);jobs.run(*jobs.claim())
    save_setting('stt',{**CFG,'model':'other'})
    monkeypatch.setattr(jobs,'transcribe_chunk',lambda *a:'New model words')
    jobs.enqueue(ID,1);jobs.run(*jobs.claim())
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert row.status=='ready' and 'Old model' not in row.vtt and len(row.cues)==3


def test_silence_is_not_sent_to_stt_and_retry_can_reprocess(source,monkeypatch):
    wav_file(source,seconds=26,silent=True)
    monkeypatch.setattr(jobs,'transcribe_chunk',lambda *a:pytest.fail('Silence must not reach STT'))
    jobs.enqueue(ID,1);jobs.run(*jobs.claim())
    assert jobs.status(ID)['status']=='failed' and 'No speech' in jobs.status(ID)['error']
    jobs.enqueue(ID,1)
    with session() as s: assert s.get(GeneratedSubtitles,ID).completed_s==0


def test_actual_audio_normalization_and_pause_boundaries(tmp_path):
    import threading
    src=tmp_path/'input.wav';wav_file(src,seconds=15)
    assert jobs.normalize(src,tmp_path/'out.wav',lambda:None,threading.Event())==15
    with wave.open(str(tmp_path/'out.wav'),'rb') as w:
        end,audio,start,finish=jobs.audio_chunk(w,0)
        assert end==12 and start==0 and finish==12
        with wave.open(io.BytesIO(audio),'rb') as chunk: assert chunk.getnframes()==12*jobs.RATE


def test_stt_errors_are_readable_and_do_not_leak_service_secrets(monkeypatch):
    import httpx
    original=httpx.AsyncClient
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(401,text='secret private server details')
    monkeypatch.setattr(jobs.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    with pytest.raises(jobs.SubtitleError) as error:jobs.transcribe_chunk(b'wav',CFG)
    assert 'HTTP 401' in str(error.value) and 'secret' not in str(error.value)
    assert seen[0].headers['authorization']=='Bearer secret'


def test_worker_recovers_after_a_transient_queue_error(monkeypatch):
    calls=[]
    def claim():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('database temporarily busy')
        raise asyncio.CancelledError()
    monkeypatch.setattr(jobs,'claim',claim)
    async def immediate(*a):pass
    monkeypatch.setattr(jobs.asyncio,'sleep',immediate)
    with pytest.raises(asyncio.CancelledError):asyncio.run(REAL_WORKER())
    assert len(calls)==2


def test_long_live_or_unknown_remote_audio_is_rejected_before_download(monkeypatch,tmp_path):
    import threading
    for info in ({'is_live':True}, {'duration':jobs.MAX_SECONDS+1}, {'duration':None}):
        monkeypatch.setattr(jobs.ytdl,'video_info',lambda *a,info=info,**kw:info)
        with pytest.raises(jobs.SubtitleError): jobs._source(ID,tmp_path,lambda:None,threading.Event())


def test_temp_cleanup_keeps_live_work_and_unrelated_files(tmp_path):
    root=tmp_path/'subtitle-tmp';root.mkdir()
    old=root/(ID+'-'+'a'*32+'-orphan');old.mkdir()
    active=root/(ID+'-'+'b'*32+'-active');active.mkdir()
    unrelated=root/'do-not-remove';unrelated.mkdir()
    link=root/(ID+'-'+'c'*32+'-link');link.symlink_to(unrelated,target_is_directory=True)
    import os
    for d in (old,active):os.utime(d,(time.time()-1000,time.time()-1000))
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status='running',token='b'*32,lease_until=time.time()+100));s.commit()
    jobs.cleanup_temp()
    assert not old.exists() and active.exists() and unrelated.exists() and link.is_symlink()


def test_total_stt_deadline_bounds_a_slow_stream(monkeypatch):
    import httpx
    original=httpx.AsyncClient
    timeout=asyncio.timeout
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(.1)
            yield b'{"text":"late"}'
    monkeypatch.setattr(jobs.asyncio,'timeout',lambda seconds:timeout(.01))
    monkeypatch.setattr(jobs.httpx,'AsyncClient',lambda **kw:original(
        transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=Slow())),**kw))
    with pytest.raises(jobs.SubtitleError,match='took too long'):jobs.transcribe_chunk(b'wav',CFG)


def test_saved_audio_avoids_youtube_fetches(monkeypatch,tmp_path):
    import threading
    from bt.models import Download
    saved=tmp_path/'saved.mp4';saved.write_bytes(b'owned local media')
    with session() as s:s.add(Download(video_id=ID,status='done',path='saved.mp4'));s.commit()
    monkeypatch.setattr(jobs,'abs_path',lambda path:saved)
    monkeypatch.setattr(jobs.ytdl,'video_info',lambda *a,**kw:pytest.fail('Offline source must not fetch YouTube'))
    assert jobs._source(ID,tmp_path,lambda:None,threading.Event())==saved
