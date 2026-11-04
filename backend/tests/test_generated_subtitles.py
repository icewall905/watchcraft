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


def seed_feed(tmp_path, count=1):
    from bt.models import Candidate, Category, Download, Video
    with session() as s:
        category=Category(profile_id=1, kind='shorts', name='Saved reels')
        s.add(category); s.flush()
        cid=category.id
        for n in range(count):
            vid=f'reeltest{n:03}'
            path=tmp_path/f'{vid}.mp4';path.write_bytes(b'local media')
            s.add(Video(id=vid,is_short=True,duration_s=30))
            s.add(Download(video_id=vid,status='done',path=str(path)))
            s.add(Candidate(video_id=vid,category_id=cid,status='accepted',score=80))
        s.commit()
    return cid


def test_feed_backfill_is_bounded_durable_and_never_retries_failures(tmp_path,monkeypatch):
    seed_feed(tmp_path,8)
    monkeypatch.setattr(jobs,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    assert jobs.backfill()==4
    assert jobs.backfill()==0
    with session() as s:
        rows=s.exec(select(GeneratedSubtitles)).all()
        assert len(rows)==4 and all(r.local_only and r.requested_by==1 for r in rows)
        rows[0].status='failed';rows[0].error='STT offline';s.add(rows[0]);s.commit()
    assert jobs.backfill()==1
    with session() as s:
        failed=s.exec(select(GeneratedSubtitles).where(GeneratedSubtitles.status=='failed')).one()
        assert failed.error=='STT offline'
    # Explicit retry remains possible, and can restore a missing offline source.
    with session() as s:
        queued=s.exec(select(GeneratedSubtitles).where(GeneratedSubtitles.status=='queued')).first()
        queued.status='ready';queued.vtt='WEBVTT';s.add(queued);s.commit()
    jobs.enqueue(failed.video_id,1)
    with session() as s:assert s.get(GeneratedSubtitles,failed.video_id).local_only


def test_backfill_skips_ineligible_and_unreadable_clips(tmp_path,monkeypatch):
    from bt.models import Candidate, Category, Download, Video, WatchHistory
    cid=seed_feed(tmp_path,6)
    monkeypatch.setattr(jobs,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    with session() as s:
        cs=s.exec(select(Candidate).order_by(Candidate.id)).all()
        cs[0].dismissed=True;s.add(cs[0])
        cs[1].status='rejected';s.add(cs[1])
        v=s.get(Video,cs[2].video_id);v.is_short=False;s.add(v)
        s.add(WatchHistory(profile_id=1,video_id=cs[3].video_id,finished=True))
        d=s.get(Download,cs[4].video_id);d.path=str(tmp_path/'gone');s.add(d)
        s.commit()
    assert jobs.backfill()==1
    with session() as s:
        c=s.get(Category,cid);c.enabled=False;s.add(c);s.commit()
    assert jobs.backfill()==0


def test_automatic_job_cannot_fall_back_to_remote_when_saved_copy_disappears(tmp_path,monkeypatch):
    import threading
    monkeypatch.setattr(jobs.ytdl,'video_info',lambda *a,**kw:pytest.fail('Automatic job fetched remote media'))
    with pytest.raises(jobs.SubtitleError,match='no longer available'):
        jobs._source(ID,tmp_path,lambda:None,threading.Event(),local_only=True)


def test_feed_embeds_cached_caption_status_and_keeps_pin_protection(tmp_path,monkeypatch):
    from bt.api import core
    from bt.profile_pins import set_pin
    cid=seed_feed(tmp_path)
    monkeypatch.setattr(core,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    with session() as s:
        s.add(GeneratedSubtitles(video_id='reeltest000',status='ready',vtt='WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n',language='en'))
        s.commit()
    client=TestClient(app)
    video=client.get(f'/api/shorts?category_id={cid}').json()['items'][0]['video']
    assert video['subtitles']['track']['url']=='/api/video/reeltest000/subtitles/generated.vtt'
    with session() as s:set_pin(s,1,'1234');s.commit()
    assert client.get(f'/api/shorts?category_id={cid}').status_code==403


def test_on_demand_local_clip_precedes_backfill_and_uses_reserved_slot(tmp_path,monkeypatch):
    seed_feed(tmp_path,8)
    monkeypatch.setattr(jobs,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    assert jobs.backfill()==4
    jobs.enqueue('reeltest007',1,local_only=True)
    assert jobs.claim()[0]=='reeltest007'
    with session() as s:assert s.get(GeneratedSubtitles,'reeltest007').local_only


def test_invalid_ready_track_is_reported_failed_and_can_be_regenerated():
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status='ready',vtt='WEBVTT\n'))
        s.commit()
    assert jobs.status(ID)['status']=='failed' and jobs.status(ID)['track'] is None
    client=TestClient(app)
    assert client.get(f'/api/video/{ID}/subtitles/generated.vtt').status_code==404
    response=client.post(f'/api/video/{ID}/subtitles',json={'local_only':True})
    assert response.json()['status']=='queued'
    with session() as s:assert s.get(GeneratedSubtitles,ID).local_only


def test_feed_counts_caption_readiness_separately_from_saved_media(tmp_path,monkeypatch):
    from bt.api import core
    seed_feed(tmp_path,4)
    monkeypatch.setattr(core,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    with session() as s:
        s.add(GeneratedSubtitles(video_id='reeltest000',status='ready',vtt='WEBVTT\n\n00:00.000 --> 00:01.000\nWords\n'))
        s.add(GeneratedSubtitles(video_id='reeltest001',status='ready',vtt='WEBVTT\n'))
        s.add(GeneratedSubtitles(video_id='reeltest002',status='running'))
        s.commit()
    feed=TestClient(app).get('/api/shorts/feeds').json()[0]
    assert feed['ready_count']==4
    assert feed['caption_ready_count']==1 and feed['caption_pending_count']==2 and feed['caption_error_count']==1


def test_backfill_skips_long_or_unknown_duration_and_respects_disabled_stt(tmp_path,monkeypatch):
    from bt.models import Video
    seed_feed(tmp_path,2)
    monkeypatch.setattr(jobs,'abs_path',lambda relative:__import__('pathlib').Path(relative))
    with session() as s:
        for vid,duration in [('reeltest000',0),('reeltest001',181)]:
            row=s.get(Video,vid);row.duration_s=duration;s.add(row)
        s.commit()
    assert jobs.backfill()==0
    save_setting('stt',{**CFG,'subtitles_enabled':False})
    assert jobs.backfill()==0


def test_local_only_migration_preserves_existing_tracks_and_is_repeatable():
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status='ready',vtt='WEBVTT\n\n00:00.000 --> 00:01.000\nOld words\n'))
        s.commit()
    with db.engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE generatedsubtitles DROP COLUMN local_only')
        connection.exec_driver_sql('ALTER TABLE generatedsubtitles DROP COLUMN retry_count')
        connection.exec_driver_sql('ALTER TABLE generatedsubtitles DROP COLUMN retry_at')
    init_db();init_db()
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert not row.local_only and row.status=='ready' and 'Old words' in row.vtt


@pytest.mark.parametrize('state',['failed','queued','running','ready'])
def test_local_only_requirement_is_monotonic_on_every_retry_path(state):
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status=state,local_only=True,vtt='WEBVTT\n'))
        s.commit()
    jobs.enqueue(ID,1)
    with session() as s:assert s.get(GeneratedSubtitles,ID).local_only


def test_existing_running_job_can_be_upgraded_to_local_only():
    jobs.enqueue(ID,1)
    video,token=jobs.claim()
    jobs.enqueue(ID,1,local_only=True)
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert row.status=='running' and row.token==token and row.local_only


def test_source_rechecks_upgraded_local_only_after_run_snapshot(tmp_path,monkeypatch):
    import threading
    from bt.models import Download
    jobs.enqueue(ID,1)
    jobs.claim()
    class Disappeared:
        def is_file(self):
            jobs.enqueue(ID,1,local_only=True)
            return False
    with session() as s:s.add(Download(video_id=ID,status='done',path='lost.mp4'));s.commit()
    monkeypatch.setattr(jobs,'abs_path',lambda relative:Disappeared())
    monkeypatch.setattr(jobs.ytdl,'video_info',lambda *a,**kw:pytest.fail('Upgraded job fetched remote media'))
    with pytest.raises(jobs.SubtitleError,match='no longer available'):
        jobs._source(ID,tmp_path,lambda:None,threading.Event(),local_only=False)


@pytest.mark.parametrize('vtt',[
    'WEBVTTBAD\n\n00:00.000 --> 00:01.000\nWords',
    'WEBVTT\n\n00:00.000 --> 00:01.000\nWords\n\nbad --> bad\nMore',
    'WEBVTT\n\n00:02.000 --> 00:01.000\nWords',
    'WEBVTT\n\n00:00.000 --> 00:01.000\n<b> </b>',
    'WEBVTT\n\n00:00,000 --> 00:01,000\nWords',
])
def test_ready_validation_rejects_every_malformed_cue(vtt):
    assert not jobs.usable_vtt(vtt)


def test_explicit_regenerate_discards_client_rejected_cached_track():
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status='ready',local_only=True,
            vtt='WEBVTT\n\n00:00.000 --> 00:01.000\nWords',
            cues=[{'start':0,'end':1,'text':'Words'}],completed_s=60,progress=100))
        s.commit()
    response=TestClient(app).post(f'/api/video/{ID}/subtitles',json={'local_only':True,'regenerate':True})
    assert response.json()['status']=='queued'
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert row.local_only and not row.cues and not row.vtt and row.completed_s==0


def test_transient_service_failures_retry_with_bounded_durable_backoff(source,monkeypatch):
    calls=[]
    def transcribe(*a):
        calls.append(True)
        if len(calls)==1:return 'Completed speech'
        raise jobs.TransientSubtitleError('The STT service returned HTTP 503.')
    monkeypatch.setattr(jobs,'transcribe_chunk',transcribe)
    jobs.enqueue(ID,1)
    for attempt,delay in enumerate((60,300,900,None),start=1):
        before=time.time()
        jobs.run(*jobs.claim())
        with session() as s:
            row=s.get(GeneratedSubtitles,ID)
            assert row.completed_s==12 and len(row.cues)==1
            if delay is None:
                assert row.status=='failed' and row.retry_count==3
                break
            assert row.status=='queued' and row.retry_count==attempt
            assert before+delay<=row.retry_at<=time.time()+delay
        assert jobs.claim() is None  # restarting a daemon cannot bypass retry delay
        with session() as s:
            row=s.get(GeneratedSubtitles,ID);row.retry_at=0;s.add(row);s.commit()


def test_viewing_request_bypasses_transient_retry_delay_without_losing_chunks():
    with session() as s:
        s.add(GeneratedSubtitles(video_id=ID,status='queued',local_only=True,
            retry_at=time.time()+900,retry_count=3,completed_s=6,
            cues=[{'start':0,'end':6,'text':'Saved speech'}]))
        s.commit()
    assert jobs.claim() is None
    jobs.enqueue(ID,1,local_only=True)
    assert jobs.claim()[0]==ID
    with session() as s:
        row=s.get(GeneratedSubtitles,ID)
        assert row.retry_at==0 and row.retry_count==0 and row.completed_s==6 and row.cues


def test_graceful_stop_returns_unfinished_job_to_queue(source,monkeypatch):
    import threading
    stop=threading.Event()
    def shutdown(*a):
        stop.set()
        raise jobs.SubtitleError('Subtitle generation stopped; retry to continue.')
    monkeypatch.setattr(jobs,'transcribe_chunk',shutdown)
    jobs.enqueue(ID,1)
    jobs.run(*jobs.claim(),stop)
    assert jobs.status(ID)['status']=='queued'
    assert jobs.claim()[0]==ID
