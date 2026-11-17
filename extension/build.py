"""Build the Hub browser extension for Chrome and Firefox.

    backend/.venv/bin/python extension/build.py

Writes frontend/static/extensions/the-hub-chrome.zip and the-hub-firefox.zip (served by the app at
/extensions/…). Both share src/; only the manifest differs (Chrome: service worker, Firefox: event page + id).
"""
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / 'frontend' / 'static' / 'extensions'
VERSION = '1.2.0'

BASE = {
    'manifest_version': 3,
    'name': 'The Hub',
    'version': VERSION,
    'description': 'Right-click any image to add it to one of your Hub mood boards.',
    'permissions': ['contextMenus', 'storage', 'activeTab', 'scripting', 'notifications', 'alarms'],
    # Only the user's own Hub address is requested, at setup time.
    'optional_host_permissions': ['https://*/*', 'http://*/*'],
    'action': {'default_title': 'The Hub settings', 'default_icon': {'16': 'icons/icon-16.png', '32': 'icons/icon-32.png'}},
    'options_ui': {'page': 'options.html', 'open_in_tab': True},
    'icons': {str(s): f'icons/icon-{s}.png' for s in (16, 32, 48, 128)},
}
CHROME = {**BASE, 'background': {'service_worker': 'background.js'}, 'minimum_chrome_version': '116'}
FIREFOX = {**BASE, 'background': {'scripts': ['background.js']},
           'browser_specific_settings': {'gecko': {'id': 'watchcraft-moodboards@watchcraft.local', 'strict_min_version': '128.0'}}}


def icon(size):
    scale = 4
    img = Image.new('RGBA', (size * scale, size * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size * scale
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 4, fill=(255, 143, 171, 255))
    d.rounded_rectangle((s * .08, s * .08, s * .92, s * .92), radius=s // 5, fill=(255, 181, 71, 255))
    # Bookmark/pin mark (matches the Mood Board nav icon).
    w = s * .22
    d.polygon([(s / 2 - w, s * .24), (s / 2 + w, s * .24), (s / 2 + w, s * .78), (s / 2, s * .64), (s / 2 - w, s * .78)], fill=(27, 16, 32, 255))
    out = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(out, 'PNG')
    return out.getvalue()


def build(name, manifest):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'the-hub-{name}.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        def add(arc, data):
            info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))  # reproducible zips
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        add('manifest.json', json.dumps(manifest, indent=2))
        for f in sorted((ROOT / 'src').iterdir()):
            add(f.name, f.read_bytes())
        for size in (16, 32, 48, 128):
            add(f'icons/icon-{size}.png', icon(size))
    print(path, path.stat().st_size, 'bytes')


if __name__ == '__main__':
    build('chrome', CHROME)
    build('firefox', FIREFOX)
