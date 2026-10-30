import json

from bt.captions import two_line_vtt, _vtt_cues


def events(*rows):
    return json.dumps({'events': [{'tStartMs': start, 'dDurationMs': end - start, 'segs': [{'utf8': text}], **extra}
                                  for start, end, text, extra in rows]})


def cues(raw, fmt='json3'):
    return _vtt_cues(two_line_vtt(raw, fmt))


def test_rolling_lines_advance_together_without_overlap():
    raw = events((0, 5000, 'First line', {}), (2000, 7000, 'First line\nSecond line', {}),
                 (4000, 9000, 'Second line\nThird line', {}), (6000, 11000, 'Third line\nFourth line', {}))
    result = cues(raw)
    assert [(c.start, c.end, c.text) for c in result] == [
        (0, 4000, 'First line\nSecond line'), (4000, 11000, 'Third line\nFourth line')]


def test_appended_words_and_window_newlines_form_complete_lines():
    raw = events((0, 1500, 'First', {}), (500, 1500, ' line', {'aAppend': 1}),
                 (1500, 2000, '\n', {'aAppend': 1}), (2000, 3500, 'Second', {'aAppend': 1}),
                 (2500, 3500, ' line', {'aAppend': 1}), (3500, 4000, '\n', {'aAppend': 1}),
                 (4000, 5500, 'Third line', {'aAppend': 1}))
    result = cues(raw)
    assert [(c.start, c.end, c.text) for c in result] == [
        (0, 3500, 'First line\nSecond line'), (4000, 5500, 'Third line')]


def test_word_by_word_revisions_become_one_stable_block():
    raw = events((0, 2000, 'Hello', {}), (100, 2000, 'Hello there', {}),
                 (200, 2000, 'Hello there friend', {}), (2000, 4000, 'Next line', {}))
    result = cues(raw)
    assert len(result) == 1
    assert result[0].text == 'Hello there friend\nNext line'
    assert (result[0].start, result[0].end) == (0, 4000)


def test_vtt_rolling_cues_remove_word_timestamps_and_source_positioning():
    raw = ('WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000 align:start position:0%\n'
           '<c>First</c><00:00:00.500> line\n\n'
           '2\n00:00:02.000 --> 00:00:05.000\nFirst line\nSecond line\n\n'
           '3\n00:00:04.000 --> 00:00:07.000\nSecond line\nThird line\n')
    output = two_line_vtt(raw, 'vtt')
    result = _vtt_cues(output)
    assert [c.text for c in result] == ['First line\nSecond line', 'Third line']
    assert result[0].end == result[1].start == 4000
    assert 'align:' not in output and '<00:' not in output and '<c>' not in output


def test_long_lines_wrap_and_keep_no_more_than_two_lines_per_block():
    sentence = 'A longer caption should wrap into readable lines without introducing overlapping blocks or losing any of its words.'
    result = cues(events((0, 8000, sentence, {})))
    assert len(result) == 2
    assert ' '.join(c.text.replace('\n', ' ') for c in result) == sentence
    assert all(len(c.text.splitlines()) <= 2 for c in result)
    assert all(len(line) <= 40 for c in result for line in c.text.splitlines())
    assert all(a.end <= b.start for a, b in zip(result, result[1:]))


def test_long_silences_stay_empty_and_repeated_speech_is_retained():
    result = cues(events((0, 1000, 'Yes', {}), (6000, 7000, 'Yes', {})))
    assert [(c.start, c.end, c.text) for c in result] == [(0, 1000, 'Yes'), (6000, 7000, 'Yes')]


def test_authored_two_line_cue_keeps_its_timing():
    raw = 'WEBVTT\n\n00:01.000 --> 00:04.000\nAn authored line\nIts second line\n'
    result = cues(raw, 'vtt')
    assert [(c.start, c.end, c.text) for c in result] == [(1000, 4000, 'An authored line\nIts second line')]


def test_entities_are_displayed_as_text_and_metadata_is_ignored():
    raw = 'WEBVTT\n\nNOTE not a cue\nIgnored\n\nSTYLE\n::cue {color:red}\n\n00:00.000 --> 00:02.000\n&lt;script&gt; &amp; text\n'
    result = cues(raw, 'vtt')
    assert len(result) == 1
    assert result[0].text == '&lt;script&gt; &amp; text'


def test_empty_or_invalid_source_has_no_cues():
    assert cues('not json') == []
    assert cues('WEBVTT\n\n', 'vtt') == []


def test_rolling_window_with_growing_second_line_does_not_repeat_first_line():
    raw = events((0, 3000, 'First line', {}), (1000, 4000, 'First line\nSecond', {}),
                 (1500, 4500, 'First line\nSecond line', {}),
                 (2000, 5000, 'First line\nSecond line complete', {}),
                 (3000, 6000, 'Second line complete\nThird line', {}))
    result = cues(raw)
    assert [c.text for c in result] == ['First line\nSecond line complete', 'Third line']
    assert result[0].end == result[1].start == 3000


def test_playback_caption_path_applies_blocks_for_both_source_formats(monkeypatch):
    from bt import ytdl
    track = {'key': 'en', 'formats': {}}
    monkeypatch.setattr(ytdl, 'caption_tracks', lambda info: [track])
    samples = [
        ('json3', events((0, 2000, 'First', {}), (2000, 4000, 'Second', {}))),
        ('vtt', 'WEBVTT\n\n00:00.000 --> 00:02.000\nFirst\n\n00:02.000 --> 00:04.000\nSecond\n')
    ]
    for fmt, raw in samples:
        monkeypatch.setattr(ytdl, 'fetch_track', lambda *args, fmt=fmt, raw=raw: (fmt, raw))
        result = _vtt_cues(ytdl.caption_vtt({}, 'en'))
        assert [(c.start, c.end, c.text) for c in result] == [(0, 4000, 'First\nSecond')]


def test_wrapped_rolling_lines_keep_their_speech_order_and_non_overlapping_times():
    first = 'This caption is long enough to need several display lines before the next spoken caption starts.'
    result = cues(events((0, 8000, first, {}), (4000, 10000, 'A different next line', {})))
    assert ' '.join(c.text.replace('\n', ' ') for c in result) == first + ' A different next line'
    assert all(a.end <= b.start for a, b in zip(result, result[1:]))


def test_authored_repeated_speech_is_not_treated_as_rolling_duplication():
    raw = 'WEBVTT\n\n00:00.000 --> 00:02.000\nYes\n\n00:02.000 --> 00:04.000\nYes\n'
    result = _vtt_cues(two_line_vtt(raw, 'vtt', rolling=False))
    assert [(c.start, c.end, c.text) for c in result] == [(0, 4000, 'Yes\nYes')]
