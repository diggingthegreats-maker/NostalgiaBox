import signal

import pytest

from nostalgiabox.actions import Action, InputEvent
from nostalgiabox.app import TVApp, run_from_config
from nostalgiabox.config import config_from_dict
from nostalgiabox.input.manager import InputManager
from nostalgiabox.player import END_EOF, END_ERROR, MockPlayer
from nostalgiabox.state import ResumeStateStore
from tests.helpers import FakeClock, make_show


def build_app(tmp_path, *, assets_dir=None, player=None, **overrides):
    for name in ("dragon", "arthur", "rugrats"):
        make_show(tmp_path, name, 4)
    data = {
        "shuffle_seed": 7,
        "start_channel": 2,
        "start_offset": 0,  # keep test assertions on start=0 unless overridden
        "state_dir": str(tmp_path / "state"),
        "power_off_command": [],  # no-op in tests (never actually shut down)
        "channels": [
            {"number": 2, "name": "Dragon Tales", "path": str(tmp_path / "dragon")},
            {"number": 3, "name": "Arthur", "path": str(tmp_path / "arthur")},
            {"number": 4, "name": "Rugrats", "path": str(tmp_path / "rugrats")},
        ],
    }
    data.update(overrides)
    config = config_from_dict(data)
    clock = FakeClock()
    player = player or MockPlayer()
    app = TVApp(
        config,
        player,
        InputManager([]),
        clock=clock,
        assets_dir=assets_dir,
    )
    return app, player, clock


def send(app, action, value=None):
    app.handle_event(InputEvent(action, value))


def test_start_tunes_to_start_channel_and_plays(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    assert app.lineup.current.number == 2
    assert player.current is not None  # an episode is playing
    assert player.volume == 70
    assert player.overlays.get(1) and "Dragon Tales" in player.overlays[1]
    assert 5 not in player.overlays  # disabled DTG bug adds no default overlay


def test_channel_up_down_wraps(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.CHANNEL_UP)
    assert app.lineup.current.number == 3
    send(app, Action.CHANNEL_UP)
    assert app.lineup.current.number == 4
    send(app, Action.CHANNEL_UP)
    assert app.lineup.current.number == 2  # wrapped
    send(app, Action.CHANNEL_DOWN)
    assert app.lineup.current.number == 4  # wrapped back


def test_volume_controls(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.VOLUME_UP)
    assert app.volume == 75 and player.volume == 75
    send(app, Action.VOLUME_DOWN)
    assert app.volume == 70
    # volume overlay was drawn
    assert "Volume" in player.overlays[2]


def test_volume_clamps(tmp_path):
    app, player, _ = build_app(tmp_path, initial_volume=98, volume_step=5)
    app.start()
    send(app, Action.VOLUME_UP)
    assert app.volume == 100
    for _ in range(30):
        send(app, Action.VOLUME_DOWN)
    assert app.volume == 0


def test_volume_down_at_zero_powers_off(tmp_path):
    app, player, _ = build_app(tmp_path, initial_volume=10, volume_step=5)
    app.start()
    send(app, Action.VOLUME_DOWN)   # 10 -> 5
    send(app, Action.VOLUME_DOWN)   # 5 -> 0
    assert app.volume == 0 and not app.powered_off
    send(app, Action.VOLUME_DOWN)   # one more at 0 -> power off
    assert app.powered_off is True
    assert app._running is False
    assert player.current is None   # playback stopped


def test_power_off_disabled(tmp_path):
    app, player, _ = build_app(
        tmp_path, initial_volume=0, power_off_on_min_volume=False
    )
    app.start()
    send(app, Action.VOLUME_DOWN)   # at 0, but feature disabled
    assert app.powered_off is False


def test_mute_toggle_and_unmute_on_volume(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.MUTE)
    assert app.muted and player.muted
    send(app, Action.VOLUME_UP)  # changing volume unmutes
    assert not app.muted and not player.muted


def test_direct_channel_entry_with_enter(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.DIGIT, 4)
    assert app.lineup.current.number == 2  # not committed yet
    send(app, Action.ENTER)
    assert app.lineup.current.number == 4


def test_direct_channel_entry_times_out(tmp_path):
    app, player, clock = build_app(tmp_path)
    app.start()
    send(app, Action.DIGIT, 3)
    assert app.lineup.current.number == 2
    clock.advance(2.1)  # past the entry timeout
    app.step()
    assert app.lineup.current.number == 3


def test_invalid_channel_entry_shows_message(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    assert app.select_channel_number(99) is False
    assert "NO CHANNEL" in player.overlays.get(4, "")
    assert app.lineup.current.number == 2  # unchanged


def test_last_channel_jump(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.CHANNEL_UP)  # now on 3, last=2
    assert app.lineup.current.number == 3
    send(app, Action.LAST_CHANNEL)
    assert app.lineup.current.number == 2
    send(app, Action.LAST_CHANNEL)  # bounces back to 3
    assert app.lineup.current.number == 3


def test_episode_advances_on_end(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    first = player.current
    player.finish_current(END_EOF)  # simulate the episode ending
    app._drain_playback_events()
    assert player.current is not None
    assert player.current != first  # rolled into the next shuffled episode


def test_standby_blanks_and_ignores_input(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    send(app, Action.POWER)
    assert app.standby
    assert player.current is None  # screen blanked
    assert 3 in player.overlays  # standby overlay
    # input is ignored while in standby
    send(app, Action.CHANNEL_UP)
    assert app.lineup.current.number == 2
    # power again wakes it up and resumes playback
    send(app, Action.POWER)
    assert not app.standby
    assert player.current is not None


def test_quit_stops_running(tmp_path):
    app, player, _ = build_app(tmp_path)
    app.start()
    app._running = True
    send(app, Action.QUIT)
    assert app._running is False


def test_glitch_transition_then_episode(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "glitch.mp4").write_bytes(b"\x00")
    app, player, clock = build_app(tmp_path, assets_dir=assets, transition="glitch")
    app.start()
    send(app, Action.CHANNEL_UP)
    # A glitch->episode transition was issued (glitch clip + preloaded episode).
    assert player.transitions, "expected a transition on channel change"
    clip, target, _start = player.transitions[-1]
    assert clip == assets / "glitch.mp4"
    assert player.current == target  # the episode is what plays


def test_transition_none_cuts_straight(tmp_path):
    # bridge_seconds=0 -> switch immediately, no transition clip, no preload
    app, player, _ = build_app(tmp_path, transition="none", bridge_seconds=0)
    app.start()
    first = player.current
    send(app, Action.CHANNEL_UP)
    assert not player.transitions
    assert player.preloaded is None
    assert player.current is not None and player.current != first


def test_channel_change_bridges_current_until_next_ready(tmp_path):
    # With bridge_seconds>0 and no transition, the current show keeps playing
    # while the next channel preloads, then cuts over after the window.
    app, player, clock = build_app(tmp_path, bridge_seconds=0.8)
    app.start()
    first = player.current
    send(app, Action.CHANNEL_UP)
    assert player.current == first          # old show still playing...
    assert player.preloaded is not None     # ...next channel preloading
    clock.advance(1.0)
    app.step()                              # bridge window elapsed -> switch
    assert player.preloaded is None
    assert player.current is not None and player.current != first


def test_advance_within_channel_has_no_transition(tmp_path):
    # An episode ending should roll straight into the next one (no glitch burst).
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "glitch.mp4").write_bytes(b"\x00")
    app, player, _ = build_app(tmp_path, assets_dir=assets, transition="glitch")
    app.start()
    before = len(player.transitions)
    player.finish_current(END_EOF)
    app._drain_playback_events()
    assert len(player.transitions) == before  # no new transition
    assert player.current is not None


def test_start_offset_applied(tmp_path):
    app, player, _ = build_app(tmp_path, start_offset=5)
    app.start()
    # The episode should begin 5 seconds in, not at the very beginning.
    assert player.played[-1][1] == 5.0


def test_start_offset_range_applied(tmp_path):
    app, player, _ = build_app(tmp_path, start_offset=[6, 10])
    app.start()
    assert 6.0 <= player.played[-1][1] <= 10.0


def test_empty_channel_shows_no_signal(tmp_path):
    (tmp_path / "dragon").mkdir()
    make_show(tmp_path, "arthur", 2)
    config = config_from_dict(
        {
            "channels": [
                {"number": 2, "name": "Dragon Tales", "path": str(tmp_path / "dragon")},
                {"number": 3, "name": "Arthur", "path": str(tmp_path / "arthur")},
            ]
        }
    )
    app = TVApp(config, MockPlayer(), InputManager([]), clock=FakeClock())
    app.start()  # starts on ch 2 which is empty
    assert "NO SIGNAL" in app.player.overlays.get(4, "")


def test_channel_banner_deferred_until_switch(tmp_path):
    app, player, clock = build_app(tmp_path, bridge_seconds=0.8)
    app.start()
    player.overlays.pop(1, None)          # clear the power-on banner
    send(app, Action.CHANNEL_UP)
    assert 1 not in player.overlays       # banner NOT shown during the bridge
    clock.advance(1.0)
    app.step()                            # cut-over happens here
    assert "CH 03" in player.overlays.get(1, "")  # banner appears at the switch


def test_resume_mode_restarts_where_left(tmp_path):
    # bridge_seconds=0 keeps this test focused on resume (immediate switches)
    app, player, _ = build_app(tmp_path, tune_in="resume", bridge_seconds=0)
    app.start()
    playing = player.current
    player.time_pos = 42.0
    send(app, Action.CHANNEL_UP)  # leave ch 2, remembering position 42
    send(app, Action.CHANNEL_DOWN)  # back to ch 2 -> resume at 42
    assert player.current == playing
    assert player.played[-1] == (playing, 42.0)


def test_resume_position_survives_app_restart(tmp_path):
    app, player, _ = build_app(tmp_path, tune_in="resume", bridge_seconds=0)
    app.start()
    playing = player.current
    player.time_pos = 87.25
    send(app, Action.CHANNEL_UP)

    restarted, restarted_player, _ = build_app(
        tmp_path, tune_in="resume", bridge_seconds=0
    )
    restarted.start()
    assert restarted_player.current == playing
    assert restarted_player.played[-1] == (playing, 87.25)


def test_missing_saved_episode_falls_back_to_normal_tune_in(tmp_path):
    app, player, _ = build_app(tmp_path, tune_in="resume", bridge_seconds=0)
    app.start()
    removed = player.current
    player.time_pos = 31.0
    send(app, Action.CHANNEL_UP)
    removed.unlink()

    restarted = TVApp(
        app.config,
        MockPlayer(),
        InputManager([]),
        clock=FakeClock(),
    )
    restarted.start()
    assert restarted.player.current is not None
    assert restarted.player.current != removed
    assert restarted.player.played[-1][1] == 0.0


def test_commercial_position_is_not_persisted(tmp_path):
    ads = make_show(tmp_path, "ads", 2)
    app, player, _ = build_app(
        tmp_path,
        tune_in="resume",
        bridge_seconds=0,
        commercials={
            "enabled": True,
            "path": str(ads),
            "every": 1,
            "count": 1,
        },
    )
    app.start()
    player.finish_current(END_EOF)
    app._drain_playback_events()
    assert player.current is not None and player.current.parent == ads

    player.time_pos = 9.0
    send(app, Action.CHANNEL_UP)
    entries = ResumeStateStore(tmp_path / "state").load()
    assert 2 not in entries


def test_network_bug_tracks_playback_and_standby(tmp_path):
    app, player, _ = build_app(
        tmp_path,
        network_bug={"enabled": True, "text": "DTG", "corner": "bottom-right"},
    )
    app.start()
    assert "DTG" in player.overlays[5]

    send(app, Action.POWER)
    assert 5 not in player.overlays
    send(app, Action.POWER)
    assert "DTG" in player.overlays[5]


def test_network_bug_stays_visible_during_commercials(tmp_path):
    ads = make_show(tmp_path, "ads", 2)
    app, player, _ = build_app(
        tmp_path,
        commercials={"enabled": True, "path": str(ads), "count": 1},
        network_bug={"enabled": True, "text": "DTG"},
    )
    app.start()
    player.finish_current(END_EOF)
    app._drain_playback_events()
    assert player.current is not None and player.current.parent == ads
    assert "DTG" in player.overlays[5]


def test_two_ad_break_plays_episode_ads_episode_and_logs(tmp_path, caplog):
    caplog.set_level("INFO", logger="nostalgiabox.app")
    ads = make_show(tmp_path, "ads", 3)
    app, player, _ = build_app(
        tmp_path,
        commercials={
            "enabled": True,
            "path": str(ads),
            "every": 1,
            "count": 2,
        },
    )
    app.start()
    first_episode = player.current

    player.finish_current(END_EOF)
    app._drain_playback_events()
    first_ad = player.current
    player.finish_current(END_EOF)
    app._drain_playback_events()
    second_ad = player.current
    player.finish_current(END_EOF)
    app._drain_playback_events()
    next_episode = player.current

    assert first_episode.parent != ads
    assert first_ad.parent == ads
    assert second_ad.parent == ads
    assert first_ad != second_ad
    assert next_episode.parent != ads
    assert caplog.text.count("commercial break: playing") == 2


def test_network_bug_hidden_on_no_signal(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    config = config_from_dict(
        {
            "network_bug": {"enabled": True, "text": "DTG"},
            "channels": [{"number": 2, "name": "Empty", "path": str(empty)}],
        }
    )
    app = TVApp(config, MockPlayer(), InputManager([]), clock=FakeClock())
    app.start()
    assert 5 not in app.player.overlays


def test_static_audio_uses_cache_busted_transition_asset(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "glitch-audio.mp4").write_bytes(b"\x00")
    app, player, _ = build_app(
        tmp_path,
        assets_dir=assets,
        transition="glitch",
        static_audio=True,
    )
    app.start()
    send(app, Action.CHANNEL_UP)
    assert player.transitions[-1][0] == assets / "glitch-audio.mp4"


def test_power_off_flushes_resume_before_stopping_player(tmp_path):
    app, player, _ = build_app(
        tmp_path,
        tune_in="resume",
        bridge_seconds=0,
        initial_volume=0,
    )
    app.start()
    playing = player.current
    player.time_pos = 77.0

    send(app, Action.VOLUME_DOWN)

    entry = ResumeStateStore(tmp_path / "state").load()[2]
    assert entry.path == playing
    assert entry.position == 77.0


def test_bridge_keeps_resume_metadata_on_actual_outgoing_channel(tmp_path):
    app, player, _ = build_app(tmp_path, tune_in="resume", bridge_seconds=0.8)
    app.start()
    outgoing = player.current
    player.time_pos = 41.0

    send(app, Action.CHANNEL_UP)  # ch 3 is pending; ch 2 still plays
    assert player.current == outgoing
    send(app, Action.CHANNEL_UP)  # leave pending ch 3 before it commits

    entries = ResumeStateStore(tmp_path / "state").load()
    assert entries[2].path == outgoing
    assert entries[2].position == 41.0
    assert 3 not in entries


def test_bridge_from_commercial_never_persists_pending_show(tmp_path):
    ads = make_show(tmp_path, "ads", 2)
    app, player, _ = build_app(
        tmp_path,
        tune_in="resume",
        bridge_seconds=0.8,
        commercials={"enabled": True, "path": str(ads), "count": 1},
    )
    app.start()
    player.finish_current(END_EOF)
    app._drain_playback_events()
    assert player.current.parent == ads
    player.time_pos = 8.0

    send(app, Action.CHANNEL_UP)
    send(app, Action.CHANNEL_UP)

    assert ResumeStateStore(tmp_path / "state").load() == {}


def test_transition_does_not_save_filler_time_as_target_episode(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "glitch.mp4").write_bytes(b"\x00")
    app, player, _ = build_app(
        tmp_path,
        assets_dir=assets,
        tune_in="resume",
        transition="glitch",
        transition_duration=0.4,
    )
    app.start()
    player.time_pos = 22.0
    send(app, Action.CHANNEL_UP)
    player.time_pos = 0.2  # position belongs to the transition filler

    app.shutdown()

    entries = ResumeStateStore(tmp_path / "state").load()
    assert 2 in entries
    assert 3 not in entries


def test_run_registers_sigterm_for_orderly_shutdown(tmp_path, monkeypatch):
    app, player, _ = build_app(tmp_path)
    app.input.put(InputEvent(Action.QUIT))
    registrations = []

    def fake_signal(signum, handler):
        registrations.append((signum, handler))
        return signal.SIG_DFL

    monkeypatch.setattr("nostalgiabox.app.signal.signal", fake_signal)
    app.run()

    assert registrations[0][0] == signal.SIGTERM
    assert callable(registrations[0][1])
    assert registrations[-1] == (signal.SIGTERM, signal.SIG_DFL)
    assert player.closed is True


def test_synchronous_play_error_is_processed_on_next_drain(tmp_path):
    ads = make_show(tmp_path, "ads", 2)

    class FailFirstAdPlayer(MockPlayer):
        def __init__(self):
            super().__init__()
            self.failed = False

        def play(self, path, *, start=0.0):
            super().play(path, start=start)
            if path.parent == ads and not self.failed:
                self.failed = True
                if self.on_end is not None:
                    self.on_end(END_ERROR)

    player = FailFirstAdPlayer()
    app, player, _ = build_app(
        tmp_path,
        player=player,
        commercials={"enabled": True, "path": str(ads), "count": 1},
    )
    app.start()
    player.finish_current(END_EOF)

    app._drain_playback_events()  # starts the bad ad, which queues END_ERROR
    assert player.current.parent == ads
    app._drain_playback_events()  # processes that newly queued error
    assert player.current.parent != ads


def test_bounded_dry_run_demo_logs_episode_ads_episode(tmp_path, capsys):
    show = make_show(tmp_path, "show", 3)
    ads = make_show(tmp_path, "ads", 3)
    config = config_from_dict(
        {
            "start_offset": 0,
            "commercials": {
                "enabled": True,
                "path": str(ads),
                "every": 1,
                "count": 2,
            },
            "channels": [{"number": 2, "name": "Show", "path": str(show)}],
        }
    )

    run_from_config(config, dry_run=True, demo_ends=3)

    play_lines = [
        line for line in capsys.readouterr().out.splitlines() if "[player] PLAY" in line
    ]
    assert len(play_lines) == 4
    assert str(show) in play_lines[0]
    assert str(ads) in play_lines[1]
    assert str(ads) in play_lines[2]
    assert str(show) in play_lines[3]
