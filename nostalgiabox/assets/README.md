# Generated assets

This folder holds the short filler clips the TV uses:

- `static.mp4` — analog "snow" shown briefly when changing channels.
- `glitch.mp4` — digital corruption used by the glitch transition.
- `static-audio.mp4` / `glitch-audio.mp4` — cache-distinct versions with
  analog hiss, generated when `static_audio: true`.
- `colorbars.mp4` — SMPTE colour bars / "no signal" screen for empty channels.

These are **generated with ffmpeg**, not committed to git. Create them with:

```bash
nostalgiabox --generate-assets --config config.yaml
# or
python -m nostalgiabox.static_gen --static-audio  # when hiss is enabled
```

`scripts/install.sh` runs this for you during setup. If the files are missing at
runtime the box still works — channel changes just skip the static burst and
empty channels fall back to a plain "STANDBY" screen.
