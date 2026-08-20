from pathlib import Path

import pytest

from nostalgiabox.config import (
    ConfigError,
    config_from_dict,
    load_config,
)
from tests.helpers import make_show


def test_explicit_channels(tmp_path):
    make_show(tmp_path, "dragon-tales", 3)
    make_show(tmp_path, "arthur", 2)
    data = {
        "channels": [
            {"number": 2, "name": "Dragon Tales", "path": str(tmp_path / "dragon-tales")},
            {"number": 3, "name": "Arthur", "path": str(tmp_path / "arthur")},
        ]
    }
    cfg = config_from_dict(data)
    assert cfg.channel_numbers() == [2, 3]
    assert cfg.channels[0].name == "Dragon Tales"
    assert cfg.tune_in == "random"  # default


def test_channel_number_and_name_defaults(tmp_path):
    make_show(tmp_path, "magic_school_bus", 1)
    data = {"channels": [{"path": str(tmp_path / "magic_school_bus")}]}
    cfg = config_from_dict(data)
    # number defaults to index+2, name derived + prettified from folder
    assert cfg.channels[0].number == 2
    assert cfg.channels[0].name == "Magic School Bus"


def test_media_root_autodiscovery(tmp_path):
    make_show(tmp_path, "arthur", 1)
    make_show(tmp_path, "rugrats", 1)
    make_show(tmp_path, "dragon tales", 1)
    (tmp_path / ".hidden").mkdir()
    cfg = config_from_dict({"media_root": str(tmp_path)})
    # alphabetical order, numbered from 2, hidden folder ignored
    assert [(c.number, c.name) for c in cfg.channels] == [
        (2, "Arthur"),
        (3, "Dragon Tales"),
        (4, "Rugrats"),
    ]


def test_autodiscovery_custom_first_number(tmp_path):
    make_show(tmp_path, "arthur", 1)
    cfg = config_from_dict({"media_root": str(tmp_path), "first_channel_number": 7})
    assert cfg.channels[0].number == 7


def test_duplicate_channel_numbers_rejected(tmp_path):
    make_show(tmp_path, "a", 1)
    make_show(tmp_path, "b", 1)
    data = {
        "channels": [
            {"number": 5, "name": "A", "path": str(tmp_path / "a")},
            {"number": 5, "name": "B", "path": str(tmp_path / "b")},
        ]
    }
    with pytest.raises(ConfigError, match="duplicate channel number"):
        config_from_dict(data)


def test_missing_channels_and_media_root():
    with pytest.raises(ConfigError, match="either 'channels' or 'media_root'"):
        config_from_dict({})


def test_bad_tune_in_mode(tmp_path):
    make_show(tmp_path, "a", 1)
    data = {"tune_in": "nonsense", "channels": [{"path": str(tmp_path / "a")}]}
    with pytest.raises(ConfigError, match="tune_in"):
        config_from_dict(data)


def test_volume_and_durations_clamped(tmp_path):
    make_show(tmp_path, "a", 1)
    data = {
        "initial_volume": 500,
        "volume_step": 0,
        "transition_duration": -3,
        "channels": [{"path": str(tmp_path / "a")}],
    }
    cfg = config_from_dict(data)
    assert cfg.initial_volume == 100
    assert cfg.volume_step == 1
    assert cfg.transition_duration == 0.0


def test_video_extensions_normalised(tmp_path):
    make_show(tmp_path, "a", 1)
    data = {
        "video_extensions": ["mp4", ".MKV"],
        "channels": [{"path": str(tmp_path / "a")}],
    }
    cfg = config_from_dict(data)
    assert cfg.video_extensions == (".mp4", ".mkv")


def test_ui_and_crt_defaults(tmp_path):
    make_show(tmp_path, "a", 1)
    cfg = config_from_dict({"channels": [{"path": str(tmp_path / "a")}]})
    assert cfg.ui.font == "VT323"
    assert cfg.ui.color == "#4DFF5A"
    assert cfg.crt.enabled is True
    assert cfg.force_4_3 is False   # shows keep their native aspect by default
    assert cfg.start_offset_min == 6.0
    assert cfg.start_offset_max == 10.0
    assert cfg.transition_effect == "none"
    assert cfg.transition_duration == 0.4
    assert cfg.bridge_seconds == 0.8


def test_start_offset_forms(tmp_path):
    make_show(tmp_path, "a", 1)
    base = {"channels": [{"path": str(tmp_path / "a")}]}
    # single number -> min == max
    c1 = config_from_dict({**base, "start_offset": 8})
    assert (c1.start_offset_min, c1.start_offset_max) == (8.0, 8.0)
    # [min, max] list
    c2 = config_from_dict({**base, "start_offset": [6, 10]})
    assert (c2.start_offset_min, c2.start_offset_max) == (6.0, 10.0)
    # explicit keys, and min/max get ordered
    c3 = config_from_dict({**base, "start_offset_min": 10, "start_offset_max": 6})
    assert (c3.start_offset_min, c3.start_offset_max) == (10.0, 10.0)


def test_ui_and_crt_overrides(tmp_path):
    make_show(tmp_path, "a", 1)
    cfg = config_from_dict(
        {
            "channels": [{"path": str(tmp_path / "a")}],
            "ui": {"font": "Press Start 2P", "color": "00FF00", "glow": False},
            "crt": {"enabled": False, "curvature": 0.2, "scanlines": False},
        }
    )
    assert cfg.ui.font == "Press Start 2P"
    assert cfg.ui.color == "#00FF00"  # normalised with leading '#'
    assert cfg.ui.glow is False
    assert cfg.crt.enabled is False
    assert cfg.crt.curvature == 0.2
    assert cfg.crt.scanlines is False


def test_crt_values_clamped(tmp_path):
    make_show(tmp_path, "a", 1)
    cfg = config_from_dict(
        {
            "channels": [{"path": str(tmp_path / "a")}],
            "crt": {"curvature": 5.0, "vignette": -1},
        }
    )
    assert cfg.crt.curvature == 0.5   # clamped to max
    assert cfg.crt.vignette == 0.0    # clamped to min


def test_bad_transition_rejected(tmp_path):
    make_show(tmp_path, "a", 1)
    with pytest.raises(ConfigError, match="transition"):
        config_from_dict(
            {"channels": [{"path": str(tmp_path / "a")}], "transition": "sparkles"}
        )


def test_bad_color_rejected(tmp_path):
    make_show(tmp_path, "a", 1)
    with pytest.raises(ConfigError, match="ui.color"):
        config_from_dict(
            {"channels": [{"path": str(tmp_path / "a")}], "ui": {"color": "greenish"}}
        )


def test_load_config_from_file(tmp_path):
    make_show(tmp_path, "arthur", 1)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "channels:\n"
        f"  - path: {tmp_path / 'arthur'}\n"
        "    name: Arthur\n"
        "    number: 3\n"
        "tune_in: resume\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.tune_in == "resume"
    assert cfg.channels[0].number == 3


def test_load_config_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_relative_paths_resolved_against_config_dir(tmp_path):
    make_show(tmp_path, "arthur", 1)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("channels:\n  - path: arthur\n    name: Arthur\n")
    cfg = load_config(cfg_file)
    assert cfg.channels[0].path == tmp_path / "arthur"


def test_dtg_feature_defaults_are_off(tmp_path):
    make_show(tmp_path, "arthur", 1)
    cfg = config_from_dict({"channels": [{"path": str(tmp_path / "arthur")}]})

    assert cfg.commercials.enabled is False
    assert cfg.commercials.path is None
    assert cfg.commercials.every == 1
    assert cfg.commercials.count == (1, 3)
    assert cfg.channels[0].commercials is True
    assert cfg.network_bug.enabled is False
    assert cfg.network_bug.text == "DTG"
    assert cfg.network_bug.image is None
    assert cfg.network_bug.corner == "bottom-right"
    assert cfg.network_bug.opacity == 0.75
    assert cfg.static_audio is False
    assert cfg.state_dir == Path.home() / ".local" / "state" / "nostalgiabox"


def test_dtg_feature_overrides_and_relative_paths(tmp_path):
    make_show(tmp_path, "arthur", 1)
    cfg = config_from_dict(
        {
            "channels": [{"path": "arthur", "commercials": False}],
            "commercials": {
                "enabled": True,
                "path": "commercials",
                "every": 2,
                "count": [2, 4],
            },
            "network_bug": {
                "enabled": True,
                "text": "DTG 90",
                "image": "art/bug.png",
                "corner": "TOP-LEFT",
                "opacity": 0.5,
            },
            "static_audio": True,
            "state_dir": "state",
        },
        base_dir=tmp_path,
    )

    assert cfg.channels[0].commercials is False
    assert cfg.commercials.enabled is True
    assert cfg.commercials.path == tmp_path / "commercials"
    assert cfg.commercials.every == 2
    assert cfg.commercials.count == (2, 4)
    assert cfg.network_bug.enabled is True
    assert cfg.network_bug.text == "DTG 90"
    assert cfg.network_bug.image == tmp_path / "art" / "bug.png"
    assert cfg.network_bug.corner == "top-left"
    assert cfg.network_bug.opacity == 0.5
    assert cfg.static_audio is True
    assert cfg.state_dir == tmp_path / "state"


@pytest.mark.parametrize(
    ("raw_count", "expected"),
    [
        (2, (2, 2)),
        ([1, 3], (1, 3)),
        ((4, 6), (4, 6)),
    ],
)
def test_commercial_count_forms(tmp_path, raw_count, expected):
    make_show(tmp_path, "arthur", 1)
    cfg = config_from_dict(
        {
            "channels": [{"path": str(tmp_path / "arthur")}],
            "commercials": {"count": raw_count},
        }
    )
    assert cfg.commercials.count == expected


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"commercials": []}, "commercials.*mapping"),
        ({"commercials": {"enabled": "false"}}, "commercials.enabled"),
        ({"commercials": {"path": 123}}, "commercials.path"),
        ({"commercials": {"every": 0}}, "commercials.every"),
        ({"commercials": {"every": True}}, "commercials.every"),
        ({"commercials": {"count": []}}, "commercials.count"),
        ({"commercials": {"count": [0, 2]}}, "commercials.count"),
        ({"commercials": {"count": [3, 1]}}, "commercials.count"),
        ({"commercials": {"count": [1, 2.5]}}, "commercials.count"),
        ({"network_bug": []}, "network_bug.*mapping"),
        ({"network_bug": {"enabled": "true"}}, "network_bug.enabled"),
        ({"network_bug": {"text": 90}}, "network_bug.text"),
        ({"network_bug": {"image": 90}}, "network_bug.image"),
        ({"network_bug": {"corner": "center"}}, "network_bug.corner"),
        ({"network_bug": {"opacity": -0.1}}, "network_bug.opacity"),
        ({"network_bug": {"opacity": 1.1}}, "network_bug.opacity"),
        ({"network_bug": {"opacity": "0.5"}}, "network_bug.opacity"),
        ({"static_audio": "false"}, "static_audio"),
        ({"state_dir": 123}, "state_dir"),
    ],
)
def test_bad_dtg_config_rejected_with_friendly_error(tmp_path, extra, match):
    make_show(tmp_path, "arthur", 1)
    data = {"channels": [{"path": str(tmp_path / "arthur")}]}
    data.update(extra)
    with pytest.raises(ConfigError, match=match):
        config_from_dict(data)


def test_bad_channel_commercials_flag_rejected(tmp_path):
    make_show(tmp_path, "arthur", 1)
    with pytest.raises(ConfigError, match=r"channels\[0\]\.commercials"):
        config_from_dict(
            {
                "channels": [
                    {"path": str(tmp_path / "arthur"), "commercials": "false"}
                ]
            }
        )


def test_enabled_network_bug_requires_text_in_text_only_release(tmp_path):
    make_show(tmp_path, "arthur", 1)
    base = {"channels": [{"path": str(tmp_path / "arthur")}]}

    with pytest.raises(ConfigError, match="network_bug.text.*non-empty"):
        config_from_dict(
            {
                **base,
                "network_bug": {"enabled": True, "text": "   ", "image": None},
            }
        )

    with pytest.raises(ConfigError, match="PNG image rendering is not available"):
        config_from_dict(
            {
                **base,
                "network_bug": {"enabled": True, "text": "", "image": "bug.png"},
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize("raw_state_dir", [None])
def test_null_state_dir_uses_default(tmp_path, raw_state_dir):
    make_show(tmp_path, "arthur", 1)
    cfg = config_from_dict(
        {
            "channels": [{"path": str(tmp_path / "arthur")}],
            "state_dir": raw_state_dir,
        }
    )
    assert cfg.state_dir == Path.home() / ".local" / "state" / "nostalgiabox"
