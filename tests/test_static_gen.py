from pathlib import Path

from nostalgiabox import static_gen
from nostalgiabox import __main__ as cli


def _capture_command(monkeypatch):
    commands = []
    monkeypatch.setattr(static_gen, "_run", commands.append)
    return commands


def test_silent_static_command_is_unchanged(monkeypatch, tmp_path):
    commands = _capture_command(monkeypatch)
    out_path = tmp_path / "static.mp4"

    static_gen.generate_static(out_path)

    assert commands == [[
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "nullsrc=s=1280x720:r=25:d=1.0",
        "-vf", "geq=lum='random(1)*255':cb=128:cr=128,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]]


def test_silent_glitch_command_is_unchanged(monkeypatch, tmp_path):
    commands = _capture_command(monkeypatch)
    out_path = tmp_path / "glitch.mp4"

    static_gen.generate_glitch(out_path)

    assert commands == [[
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "nullsrc=s=96x54:r=25:d=0.6",
        "-vf", (
            "geq=r='random(1)*255':g='random(2)*255':b='random(3)*255',"
            "scale=1280:720:flags=neighbor,format=yuv420p"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-an",
        str(out_path),
    ]]


def test_static_audio_adds_quiet_white_noise(monkeypatch, tmp_path):
    commands = _capture_command(monkeypatch)

    static_gen.generate_static(tmp_path / "static-audio.mp4", static_audio=True)

    cmd = commands[0]
    assert any("anoisesrc=color=white" in arg for arg in cmd)
    assert cmd[cmd.index("-af") + 1] == "volume=-18dB"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-shortest" in cmd
    assert "-an" not in cmd


def test_glitch_audio_adds_a_short_noise_burst(monkeypatch, tmp_path):
    commands = _capture_command(monkeypatch)

    static_gen.generate_glitch(
        tmp_path / "glitch-audio.mp4",
        duration=0.25,
        static_audio=True,
    )

    cmd = commands[0]
    assert "anoisesrc=color=white:sample_rate=48000:duration=0.25" in cmd
    assert "volume=-18dB" in cmd
    assert "-an" not in cmd


def test_audio_mode_uses_distinct_cache_filenames():
    assert static_gen.static_filename(False) == static_gen.STATIC_FILENAME
    assert static_gen.static_filename(True) == static_gen.STATIC_AUDIO_FILENAME
    assert static_gen.static_filename(False) != static_gen.static_filename(True)
    assert static_gen.glitch_filename(False) == static_gen.GLITCH_FILENAME
    assert static_gen.glitch_filename(True) == static_gen.GLITCH_AUDIO_FILENAME
    assert static_gen.glitch_filename(False) != static_gen.glitch_filename(True)


def test_generate_all_threads_audio_mode_to_generators(monkeypatch, tmp_path):
    generated = []

    def fake_static(path: Path, *, static_audio=False):
        generated.append(("static", path, static_audio))
        return path

    def fake_glitch(path: Path, *, static_audio=False):
        generated.append(("glitch", path, static_audio))
        return path

    def fake_bars(path: Path):
        generated.append(("bars", path, None))
        return path

    monkeypatch.setattr(static_gen, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(static_gen, "generate_static", fake_static)
    monkeypatch.setattr(static_gen, "generate_glitch", fake_glitch)
    monkeypatch.setattr(static_gen, "generate_color_bars", fake_bars)

    results = static_gen.generate_all(tmp_path, static_audio=True)

    assert results == [
        tmp_path / static_gen.STATIC_AUDIO_FILENAME,
        tmp_path / static_gen.GLITCH_AUDIO_FILENAME,
        tmp_path / static_gen.COLORBARS_FILENAME,
    ]
    assert generated == [
        ("static", tmp_path / static_gen.STATIC_AUDIO_FILENAME, True),
        ("glitch", tmp_path / static_gen.GLITCH_AUDIO_FILENAME, True),
        ("bars", tmp_path / static_gen.COLORBARS_FILENAME, None),
    ]


def test_top_level_asset_generation_uses_config_audio_mode(monkeypatch, tmp_path):
    show = tmp_path / "show"
    show.mkdir()
    assets = tmp_path / "assets"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "channels:",
                f"  - path: {show}",
                f"assets_dir: {assets}",
                "static_audio: true",
            ]
        )
    )
    calls = []
    monkeypatch.setattr(static_gen, "main", lambda argv: calls.append(argv) or 0)

    assert cli._generate_assets(str(config_path)) == 0
    assert calls == [["--assets-dir", str(assets), "--static-audio"]]
