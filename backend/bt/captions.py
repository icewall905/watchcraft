"""Convert rolling source captions into stable, two-line playback blocks."""
from __future__ import annotations

import html
import json
import re
import textwrap
from dataclasses import dataclass

LINE_WIDTH = 40
PAIR_GAP_MS = 1500
PAIR_SPAN_MS = 8000
_TIMING = re.compile(r'(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})')
_TAG = re.compile(r'<[^>]*>')


@dataclass
class Cue:
    start: int
    end: int
    text: str


def _plain(text: str) -> list[str]:
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    return [line for raw in html.unescape(_TAG.sub('', text)).splitlines()
            if (line := re.sub(r'\s+', ' ', raw).strip())]


def _json_cues(raw: str) -> list[Cue]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    active = None
    for event in data.get('events') or []:
        value = ''.join(segment.get('utf8', '') for segment in event.get('segs') or [])
        if not value:
            continue
        start = int(event.get('tStartMs') or 0)
        end = start + max(1, int(event.get('dDurationMs') or 0))
        if not event.get('aAppend'):
            if value.strip():
                out.append(Cue(start, end, value))
                active = out[-1]
            else:
                active = None
            continue
        # A newline advances the rolling window; subsequent word appends belong
        # to the new line, with that line's own start time.
        for n, part in enumerate(value.split('\n')):
            if n:
                active = None
            if not part:
                continue
            if active is not None:
                active.text += part
                active.end = max(active.end, end)
            elif part.strip():
                out.append(Cue(start, end, part))
                active = out[-1]
    return out


def _time(value: str) -> int:
    parts = value.replace(',', '.').split(':')
    seconds, millis = parts[-1].split('.')
    minutes = int(parts[-2])
    hours = int(parts[-3]) if len(parts) == 3 else 0
    return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(millis)


def _vtt_cues(raw: str) -> list[Cue]:
    out = []
    for block in re.split(r'\n\s*\n', raw.replace('\r\n', '\n').replace('\r', '\n')):
        lines = block.splitlines()
        if not lines or lines[0].lstrip('\ufeff').startswith(('NOTE', 'STYLE', 'REGION')):
            continue
        for n, line in enumerate(lines):
            match = _TIMING.match(line.strip())
            if match:
                start, end = map(_time, match.groups())
                if end > start:
                    out.append(Cue(start, end, '\n'.join(lines[n + 1:])))
                break
    return out


def _lines(cues: list[Cue], rolling: bool) -> list[Cue]:
    """Keep each spoken line once, including the final version of live revisions."""
    out: list[Cue] = []
    previous: list[str] = []
    previous_end = -1
    for cue in sorted(cues, key=lambda c: c.start):
        lines = _plain(cue.text)
        if not lines:
            continue
        nearby = rolling and cue.start <= previous_end + 500
        overlap = 0
        if nearby:
            for count in range(min(len(previous), len(lines)), 0, -1):
                old = previous[-count:]
                current = lines[:count]
                same_prefix = old[:-1] == current[:-1]
                revised = (current[-1].startswith(old[-1] + ' ') or
                           old[-1].startswith(current[-1] + ' '))
                if old == current or (same_prefix and revised):
                    overlap = count
                    if revised and out and current[-1].startswith(out[-1].text + ' '):
                        out[-1].text = current[-1]
                    break
        new = lines[overlap:]
        if not new and overlap and out:
            out[-1].end = max(out[-1].end, cue.end)
        # A source with several fresh lines gives them one shared time interval.
        # Divide that interval so long captions can advance without overlapping.
        weights = [max(1, len(line)) for line in new]
        total = sum(weights) or 1
        elapsed = 0
        for line, weight in zip(new, weights):
            start = cue.start + (cue.end - cue.start) * elapsed // total
            elapsed += weight
            end = cue.start + (cue.end - cue.start) * elapsed // total
            out.append(Cue(start, max(start + 1, end), line))
        previous, previous_end = lines, cue.end
    return out


def _stamp(ms: int) -> str:
    h, rest = divmod(max(0, ms), 3600000)
    m, rest = divmod(rest, 60000)
    s, milli = divmod(rest, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{milli:03d}'


def two_line_vtt(raw: str, fmt: str, *, rolling: bool = True) -> str:
    """Each cue is a complete block; its two lines enter and leave together."""
    logical = _lines(_json_cues(raw) if fmt == 'json3' else _vtt_cues(raw), rolling)
    lines = []
    for index, cue in enumerate(logical):
        end = min(cue.end, logical[index + 1].start) if index + 1 < len(logical) else cue.end
        end = max(cue.start + 1, end)
        wrapped = textwrap.wrap(cue.text, width=LINE_WIDTH, break_on_hyphens=False) or [cue.text]
        span = max(len(wrapped), end - cue.start)
        for n, line in enumerate(wrapped):
            lines.append(Cue(cue.start + span * n // len(wrapped),
                             cue.start + span * (n + 1) // len(wrapped), line))
    blocks: list[Cue] = []
    index = 0
    while index < len(lines):
        first = lines[index]
        block = Cue(first.start, first.end, first.text)
        if index + 1 < len(lines):
            second = lines[index + 1]
            if second.start - first.end <= PAIR_GAP_MS and second.start - first.start <= PAIR_SPAN_MS:
                block.end = max(first.end, second.end)
                block.text += '\n' + second.text
                index += 1
        blocks.append(block)
        index += 1
    output = ['WEBVTT', '']
    for index, block in enumerate(blocks):
        end = min(block.end, blocks[index + 1].start) if index + 1 < len(blocks) else block.end
        if end <= block.start:
            continue
        # Remove source positioning and word timestamps so only the full block updates.
        output.extend([f'{_stamp(block.start)} --> {_stamp(end)}',
                       html.escape(block.text, quote=False).replace('--&gt;', '-&gt;'), ''])
    return '\n'.join(output)
