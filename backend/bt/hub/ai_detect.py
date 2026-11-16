"""Cheap provenance check for AI-generated images, run before any vision call.

Only reads markers generators and platforms embed on purpose (C2PA / IPTC digital source
type, generator parameters in PNG text chunks or EXIF). Stripped files pass; the vision
judge is the second line of defence. A hit is strong evidence, a miss proves nothing.
"""
import re
from io import BytesIO

from PIL import Image

# IPTC DigitalSourceType values for generated media (C2PA and XMP both use them).
_SOURCE_TYPES = (b'trainedAlgorithmicMedia', b'compositeWithTrainedAlgorithmicMedia', b'algorithmicMedia')
_GENERATORS = re.compile(
    r'stable[ _-]?diffusion|midjourney|dall[\s·-]?e|novelai|comfyui|automatic1111|invokeai|fooocus|'
    r'leonardo\.ai|ideogram|firefly|imagen|flux\.?1|sdxl|negative prompt:|"sampler_name"', re.I)
_PNG_KEYS = ('parameters', 'prompt', 'workflow', 'Dream', 'sd-metadata', 'invokeai_metadata')
# 0x0131 Software, 0x010E ImageDescription, 0x013B Artist, 0x9286 UserComment
_EXIF_TAGS = (0x0131, 0x010E, 0x013B)


def detect(data: bytes) -> str:
    """Return a short reason when the bytes declare AI generation, else ''."""
    if not data:
        return ''
    head = data[:1_000_000]
    for marker in _SOURCE_TYPES:
        if marker in head:
            return 'provenance: ' + marker.decode()
    try:
        with Image.open(BytesIO(data)) as image:
            info = image.info or {}
            for key in _PNG_KEYS:
                if key in info and str(info[key]).strip():
                    return f'generator metadata: {key}'
            texts = [str(v) for v in info.values() if isinstance(v, (str, bytes))]
            exif = image.getexif()
            texts += [str(exif.get(tag, '')) for tag in _EXIF_TAGS]
            texts.append(str(exif.get_ifd(0x8769).get(0x9286, '')))
    except Exception:
        return ''
    for text in texts:
        match = _GENERATORS.search(text[:4000])
        if match:
            return 'generator metadata: ' + match.group(0).strip().lower()
    return ''


_WANTS_AI = re.compile(r'\b(ai[ -]?(art|generated|images?|illustrations?)|midjourney|stable diffusion|dall[ -]?e|generative)\b', re.I)

_NEGATION = re.compile(r"\b(no|not|never|avoid|without|exclude|except|skip|don'?t)\b[\w\s,-]*$")


def board_wants_ai(*texts: str) -> bool:
    """Boards that ask for AI art keep it; every other board drops declared AI images."""
    for text in texts:
        for match in _WANTS_AI.finditer(text or ''):
            before = (text or '')[max(0, match.start() - 25):match.start()].lower()
            if not _NEGATION.search(before):
                return True
    return False
