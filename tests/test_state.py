import json

from nostalgiabox.state import RESUME_STATE_FILENAME, ResumeStateStore
from tests.helpers import FakeClock


def test_resume_state_round_trip(tmp_path):
    clock = FakeClock()
    episode = tmp_path / "show" / "episode.mp4"
    episode.parent.mkdir()
    episode.write_bytes(b"video")

    store = ResumeStateStore(tmp_path / "state", clock=clock, wall_clock=clock)
    store.load()
    store.remember(2, episode, 42.5)

    restored = ResumeStateStore(tmp_path / "state").load()
    assert restored[2].path == episode
    assert restored[2].position == 42.5
    assert restored[2].saved_at == 1000.0


def test_resume_state_writes_are_throttled_and_force_flushes(tmp_path):
    clock = FakeClock()
    state_dir = tmp_path / "state"
    episode = tmp_path / "episode.mp4"
    store = ResumeStateStore(state_dir, clock=clock, wall_clock=clock)

    store.remember(2, episode, 10.0)  # first update writes immediately
    state_path = state_dir / RESUME_STATE_FILENAME
    assert json.loads(state_path.read_text())["2"]["position"] == 10.0

    clock.advance(5.0)
    store.remember(2, episode, 20.0)
    assert json.loads(state_path.read_text())["2"]["position"] == 10.0

    assert store.flush(force=True) is True
    assert json.loads(state_path.read_text())["2"]["position"] == 20.0
    assert not list(state_dir.glob("*.tmp"))


def test_resume_state_flushes_after_interval(tmp_path):
    clock = FakeClock()
    state_dir = tmp_path / "state"
    episode = tmp_path / "episode.mp4"
    store = ResumeStateStore(state_dir, clock=clock, wall_clock=clock)
    store.remember(2, episode, 10.0)
    store.remember(2, episode, 20.0)

    clock.advance(15.0)
    assert store.flush() is True
    payload = json.loads((state_dir / RESUME_STATE_FILENAME).read_text())
    assert payload["2"]["position"] == 20.0


def test_corrupt_resume_state_is_ignored(tmp_path, caplog):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / RESUME_STATE_FILENAME).write_text("{not json")

    store = ResumeStateStore(state_dir)
    assert store.load() == {}
    assert "ignoring corrupt resume state file" in caplog.text


def test_invalid_resume_entries_are_skipped(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / RESUME_STATE_FILENAME).write_text(
        json.dumps(
            {
                "2": {"path": "/shows/good.mp4", "position": 12, "saved_at": 1},
                "bad": {"path": "/shows/bad.mp4", "position": 3, "saved_at": 1},
                "4": {"path": "", "position": 3, "saved_at": 1},
            }
        )
    )

    entries = ResumeStateStore(state_dir).load()
    assert list(entries) == [2]
