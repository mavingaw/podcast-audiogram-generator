"""Expiring old renders.

Deletion is the one operation with no undo, so most of these are about what the
sweep refuses to do: it is off unless asked, it keeps a floor of recent work,
and a configuration it cannot parse leaves everything alone.
"""

from __future__ import annotations

import os
import time

import pytest

from app.services import retention
from app.services.retention import collect, sweep


def render(root, project_id: str, age_days: float = 0.0, size: int = 1024):
    """A directory that looks like a finished export."""
    path = root / project_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "audiogram.mp4").write_bytes(b"x" * size)
    when = time.time() - age_days * 86400.0
    for entry in (path / "audiogram.mp4", path):
        os.utime(entry, (when, when))
    return path


@pytest.fixture
def outputs(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    return root


# --------------------------------------------------------------------------
# Off by default
# --------------------------------------------------------------------------


def test_retention_is_disabled_unless_a_number_is_configured(monkeypatch):
    monkeypatch.delenv("PAS_RETENTION_DAYS", raising=False)
    assert retention.retention_days() == 0


def test_a_disabled_sweep_deletes_nothing(outputs):
    render(outputs, "ancient", age_days=9000)
    result = sweep(outputs, max_age_days=0, known_projects=set())
    assert result["enabled"] is False
    assert result["removed"] == []
    assert (outputs / "ancient").exists()


def test_an_unparseable_age_limit_leaves_retention_off(monkeypatch):
    """A typo in an environment variable must not start deleting things."""
    monkeypatch.setenv("PAS_RETENTION_DAYS", "sixty")
    assert retention.retention_days() == 0


def test_a_negative_age_limit_is_treated_as_disabled(monkeypatch):
    monkeypatch.setenv("PAS_RETENTION_DAYS", "-5")
    assert retention.retention_days() == 0


def test_the_keep_floor_has_a_default(monkeypatch):
    monkeypatch.delenv("PAS_RETENTION_KEEP", raising=False)
    assert retention.retention_keep() == retention.DEFAULT_KEEP


def test_an_unparseable_keep_floor_falls_back(monkeypatch):
    monkeypatch.setenv("PAS_RETENTION_KEEP", "lots")
    assert retention.retention_keep() == retention.DEFAULT_KEEP


def test_the_interval_never_drops_below_half_an_hour(monkeypatch):
    """A sweep every few seconds would spend the disk it is meant to save."""
    monkeypatch.setenv("PAS_RETENTION_INTERVAL_HOURS", "0.001")
    assert retention.retention_interval_hours() >= 0.5


# --------------------------------------------------------------------------
# What it expires
# --------------------------------------------------------------------------


def test_renders_past_the_age_limit_are_removed(outputs):
    render(outputs, "old", age_days=90)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"old"})
    assert result["removed"] == ["old"]
    assert not (outputs / "old").exists()


def test_renders_inside_the_age_limit_survive(outputs):
    render(outputs, "recent", age_days=3)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"recent"})
    assert result["removed"] == []
    assert (outputs / "recent").exists()


def test_the_most_recent_renders_survive_whatever_their_age(outputs):
    """The floor exists so a mistimed age limit cannot empty the library."""
    for index in range(5):
        render(outputs, f"clip-{index}", age_days=100 + index)
    live = {f"clip-{index}" for index in range(5)}

    result = sweep(outputs, max_age_days=1, keep_minimum=3, known_projects=live)
    assert result["kept"] == 3
    assert len(result["removed"]) == 2
    # The three newest are the ones that stayed.
    assert (outputs / "clip-0").exists()
    assert (outputs / "clip-1").exists()
    assert (outputs / "clip-2").exists()


def test_a_keep_floor_larger_than_the_library_keeps_everything(outputs):
    for index in range(3):
        render(outputs, f"clip-{index}", age_days=500)
    result = sweep(
        outputs, max_age_days=1, keep_minimum=20,
        known_projects={f"clip-{i}" for i in range(3)},
    )
    assert result["removed"] == []
    assert result["kept"] == 3


def test_orphaned_renders_go_without_waiting_for_the_age_limit(outputs):
    """Their project was deleted, so somebody already said they were finished."""
    render(outputs, "deleted-project", age_days=0)
    result = sweep(outputs, max_age_days=30, keep_minimum=20, known_projects=set())
    assert result["removed"] == ["deleted-project"]


def test_an_orphan_is_not_protected_by_the_keep_floor(outputs):
    """The floor guards against a mistimed age limit, not against a deletion."""
    render(outputs, "live", age_days=0)
    render(outputs, "orphan", age_days=0)
    result = sweep(outputs, max_age_days=30, keep_minimum=20, known_projects={"live"})
    assert result["removed"] == ["orphan"]
    assert (outputs / "live").exists()


def test_a_live_project_is_never_removed_before_its_time(outputs):
    render(outputs, "live", age_days=10)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"live"})
    assert result["removed"] == []


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_a_dry_run_reports_without_deleting(outputs):
    render(outputs, "old", age_days=90)
    result = sweep(
        outputs, max_age_days=30, keep_minimum=0,
        known_projects={"old"}, dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["removed"] == ["old"]
    assert (outputs / "old").exists(), "a dry run must not delete anything"


def test_the_summary_counts_what_it_freed(outputs):
    render(outputs, "old", age_days=90, size=4096)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"old"})
    assert result["freed_bytes"] >= 4096
    assert result["removed_count"] == 1


def test_the_summary_shape_is_stable(outputs):
    result = sweep(outputs, max_age_days=30, known_projects=set())
    for key in ("enabled", "dry_run", "scanned", "removed", "removed_count",
                "kept", "freed_bytes", "errors"):
        assert key in result


# --------------------------------------------------------------------------
# Not making things worse
# --------------------------------------------------------------------------


def test_a_missing_outputs_directory_is_not_an_error(tmp_path):
    result = sweep(tmp_path / "nothing-here", max_age_days=30, known_projects=set())
    assert result["scanned"] == 0
    assert result["removed"] == []


def test_loose_files_beside_the_render_directories_are_ignored(outputs):
    (outputs / "stray.txt").write_text("not a render")
    render(outputs, "old", age_days=90)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"old"})
    assert result["removed"] == ["old"]
    assert (outputs / "stray.txt").exists()


def test_age_is_taken_from_the_newest_file_inside(outputs):
    """A directory's own mtime lies; a republished export updates a file in it."""
    path = render(outputs, "clip", age_days=90)
    fresh = path / "captions.srt"
    fresh.write_text("1")
    now = time.time()
    os.utime(fresh, (now, now))

    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"clip"})
    assert result["removed"] == [], "a freshly rewritten render is not old"


def test_the_sweep_aborts_when_the_project_list_cannot_be_read(outputs, monkeypatch):
    """Without it, every render looks like an orphan and all of them go."""
    render(outputs, "live", age_days=1)

    def unavailable():
        raise RuntimeError("database is down")

    monkeypatch.setattr(retention, "live_project_ids", unavailable)
    result = sweep(outputs, max_age_days=30)
    assert result["removed"] == []
    assert result["errors"]
    assert (outputs / "live").exists()


def test_a_failed_deletion_is_reported_rather_than_raised(outputs, monkeypatch):
    render(outputs, "old", age_days=90)

    def refuse(path):
        raise OSError("permission denied")

    monkeypatch.setattr(retention.shutil, "rmtree", refuse)
    result = sweep(outputs, max_age_days=30, keep_minimum=0, known_projects={"old"})
    assert result["errors"]


def test_candidates_are_ordered_newest_first(outputs):
    render(outputs, "older", age_days=50)
    render(outputs, "newer", age_days=1)
    order = [item.project_id for item in collect(outputs, {"older", "newer"})]
    assert order == ["newer", "older"]


def test_orphans_are_flagged(outputs):
    render(outputs, "live", age_days=1)
    render(outputs, "gone", age_days=1)
    found = {item.project_id: item.orphaned for item in collect(outputs, {"live"})}
    assert found == {"live": False, "gone": True}


# --------------------------------------------------------------------------
# Starting it
# --------------------------------------------------------------------------


def test_start_does_nothing_when_retention_is_off(monkeypatch):
    monkeypatch.delenv("PAS_RETENTION_DAYS", raising=False)
    assert retention.start() is False


def test_start_runs_a_sweep_in_the_foreground_when_asked(monkeypatch, outputs):
    monkeypatch.setenv("PAS_RETENTION_DAYS", "30")
    swept = []
    monkeypatch.setattr(retention, "sweep_now", lambda: swept.append(1) or {})
    assert retention.start(background=False) is True
    assert swept == [1]


def test_a_failure_while_starting_is_swallowed(monkeypatch):
    """Start-up must not be blocked by housekeeping."""
    monkeypatch.setenv("PAS_RETENTION_DAYS", "30")

    def boom():
        raise RuntimeError("no")

    monkeypatch.setattr(retention, "sweep_now", boom)
    assert retention.start(background=False) is False
