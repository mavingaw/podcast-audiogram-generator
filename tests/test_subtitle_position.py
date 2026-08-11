"""Tests for the configurable subtitle block position."""
import pytest

from audiogram_generator.rendering.layouts import (
    LAYOUT_CONFIGS,
    SUBTITLE_Y_OFFSET_DEFAULTS,
    apply_subtitle_overrides,
)


@pytest.fixture(autouse=True)
def restore_defaults():
    """apply_subtitle_overrides writes into a module-level table."""
    yield
    apply_subtitle_overrides({})


def test_override_is_applied():
    apply_subtitle_overrides({'subtitles': {'vertical': {'y_offset': 0.91}}})
    assert LAYOUT_CONFIGS['vertical']['transcript_y_offset'] == 0.91


def test_other_formats_keep_their_defaults():
    apply_subtitle_overrides({'subtitles': {'vertical': {'y_offset': 0.91}}})
    assert LAYOUT_CONFIGS['square']['transcript_y_offset'] == SUBTITLE_Y_OFFSET_DEFAULTS['square']


def test_empty_config_restores_the_defaults():
    apply_subtitle_overrides({'subtitles': {'vertical': {'y_offset': 0.91}}})
    apply_subtitle_overrides({})
    assert LAYOUT_CONFIGS['vertical']['transcript_y_offset'] == SUBTITLE_Y_OFFSET_DEFAULTS['vertical']


def test_none_config_is_tolerated():
    apply_subtitle_overrides(None)
    assert LAYOUT_CONFIGS['vertical']['transcript_y_offset'] == SUBTITLE_Y_OFFSET_DEFAULTS['vertical']


@pytest.mark.parametrize("bad", ["boh", None, 3, -0.5])
def test_invalid_values_keep_the_default(bad, caplog):
    apply_subtitle_overrides({'subtitles': {'vertical': {'y_offset': bad}}})
    assert LAYOUT_CONFIGS['vertical']['transcript_y_offset'] == SUBTITLE_Y_OFFSET_DEFAULTS['vertical']


def test_integer_bounds_are_accepted():
    apply_subtitle_overrides({'subtitles': {'vertical': {'y_offset': 1}}})
    assert LAYOUT_CONFIGS['vertical']['transcript_y_offset'] == 1.0
