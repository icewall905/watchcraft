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


def test_board_wants_ai():
    assert ai_detect.board_wants_ai('Prompts', 'midjourney landscapes')
    assert ai_detect.board_wants_ai('', '', 'only AI art please')
    assert not ai_detect.board_wants_ai('Garden', 'wildflowers', 'real photos, no AI art')
    assert not ai_detect.board_wants_ai('Garden', 'wildflowers', 'avoid midjourney or dall-e stuff')
    assert not ai_detect.board_wants_ai('Garden', 'wildflowers')


def run(client, monkeypatch, data, **kw):
    b = make(client, **kw)
    monkeypatch.setattr(moodboard_sources, 'search', lambda query, **k: [candidate()])
    monkeypatch.setattr(mb, 'capture_image', lambda url: (data, 'image/png', 20, 30))
    assert mb.dispatch_due()
    with session() as s:
        return s.exec(select(MoodPin).where(MoodPin.board_id == b['id'])).all()


def test_curator_skips_declared_ai(client, monkeypatch):
    assert run(client, monkeypatch, png(parameters='Negative prompt: blurry')) == []


def test_curator_keeps_ai_on_ai_board(client, monkeypatch):
    assert len(run(client, monkeypatch, png(parameters='Negative prompt: blurry'), interests='midjourney art')) == 1


def test_curator_keeps_plain_image(client, monkeypatch):
    assert len(run(client, monkeypatch, png(Title='Holiday'))) == 1
