import random

import pytest

from nostalgiabox.channel import (
    BroadcastSchedule,
    Channel,
    ChannelLineup,
    build_lineup,
    detect_season,
    scan_episodes,
)
from nostalgiabox.config import ChannelConfig, config_from_dict
from nostalgiabox.playlist import ShuffleBag
from tests.helpers import make_show


def _channel(tmp_path, name="arthur", episodes=4, **kw):
    folder = make_show(tmp_path, name, episodes)

    cfg = ChannelConfig(number=kw.pop("number", 3), name=name, path=folder)
    eps = scan_episodes(folder, [".mp4"])
    return Channel(cfg, eps, rng=random.Random(0), **kw)


def _commercial_channel(
    tmp_path,
    *,
    every=1,
    count=1,
    commercials=True,
    ad_count=6,
    tune_in="random",
    start_offset=0.0,
):
    show = make_show(tmp_path, "arthur", 8)
    ads = make_show(tmp_path, "commercials", ad_count)
    cfg = ChannelConfig(
        number=3,
        name="Arthur",
        path=show,
        commercials=commercials,
    )
    return Channel(
        cfg,
        scan_episodes(show, [".mp4"]),
        tune_in=tune_in,
        start_offset_min=start_offset,
        start_offset_max=start_offset,
        rng=random.Random(0),
        commercial_bag=ShuffleBag(
            scan_episodes(ads, [".mp4"]), random.Random(1)
        ),
        commercial_every=every,
        commercial_count=count,
    )


def test_scan_episodes_sorted_and_filtered(tmp_path):
    folder = make_show(tmp_path, "arthur", 3)
    (folder / "notes.txt").write_text("nope")
    (folder / ".DS_Store").write_bytes(b"")
    eps = scan_episodes(folder, [".mp4"])
    assert [p.name for p in eps] == [
        "arthur_ep01.mp4",
        "arthur_ep02.mp4",
        "arthur_ep03.mp4",
    ]


def test_detect_season():
    assert detect_season("Arthur S06E01.mp4") == 6
    assert detect_season("arthur.s6e12.mkv") == 6
    assert detect_season("Season 12/ep03.mp4") == 12
    assert detect_season("Arthur 6x05.mp4") == 6
    assert detect_season("Arthurs Perfect Christmas.mp4") is None


def test_scan_exclude_globs(tmp_path):
    folder = tmp_path / "arthur"
    (folder / "Season 1").mkdir(parents=True)
    (folder / "Specials").mkdir(parents=True)
    (folder / "Season 1" / "S01E01.mp4").write_bytes(b"")
    (folder / "Specials" / "Arthur Special.mp4").write_bytes(b"")
    eps = scan_episodes(folder, [".mp4"], exclude=["*special*"])
    names = [p.name for p in eps]
    assert names == ["S01E01.mp4"]


def test_scan_exclude_seasons(tmp_path):
    folder = tmp_path / "arthur"
    folder.mkdir()
    for s in (1, 5, 6, 7, 25):
        (folder / f"Arthur S{s:02d}E01.mp4").write_bytes(b"")
    eps = scan_episodes(folder, [".mp4"], exclude_seasons=set(range(6, 26)))
    seasons = sorted(detect_season(p.name) for p in eps)
    assert seasons == [1, 5]  # 6..25 removed


def test_build_lineup_applies_channel_excludes(tmp_path):
    folder = tmp_path / "arthur"
    folder.mkdir()
    (folder / "Arthur S01E01.mp4").write_bytes(b"")
    (folder / "Arthur S06E01.mp4").write_bytes(b"")
    (folder / "Arthur Special.mp4").write_bytes(b"")
    cfg = config_from_dict(
        {
            "channels": [
                {
                    "number": 3,
                    "name": "Arthur",
                    "path": str(folder),
                    "exclude": ["*special*"],
                    "exclude_seasons": ["6-25"],
                }
            ]
        }
    )
    lineup = build_lineup(cfg)
    eps = list(lineup)[0].episodes
    assert [p.name for p in eps] == ["Arthur S01E01.mp4"]


def test_scan_recursive(tmp_path):
    base = tmp_path / "show"
    (base / "season1").mkdir(parents=True)
    (base / "season2").mkdir(parents=True)
    (base / "season1" / "a.mp4").write_bytes(b"")
    (base / "season2" / "b.mp4").write_bytes(b"")
    assert len(scan_episodes(base, [".mp4"], recursive=True)) == 2
    assert len(scan_episodes(base, [".mp4"], recursive=False)) == 0


def test_tune_in_random_plays_from_start(tmp_path):
    ch = _channel(tmp_path, tune_in="random")
    req = ch.tune_in()
    assert req is not None
    assert req.start == 0.0
    assert req.path in ch.episodes


def test_advance_continues_shuffle(tmp_path):
    ch = _channel(tmp_path, episodes=4, tune_in="random")
    seen = {ch.tune_in().path}
    for _ in range(3):
        seen.add(ch.advance().path)
    assert len(seen) == 4  # every episode shown before repeats


def test_start_offset_fixed(tmp_path):
    ch = _channel(tmp_path, tune_in="random", start_offset_min=5.0, start_offset_max=5.0)
    assert ch.tune_in().start == 5.0
    assert ch.advance().start == 5.0


def test_start_offset_range(tmp_path):
    ch = _channel(tmp_path, tune_in="random", start_offset_min=6.0, start_offset_max=10.0)
    starts = [ch.tune_in().start for _ in range(20)] + [ch.advance().start for _ in range(20)]
    assert all(6.0 <= s <= 10.0 for s in starts)
    assert len(set(round(s, 3) for s in starts)) > 1  # actually varies


def test_resume_mode_remembers_position(tmp_path):
    ch = _channel(tmp_path, tune_in="resume")
    first = ch.tune_in()
    ch.remember(first.path, 123.5)
    again = ch.tune_in()
    assert again.path == first.path
    assert again.start == 123.5


def test_empty_channel_returns_none(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    from nostalgiabox.config import ChannelConfig

    ch = Channel(ChannelConfig(number=9, name="Empty", path=folder), [])
    assert ch.is_empty
    assert ch.tune_in() is None
    assert ch.advance() is None


def test_broadcast_schedule_positions():
    from pathlib import Path

    eps = [Path("a.mp4"), Path("b.mp4"), Path("c.mp4")]
    durs = [100.0, 200.0, 300.0]
    sched = BroadcastSchedule(eps, durs, epoch=0.0, rng=random.Random(0))
    # At t=0 we are at the start of the first item in the (shuffled) order.
    first = sched.at(0.0)
    assert first.start == 0.0
    # The schedule is a loop of total length 600s; t=600 == t=0.
    assert sched.at(600.0).path == first.path
    # 50s into the cycle we should still be within the first item, offset 50.
    assert sched.at(50.0).start == 50.0


def test_broadcast_tune_in_uses_real_time(tmp_path, monkeypatch):
    # Force probe_duration to a known value so we don't need ffprobe/real media.
    import nostalgiabox.channel as channel_mod

    monkeypatch.setattr(channel_mod, "probe_duration", lambda p: 60.0)
    ch = _channel(tmp_path, episodes=3, tune_in="broadcast")
    # Two tune-ins at different times should generally land at different offsets.
    r1 = ch.tune_in(now=0.0)
    r2 = ch.tune_in(now=30.0)
    assert r1.start == 0.0
    assert r2.start == 30.0


def test_commercial_break_cadence_and_fixed_count(tmp_path):
    ch = _commercial_channel(
        tmp_path, every=2, count=2, start_offset=7.0
    )

    assert ch.tune_in().is_commercial is False
    assert ch.advance().is_commercial is False  # one completed episode

    first_ad = ch.advance()  # second completed episode: break is due
    second_ad = ch.advance()
    next_episode = ch.advance()
    assert first_ad.is_commercial is True
    assert second_ad.is_commercial is True
    assert first_ad.start == second_ad.start == 0.0
    assert next_episode.is_commercial is False
    assert next_episode.start == 7.0

    # Commercial completions do not advance the cadence. Two more episode
    # completions are required before the next break.
    assert ch.advance().is_commercial is False
    assert ch.advance().is_commercial is True


def test_commercial_count_range_is_inclusive(tmp_path):
    ch = _commercial_channel(tmp_path, every=1, count=(1, 3))
    ch.tune_in()

    break_sizes = []
    for _ in range(12):
        request = ch.advance()
        size = 0
        while request.is_commercial:
            size += 1
            request = ch.advance()
        break_sizes.append(size)

    assert all(1 <= size <= 3 for size in break_sizes)
    assert len(set(break_sizes)) > 1


def test_channel_can_opt_out_of_commercials(tmp_path):
    ch = _commercial_channel(
        tmp_path, every=1, count=3, commercials=False
    )
    assert ch.tune_in().is_commercial is False
    assert all(ch.advance().is_commercial is False for _ in range(10))


def test_empty_commercial_folder_warns_and_falls_back_to_shows(tmp_path, caplog):
    show = make_show(tmp_path, "arthur", 3)
    empty_ads = tmp_path / "commercials"
    empty_ads.mkdir()
    cfg = config_from_dict(
        {
            "shuffle_seed": 7,
            "commercials": {
                "enabled": True,
                "path": str(empty_ads),
                "every": 1,
                "count": 2,
            },
            "channels": [
                {"number": 3, "name": "Arthur", "path": str(show)}
            ],
        }
    )

    ch = build_lineup(cfg).current
    assert "no playable clips" in caplog.text
    assert "commercial breaks are disabled" in caplog.text
    assert ch.tune_in().is_commercial is False
    assert all(ch.advance().is_commercial is False for _ in range(6))


@pytest.mark.parametrize("commercial_path", [None, "missing"])
def test_missing_commercial_pool_warns_and_disables(tmp_path, caplog, commercial_path):
    show = make_show(tmp_path, "arthur", 2)
    path = None if commercial_path is None else str(tmp_path / commercial_path)
    cfg = config_from_dict(
        {
            "commercials": {"enabled": True, "path": path, "count": 2},
            "channels": [{"number": 3, "name": "Arthur", "path": str(show)}],
        }
    )

    channel = build_lineup(cfg).current
    channel.tune_in()
    assert "commercial breaks are disabled" in caplog.text
    assert all(not channel.advance().is_commercial for _ in range(4))


def test_tune_in_clears_pending_commercials(tmp_path):
    ch = _commercial_channel(tmp_path, every=2, count=3)
    ch.tune_in()
    assert ch.advance().is_commercial is False
    assert ch.advance().is_commercial is True

    # Flip away and back during the break. The pending two ads are discarded,
    # and the freshly tuned episode starts a new cadence.
    assert ch.tune_in().is_commercial is False
    assert ch.advance().is_commercial is False
    assert ch.advance().is_commercial is True


def test_interrupted_break_does_not_consume_unseen_commercials(tmp_path):
    ch = _commercial_channel(tmp_path, every=1, count=3, ad_count=5)
    ch.tune_in()
    bag = ch._commercial_bag
    before = bag.peek_remaining()

    assert ch.advance().is_commercial is True
    assert bag.peek_remaining() == before - 1
    ch.tune_in()  # abandon the two ads that had not started

    assert bag.peek_remaining() == before - 1
    assert ch.advance().is_commercial is True
    assert bag.peek_remaining() == before - 2

def test_commercial_shuffle_bag_exhausts_before_reshuffling(tmp_path):
    ch = _commercial_channel(tmp_path, every=1, count=1, ad_count=3)
    ch.tune_in()

    played = []
    for _ in range(6):
        ad = ch.advance()
        assert ad.is_commercial is True
        played.append(ad.path)
        assert ch.advance().is_commercial is False

    pool = set(played[:3])
    assert len(pool) == 3
    assert set(played[3:]) == pool
    assert played[2] != played[3]  # no repeat across the reshuffle boundary


def test_build_lineup_scans_one_global_commercial_pool(
    tmp_path, monkeypatch
):
    import nostalgiabox.channel as channel_mod

    ads = make_show(tmp_path, "commercials", 2)
    first = make_show(tmp_path, "first", 2)
    second = make_show(tmp_path, "second", 2)
    cfg = config_from_dict(
        {
            "shuffle_seed": 11,
            "commercials": {
                "enabled": True,
                "path": str(ads),
                "every": 1,
                "count": 1,
            },
            "channels": [
                {"number": 2, "name": "First", "path": str(first)},
                {"number": 4, "name": "Second", "path": str(second)},
            ],
        }
    )
    scanned_roots = []
    original_scan = channel_mod.scan_episodes

    def recording_scan(root, *args, **kwargs):
        scanned_roots.append(root)
        return original_scan(root, *args, **kwargs)

    monkeypatch.setattr(channel_mod, "scan_episodes", recording_scan)
    first_channel, second_channel = list(build_lineup(cfg))

    assert scanned_roots.count(ads) == 1
    assert first_channel._commercial_bag is second_channel._commercial_bag
    first_channel.tune_in()
    second_channel.tune_in()
    assert first_channel.advance().path != second_channel.advance().path


def test_commercial_order_and_range_are_deterministic_with_injected_rng(tmp_path):
    ads = make_show(tmp_path, "commercials", 5)
    show = make_show(tmp_path, "arthur", 3)
    cfg = config_from_dict(
        {
            "commercials": {
                "enabled": True,
                "path": str(ads),
                "every": 1,
                "count": [1, 3],
            },
            "channels": [
                {"number": 3, "name": "Arthur", "path": str(show)}
            ],
        }
    )

    def commercial_sequence(seed):
        channel = build_lineup(cfg, rng=random.Random(seed)).current
        channel.tune_in()
        sequence = []
        for _ in range(8):
            request = channel.advance()
            current_break = []
            while request.is_commercial:
                current_break.append(request.path.name)
                request = channel.advance()
            sequence.append(current_break)
        return sequence

    assert commercial_sequence(23) == commercial_sequence(23)


def test_commercial_does_not_replace_resume_position(tmp_path):
    ch = _commercial_channel(tmp_path, every=1, count=2, tune_in="resume")
    episode = ch.tune_in()
    ch.remember(episode.path, 123.5)

    ad = ch.advance()
    assert ad.is_commercial is True
    ch.remember(ad.path, 9.0)

    resumed = ch.tune_in()
    assert resumed.path == episode.path
    assert resumed.start == 123.5


def test_broadcast_mode_returns_to_wall_clock_schedule_after_ads(
    tmp_path, monkeypatch
):
    import nostalgiabox.channel as channel_mod

    monkeypatch.setattr(channel_mod, "probe_duration", lambda path: 60.0)
    ch = _commercial_channel(tmp_path, every=1, count=3, tune_in="broadcast")
    initial = ch.tune_in(now=0.0)
    assert initial.is_commercial is False
    assert ch.advance().is_commercial is True
    assert ch.advance().is_commercial is True
    assert ch.advance().is_commercial is True
    monkeypatch.setattr(channel_mod.time, "time", lambda: 30.0)
    returned = ch.advance()
    assert returned.is_commercial is False
    assert returned.path == initial.path
    assert returned.start == 30.0


def test_lineup_navigation(tmp_path):
    for n in ("a", "b", "c"):
        make_show(tmp_path, n, 1)
    cfg = config_from_dict(
        {
            "shuffle_seed": 1,
            "channels": [
                {"number": 2, "name": "A", "path": str(tmp_path / "a")},
                {"number": 4, "name": "B", "path": str(tmp_path / "b")},
                {"number": 7, "name": "C", "path": str(tmp_path / "c")},
            ],
        }
    )
    lineup = build_lineup(cfg)
    assert lineup.numbers == [2, 4, 7]
    assert lineup.current.number == 2
    assert lineup.up().number == 4
    assert lineup.up().number == 7
    assert lineup.up().number == 2  # wraps
    assert lineup.down().number == 7  # wraps back
    assert lineup.select_number(4).number == 4
    assert lineup.select_number(99) is None
    assert lineup.has_number(7)


def test_lineup_sorted_by_number(tmp_path):
    for n in ("a", "b"):
        make_show(tmp_path, n, 1)
    cfg = config_from_dict(
        {
            "channels": [
                {"number": 9, "name": "Nine", "path": str(tmp_path / "a")},
                {"number": 3, "name": "Three", "path": str(tmp_path / "b")},
            ]
        }
    )
    lineup = build_lineup(cfg)
    assert lineup.numbers == [3, 9]
