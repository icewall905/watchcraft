"""Feedback compares complete words, using the same tokenizer for titles and missions."""
from types import SimpleNamespace

from bt.hub.api import _salient


def curator(**overrides):
    return SimpleNamespace(**{'name': '', 'subject': '', 'keywords': [], 'queries': [], 'aliases': [], **overrides})


def test_word_inside_a_different_mission_word_is_still_learned():
    assert _salient(SimpleNamespace(title='Rust systems'), curator(subject='trust')) == ['rust', 'systems']


def test_all_curator_fields_use_whole_words_and_hyphen_boundaries():
    c = curator(name='SCIENCE', subject='space-flight', keywords=['rocket'], queries=['Mars missions'], aliases=['orbit'])
    title = 'science-space-flight rocket Mars missions orbit discoveries'
    assert _salient(SimpleNamespace(title=title), c) == ['discoveries']


def test_feedback_retains_order_deduplicates_and_caps_distinctive_words():
    assert _salient(SimpleNamespace(title='The Rust rust systems networks tools'), curator()) == ['rust', 'systems', 'networks']
