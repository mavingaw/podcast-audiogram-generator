"""The local model layer.

The model itself is a five-gigabyte download that will not be present in a test
run, so these assert the two things that actually matter: that its absence is
handled everywhere, and that its output is parsed defensively when it is there.
A model that returns prose, truncated JSON, or a rating of 47 must not corrupt
a suggestion list.
"""

from __future__ import annotations

import pytest

from app.services import llm


@pytest.fixture(autouse=True)
def reset():
    llm.unload()
    yield
    llm.unload()


# --------------------------------------------------------------------------
# Absence is a supported state
# --------------------------------------------------------------------------


def test_reranking_without_a_model_returns_the_input_unchanged():
    clips = [{"text": "a", "score": 0.9}, {"text": "b", "score": 0.4}]
    assert llm.rerank(clips) == clips


def test_reranking_an_empty_list_is_fine():
    assert llm.rerank([]) == []


def test_rating_without_a_model_returns_none(monkeypatch):
    monkeypatch.setattr(llm, "load", lambda: None)
    assert llm.rate("anything") is None


def test_status_reports_why_it_is_not_ready():
    status = llm.runtime_status()
    for key in ("enabled", "runtime_installed", "model_present", "ready"):
        assert key in status


def enabled_settings():
    """Settings is frozen, so enabling the model means replacing it."""
    import dataclasses

    return dataclasses.replace(llm.settings, llm_enabled=True)


def test_a_missing_runtime_does_not_raise(monkeypatch):
    """No wheel installed is the normal case on a CPU-only host."""
    monkeypatch.setattr(llm, "settings", enabled_settings())
    assert llm.available() in (True, False)


def test_loading_is_not_retried_after_it_fails(monkeypatch):
    """A broken model file must not cost a load attempt on every clip."""
    attempts = []

    class Boom:
        def __init__(self, **kwargs):
            attempts.append(1)
            raise RuntimeError("no")

    monkeypatch.setattr(llm, "settings", enabled_settings())
    monkeypatch.setattr(llm, "model_path", lambda: _existing_file())
    monkeypatch.setitem(__import__("sys").modules, "llama_cpp", _fake_module(Boom))

    assert llm.load() is None
    assert llm.load() is None
    assert len(attempts) == 1


def _existing_file():
    import pathlib
    import tempfile

    path = pathlib.Path(tempfile.mkstemp(suffix=".gguf")[1])
    return path


def _fake_module(cls):
    import types

    module = types.ModuleType("llama_cpp")
    module.Llama = cls
    return module


# --------------------------------------------------------------------------
# Parsing what the model says
# --------------------------------------------------------------------------


LINES = ["The thing nobody tells you.", "You expect fireworks.", "You get a Tuesday."]


def test_clean_json_is_read():
    rating = llm._parse(
        '{"hook": 8, "standalone": 7, "interest": 9, '
        '"best_line": 2, "reason": "surprising claim"}', LINES
    )
    assert rating["hook"] == 8
    assert rating["reason"] == "surprising claim"
    # Resolved back to the excerpt, not invented.
    assert rating["headline"] == "You expect fireworks."


def test_json_wrapped_in_prose_is_read():
    """Small models like to explain themselves first."""
    reply = (
        "Sure! Here is my rating:\n"
        '{"hook": 6, "standalone": 5, "interest": 7, "best_line": 1, "reason": "ok"}\n'
        "Let me know if you want another."
    )
    assert llm._parse(reply, LINES)["hook"] == 6


def test_out_of_range_ratings_are_clamped():
    rating = llm._parse('{"hook": 47, "standalone": -3, "interest": 5}', LINES)
    assert rating["hook"] == 10
    assert rating["standalone"] == 0


def test_string_numbers_are_accepted():
    assert llm._parse('{"hook": "8", "standalone": "7", "interest": "6"}', LINES)["hook"] == 8


def test_broken_output_is_refused():
    assert llm._parse("no json here", LINES) is None
    assert llm._parse("", LINES) is None
    assert llm._parse(None, LINES) is None
    assert llm._parse('{"hook": broken}', LINES) is None
    assert llm._parse('{"hook": "not a number"}', LINES) is None


def test_missing_fields_default_to_zero_rather_than_failing():
    rating = llm._parse('{"hook": 5}', LINES)
    assert rating["hook"] == 5
    assert rating["standalone"] == 0
    assert rating["headline"] == ""


def test_absurdly_long_text_is_truncated():
    reply = '{"hook":1,"standalone":1,"interest":1,"reason":"%s"}' % ("x" * 500)
    assert len(llm._parse(reply, LINES)["reason"]) <= 120


# --------------------------------------------------------------------------
# Re-ranking
# --------------------------------------------------------------------------


def fake_rating(hook, standalone, interest, reason="", headline=""):
    return {"hook": hook, "standalone": standalone, "interest": interest,
            "reason": reason, "headline": headline}


def test_the_model_can_reorder_the_heuristic_ranking(monkeypatch):
    clips = [
        {"text": "admin chatter", "score": 0.90, "reasons": [], "title": "Admin"},
        {"text": "the good bit", "score": 0.80, "reasons": [], "title": "Good"},
    ]
    ratings = {"admin chatter": fake_rating(1, 1, 1),
               "the good bit": fake_rating(10, 10, 10, "surprising", "the good bit")}
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: ratings[text])

    result = llm.rerank(clips)
    assert result[0]["text"] == "the good bit"
    assert result[0]["llm"]["hook"] == 10


def test_the_models_reason_is_surfaced_first(monkeypatch):
    clips = [{"text": "x", "score": 0.5, "reasons": ["35s, a postable length"]}]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: fake_rating(9, 9, 9, "contrarian take"))
    assert llm.rerank(clips)[0]["reasons"][0] == "contrarian take"


def test_a_clip_the_model_cannot_rate_keeps_its_place(monkeypatch):
    clips = [{"text": "a", "score": 0.9}, {"text": "b", "score": 0.4}]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: None)
    result = llm.rerank(clips)
    assert [c["text"] for c in result] == ["a", "b"]
    assert "llm" not in result[0]


def test_only_the_shortlist_is_read(monkeypatch):
    """The heuristic pass exists so the model never reads the whole episode."""
    seen = []
    clips = [{"text": f"clip {i}", "score": 1.0 - i / 100} for i in range(40)]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: seen.append(text) or fake_rating(5, 5, 5))
    llm.rerank(clips, shortlist=5)
    assert len(seen) == 5


def test_heuristics_still_carry_weight(monkeypatch):
    """A perfect model score must not fully override a poor heuristic one."""
    clips = [
        {"text": "well formed", "score": 1.20, "reasons": []},
        {"text": "badly formed", "score": 0.10, "reasons": []},
    ]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "rate",
        lambda text: fake_rating(10, 10, 10) if text == "badly formed" else fake_rating(0, 0, 0),
    )
    result = llm.rerank(clips)
    # 0.10 + 0.55 is still less than 1.20.
    assert result[0]["text"] == "well formed"


def test_reranking_does_not_lose_clips(monkeypatch):
    clips = [{"text": f"c{i}", "score": 0.5} for i in range(20)]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: fake_rating(5, 5, 5))
    assert len(llm.rerank(clips)) == 20


def test_a_cuda_build_without_a_driver_is_not_an_error(monkeypatch):
    """The CUDA wheel raises RuntimeError at import when libcuda is missing.

    Catching only ImportError let that escape into the request that asked for
    suggestions — found by running the real image without `--gpus`.
    """
    import sys
    import types

    class Exploding(types.ModuleType):
        def __getattr__(self, name):
            raise RuntimeError("libcuda.so.1: cannot open shared object file")

    def boom(name, *args, **kwargs):
        if name == "llama_cpp":
            raise RuntimeError("libcuda.so.1: cannot open shared object file")
        return original(name, *args, **kwargs)

    original = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    monkeypatch.setattr(llm, "settings", enabled_settings())
    monkeypatch.delitem(sys.modules, "llama_cpp", raising=False)
    monkeypatch.setattr("builtins.__import__", boom)

    assert llm.available() is False
    assert llm.runtime_status()["runtime_installed"] is False
    assert llm.load() is None


# --------------------------------------------------------------------------
# The model selects; it never writes
# --------------------------------------------------------------------------
#
# An earlier version asked the model for a title and got "Home Tour" and "IT to
# Care" - good summaries of words the speaker never said. A title is content: it
# goes on the post, and sometimes into the frame. These pin the rule that it can
# only ever be speech that was actually in the clip.


TEXT = ("The thing nobody tells you is that it gets easier. "
        "You expect fireworks. Instead you get a Tuesday.")


def clip():
    return {"text": TEXT, "score": 0.5, "title": "The thing nobody tells you", "reasons": []}


def test_a_chosen_line_becomes_the_title_verbatim(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "rate", lambda text: fake_rating(9, 9, 9, headline="You expect fireworks.")
    )
    title = llm.rerank([clip()])[0]["title"]
    assert title.rstrip("\u2026").rstrip(".") in TEXT


def test_an_invented_headline_is_refused(monkeypatch):
    """The exact failure this was built to stop."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "rate", lambda text: fake_rating(9, 9, 9, headline="Home Tour"))
    assert llm.rerank([clip()])[0]["title"] == "The thing nobody tells you"


def test_a_paraphrase_is_refused(monkeypatch):
    """Close is not the same as said."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(
        llm, "rate", lambda text: fake_rating(9, 9, 9, headline="You expected fireworks")
    )
    assert llm.rerank([clip()])[0]["title"] == "The thing nobody tells you"


def test_every_title_is_speech_from_its_own_clip(monkeypatch):
    """Whatever the model returns, the invariant holds across a whole list."""
    monkeypatch.setattr(llm, "available", lambda: True)
    replies = iter([
        fake_rating(9, 9, 9, headline="You expect fireworks."),
        fake_rating(8, 8, 8, headline="A Better Title"),
        fake_rating(7, 7, 7, headline=""),
    ])
    monkeypatch.setattr(llm, "rate", lambda text: next(replies))
    for result in llm.rerank([clip(), clip(), clip()]):
        stem = result["title"].rstrip("\u2026").rstrip(".")
        assert stem in TEXT, result["title"]


def test_a_line_number_outside_the_excerpt_yields_no_headline():
    base = '{"hook":5,"standalone":5,"interest":5,"best_line":%s}'
    assert llm._parse(base % "99", LINES)["headline"] == ""
    assert llm._parse(base % "0", LINES)["headline"] == ""
    assert llm._parse(base % '"two"', LINES)["headline"] == ""


def test_the_prompt_forbids_writing():
    assert "Do NOT write a title" in llm.PROMPT
    assert "paraphrase" in llm.PROMPT


def test_excerpts_split_into_numbered_lines():
    assert llm._lines_of(TEXT) == [
        "The thing nobody tells you is that it gets easier.",
        "You expect fireworks.",
        "Instead you get a Tuesday.",
    ]
    assert llm._lines_of("") == []
    # Unpunctuated speech is still one choosable line.
    assert llm._lines_of("no full stops here") == ["no full stops here"]


def test_a_long_title_is_cut_short_not_reworded():
    long_line = "one two three four five six seven eight nine ten eleven twelve"
    trimmed = llm._trim_title(long_line)
    assert trimmed.endswith("\u2026")
    assert trimmed.rstrip("\u2026") in long_line
