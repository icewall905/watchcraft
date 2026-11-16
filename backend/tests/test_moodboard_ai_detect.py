from io import BytesIO

from PIL import Image, PngImagePlugin
from sqlmodel import select

from test_moodboards import client, make, candidate, model_stub  # noqa: F401
from bt.db import session
from bt.hub import ai_detect, moodboards as mb, moodboard_sources
from bt.hub.models import MoodPin


def png(**text):
    info = PngImagePlugin.PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    out = BytesIO()
    Image.new('RGB', (8, 8)).save(out, format='PNG', pnginfo=info)
    return out.getvalue()


def jpeg(software=''):
    image, out = Image.new('RGB', (8, 8)), BytesIO()
    exif = image.getexif()
    if software:
        exif[0x0131] = software
    image.save(out, format='JPEG', exif=exif)
    return out.getvalue()


def test_detect_markers():
    assert ai_detect.detect(png(parameters='a cat, Steps: 20, Sampler: Euler'))
    assert ai_detect.detect(png(prompt='{"3": {"class_type": "KSampler"}}'))
    assert ai_detect.detect(jpeg('Midjourney v6'))
    assert ai_detect.detect(jpeg() + b'<Iptc4xmpExt:DigitalSourceType>http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia')
    assert not ai_detect.detect(png(Title='Holiday'))
    assert not ai_detect.detect(jpeg('Adobe Lightroom 7.0'))
    assert not ai_detect.detect(b'garbage')


def ai_png():
    return png(parameters='Negative prompt: blurry')


def run(client, monkeypatch, data, **kw):
    b = make(client, **kw)
    monkeypatch.setattr(moodboard_sources, 'search', lambda query, **k: [candidate()])
    monkeypatch.setattr(mb, 'capture_image', lambda url: (data, 'image/png', 20, 30))
    assert mb.dispatch_due()
    with session() as s:
        return s.exec(select(MoodPin).where(MoodPin.board_id == b['id'])).all()


def test_default_rejects_declared_ai(client, monkeypatch):
    assert run(client, monkeypatch, ai_png()) == []


def test_mark_keeps_and_flags_declared_ai(client, monkeypatch):
    pins = run(client, monkeypatch, ai_png(), ai_images='mark')
    assert len(pins) == 1 and pins[0].ai_flag.startswith('generator metadata')
    detail = client.get(f'/api/hub/boards/{pins[0].board_id}').json()
    assert detail['board']['ai_images'] == 'mark' and detail['pins'][0]['ai_flag']


def test_ignore_keeps_unflagged(client, monkeypatch):
    pins = run(client, monkeypatch, ai_png(), ai_images='ignore')
    assert len(pins) == 1 and pins[0].ai_flag == ''


def test_mark_uses_vision_verdict(client, monkeypatch):
    seen = {}
    def call(system, user, schema, **kw):
        seen['system'], seen['schema'] = system, schema
        return {'verdicts': [{'index': 0, 'seen': 'x', 'fits': True, 'why': 'y', 'ai_generated': True}]}
    monkeypatch.setattr(mb.ai, '_call', call)
    pins = run(client, monkeypatch, png(Title='Holiday'), ai_images='mark')
    assert pins[0].ai_flag == 'looks AI-generated'
    assert 'ai_generated' in seen['schema']['properties']['verdicts']['items']['required']
    assert 'Do not reject an image only for being AI-generated' in seen['system']


def test_ignore_has_no_ai_rule(client, monkeypatch):
    seen = {}
    def call(system, user, schema, **kw):
        seen['system'] = system
        return {'verdicts': [{'index': 0, 'seen': 'x', 'fits': True, 'why': 'y'}]}
    monkeypatch.setattr(mb.ai, '_call', call)
    run(client, monkeypatch, png(Title='Holiday'), ai_images='ignore')
    assert 'look AI-generated' not in seen['system'] and 'ai_generated' not in seen['system']


def test_edit_validates_mode(client):
    b = make(client)
    assert client.patch(f"/api/hub/boards/{b['id']}", json={'ai_images': 'maybe'}).status_code == 422
    assert client.patch(f"/api/hub/boards/{b['id']}", json={'ai_images': 'ignore'}).json()['board']['ai_images'] == 'ignore'


def test_plain_image_kept_by_default(client, monkeypatch):
    assert len(run(client, monkeypatch, png(Title='Holiday'))) == 1
