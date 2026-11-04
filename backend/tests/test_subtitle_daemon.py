"""The caption daemon remains observable and stops in-flight transcription safely."""
import asyncio
import os
import signal
import threading
import time

import httpx
import pytest

from bt import db, subtitle_jobs as jobs


def test_healthcheck_requires_live_pid_and_recent_heartbeat(tmp_path):
    path=tmp_path/'heartbeat'
    assert not jobs.healthy(path)
    path.write_text(str(os.getpid()))
    assert jobs.healthy(path)
    os.utime(path,(time.time()-91,time.time()-91))
    assert not jobs.healthy(path)
    path.write_text('not a pid')
    assert not jobs.healthy(path)
    path.write_text('99999999')
    assert not jobs.healthy(path)


def test_daemon_signal_stops_work_and_removes_heartbeat(tmp_path,monkeypatch):
    monkeypatch.setattr(jobs,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'init_db',lambda:None)
    async def check():
        loop=asyncio.get_running_loop()
        handlers={}
        monkeypatch.setattr(loop,'add_signal_handler',lambda signum,callback:handlers.__setitem__(signum,callback))
        monkeypatch.setattr(loop,'remove_signal_handler',lambda signum:handlers.pop(signum))
        async def work(stop):
            # The heartbeat runs while the daemon is busy, independently of jobs.
            await asyncio.sleep(.02)
            assert jobs.healthy(tmp_path/jobs.HEARTBEAT_NAME)
            handlers[signal.SIGTERM]()
            assert stop.is_set()
        monkeypatch.setattr(jobs,'worker',work)
        await jobs.daemon()
        assert not handlers and not (tmp_path/jobs.HEARTBEAT_NAME).exists()
    asyncio.run(check())


def test_shutdown_interrupts_stt_request_without_waiting_for_remote_timeout(monkeypatch):
    original=httpx.AsyncClient
    cancelled=[]
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                await asyncio.sleep(20)
                yield b'{"text":"late"}'
            finally:
                cancelled.append(True)
    monkeypatch.setattr(jobs.httpx,'AsyncClient',lambda **kw:original(
        transport=httpx.MockTransport(lambda req:httpx.Response(200,stream=Slow())),**kw))
    stop=threading.Event()
    timer=threading.Timer(.05,stop.set);timer.start()
    started=time.monotonic()
    try:
        with pytest.raises(jobs.SubtitleError,match='stopped'):
            jobs.transcribe_chunk(b'wav',{'base_url':'http://stt/v1','model':'test'},stop)
    finally:
        timer.join()
    assert time.monotonic()-started<2 and cancelled


def test_api_can_disable_embedded_caption_worker(monkeypatch):
    from bt import main
    monkeypatch.setenv('BT_SUBTITLE_WORKER','0')
    monkeypatch.setattr(main,'init_db',lambda:None)
    async def unexpected():
        pytest.fail('Dedicated daemon mode must not also launch the API caption worker')
    monkeypatch.setattr(jobs,'worker',unexpected)
    async def check():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
    asyncio.run(check())


def test_compose_runs_one_dedicated_caption_service_with_read_only_media():
    from pathlib import Path
    import yaml
    class Loader(yaml.SafeLoader):pass
    Loader.add_constructor('!override',lambda loader,node:loader.construct_sequence(node))
    root=Path(__file__).resolve().parents[2]
    base=yaml.load((root/'compose.yaml').read_text(),Loader=Loader)['services']
    wc=yaml.load((root/'compose.watchcraft.yaml').read_text(),Loader=Loader)['services']
    service=base['bt-subtitles']
    assert base['bt-api']['environment']['BT_SUBTITLE_WORKER']=='0'
    assert service['command']==['python','-m','bt.subtitle_jobs']
    assert service['healthcheck']['test']==['CMD','python','-m','bt.subtitle_jobs','--healthcheck']
    assert service['restart']=='unless-stopped' and service['stop_grace_period']=='30s'
    assert service['volumes']==['${BT_DATA_HOST_DIR:-./data}:/data','${BT_MEDIA_HOST_DIR:-./media}:/media:ro']
    assert wc['bt-subtitles']['image']=='watchcraft:latest'
    assert wc['bt-subtitles']['container_name']=='wc-subtitles'
    assert wc['bt-subtitles']['environment']==wc['bt-api']['environment']
