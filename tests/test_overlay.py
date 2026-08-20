import re

from nostalgiabox.config import config_from_dict
from nostalgiabox.overlay import OverlayManager
from nostalgiabox.player import MockPlayer
from tests.helpers import FakeClock, make_show

# The 4:3 frame within the 1280x720 canvas spans x in [160, 1120].
_FRAME_X0, _FRAME_X1 = 160, 1120


def _all_x_positions(ass: str):
    return [int(m) for m in re.findall(r"\\pos\((\d+),", ass)]


def _position(ass: str):
    match = re.search(r"\\pos\((\d+),(\d+)\)", ass)
    assert match is not None
    return int(match.group(1)), int(match.group(2))


def _config(tmp_path):
    make_show(tmp_path, "a", 1)
    return config_from_dict(
        {
            "channel_bug_seconds": 4,
            "osd_duration": 2,
            "channels": [{"number": 3, "name": "Arthur", "path": str(tmp_path / "a")}],
        }
    )


def _bug_config(tmp_path, *, corner="bottom-right", opacity=0.75, text="DTG"):
    make_show(tmp_path, "a", 1)
    return config_from_dict(
        {
            "channels": [
                {"number": 3, "name": "Arthur", "path": str(tmp_path / "a")}
            ],
            "network_bug": {
                "enabled": True,
                "text": text,
                "corner": corner,
                "opacity": opacity,
            },
        }
    )


def test_channel_bug_drawn_and_expires(tmp_path):
    clock = FakeClock()
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=clock)

    om.show_channel_bug(3, "Arthur")
    assert 1 in player.overlays  # channel overlay id
    ass = player.overlays[1]
    assert "CH 03" in ass and "Arthur" in ass

    clock.advance(3.9)
    om.tick()
    assert 1 in player.overlays  # not yet expired

    clock.advance(0.2)
    om.tick()
    assert 1 not in player.overlays  # expired after 4s


def test_volume_overlay_has_label_and_bars(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_volume(45, muted=False)
    ass = player.overlays[2]
    assert "Volume" in ass
    # 20 segments: some drawn as bars (rectangles start "m 0 0 l"), rest as dots.
    assert ass.count("\\p1") == 20


def test_volume_bars_scale_with_level(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_volume(100, muted=False)
    full = player.overlays[2].count("m 0 0 l")  # rectangle (filled bar) count
    om.show_volume(0, muted=False)
    empty = player.overlays[2].count("m 0 0 l")
    assert full == 20 and empty == 0


def test_muted_volume_overlay(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_volume(45, muted=True)
    assert "Mute" in player.overlays[2]


def test_standby_overlay_does_not_expire(tmp_path):
    clock = FakeClock()
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=clock)
    om.show_standby()
    clock.advance(1000)
    om.tick()
    assert 3 in player.overlays  # standby id persists
    om.clear_standby()
    assert 3 not in player.overlays


def test_channel_name_with_braces_is_escaped(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_channel_bug(5, "Weird{name}")
    # Braces in the name must be neutralised (they delimit ASS override blocks).
    ass = player.overlays[1]
    assert "Weird(name)" in ass
    assert "Weird{name}" not in ass


def test_message_overlay(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_message("CH 12  -  NO CHANNEL")
    assert "NO CHANNEL" in player.overlays[4]


def test_channel_bug_sits_inside_4x3_frame(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_channel_bug(3, "Arthur")
    xs = _all_x_positions(player.overlays[1])
    assert xs and all(_FRAME_X0 <= x <= _FRAME_X1 for x in xs)


def test_volume_bar_sits_inside_4x3_frame(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_volume(100, muted=False)  # widest case: all 20 bars drawn
    xs = _all_x_positions(player.overlays[2])
    assert xs and all(_FRAME_X0 <= x <= _FRAME_X1 for x in xs)


def test_overlay_uses_configured_font_and_color(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_channel_bug(3, "Arthur")
    ass = player.overlays[1]
    assert "\\fnVT323" in ass          # bundled retro font
    assert "&H005AFF4D" in ass         # #4DFF5A -> ASS BBGGRR


def test_network_bug_is_persistent_and_can_be_hidden(tmp_path):
    clock = FakeClock()
    player = MockPlayer()
    om = OverlayManager(player, _bug_config(tmp_path), clock=clock)

    om.show_bug()
    assert 5 in player.overlays
    assert "DTG" in player.overlays[5]

    clock.advance(1000)
    om.tick()
    assert 5 in player.overlays

    om.hide_bug()
    assert 5 not in player.overlays


def test_disabled_network_bug_is_not_drawn(tmp_path):
    player = MockPlayer()
    om = OverlayManager(player, _config(tmp_path), clock=FakeClock())
    om.show_bug()
    assert 5 not in player.overlays


def test_network_bug_corners_stay_inside_4x3_safe_area(tmp_path):
    expected = {
        "top-left": (7, 217, 43),
        "top-right": (9, 1063, 43),
        "bottom-left": (1, 217, 677),
        "bottom-right": (3, 1063, 677),
    }
    for corner, (alignment, x, y) in expected.items():
        player = MockPlayer()
        om = OverlayManager(
            player,
            _bug_config(tmp_path, corner=corner),
            clock=FakeClock(),
        )
        om.show_bug()
        ass = player.overlays[5]
        assert rf"\an{alignment}" in ass
        assert _position(ass) == (x, y)
        assert _FRAME_X0 <= x <= _FRAME_X1
        assert 0 <= y <= 720


def test_network_bug_uses_ui_style_opacity_and_escaping(tmp_path):
    player = MockPlayer()
    om = OverlayManager(
        player,
        _bug_config(tmp_path, opacity=0.75, text=r"D{T}G\\"),
        clock=FakeClock(),
    )
    om.show_bug()
    ass = player.overlays[5]
    assert "\\fnVT323" in ass
    assert "&H405AFF4D" in ass       # #4DFF5A plus 25% ASS transparency
    assert "\\alpha&H40&" in ass
    assert "D(T)G" in ass
    assert "D{T}G" not in ass
