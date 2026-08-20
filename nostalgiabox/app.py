"""The television itself: the state machine that ties everything together.

:class:`TVApp` owns the channel lineup, the player, the overlays and the input
queue, and turns remote-control actions into TV behaviour: changing channels
(with a burst of static and a channel banner), adjusting and muting the volume,
direct channel entry by number, an info banner, a "last channel" jump, and a
standby/off mode. When an episode ends it automatically rolls into the next one
on that channel's shuffle, so the box never stops "broadcasting".

The class is written to be testable without a display: pass it a
:class:`~nostalgiabox.player.MockPlayer` and a fake clock and you can single-step
the whole thing (see ``step`` / ``handle_event`` / ``process_pending``).
"""

from __future__ import annotations

import logging
import queue
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

from .actions import Action, InputEvent
from .channel import Channel, ChannelLineup, PlayRequest, build_lineup
from .config import Config
from .input.manager import InputManager, create_backends
from .overlay import OverlayManager
from .player import END_EOF, END_ERROR, MockPlayer, Player
from .state import ResumeEntry, ResumeStateStore
from .static_gen import (
    COLORBARS_FILENAME,
    DEFAULT_ASSETS_DIR,
    glitch_filename,
    static_filename,
)

log = logging.getLogger(__name__)


class TVApp:
    """The retro-TV application state machine."""

    def __init__(
        self,
        config: Config,
        player: Player,
        input_manager: InputManager,
        *,
        overlay: Optional[OverlayManager] = None,
        clock: Callable[[], float] = time.monotonic,
        assets_dir: Optional[Path] = None,
        resume_store: Optional[ResumeStateStore] = None,
    ) -> None:
        self.config = config
        self.player = player
        self.input = input_manager
        self.overlay = overlay or OverlayManager(player, config, clock=clock)
        self._clock = clock

        self._resume_store: Optional[ResumeStateStore] = None
        resume_state = None
        if config.tune_in == "resume":
            self._resume_store = resume_store or ResumeStateStore(
                config.state_dir, clock=clock
            )
            resume_state = self._resume_store.load()

        self.lineup: ChannelLineup = build_lineup(config)
        if resume_state is not None:
            self._restore_resume_state(resume_state)

        # Runtime state.
        self.volume = config.initial_volume
        self.muted = False
        self.standby = False
        self.powered_off = False
        self._playing_path: Optional[Path] = None
        self._playing_is_commercial = False
        self._playing_channel_number: Optional[int] = None
        self._resume_capture_after = 0.0
        self._last_channel_number: Optional[int] = None
        self._running = False

        # Direct channel entry ("type 1 then 2 -> channel 12").
        self._digit_buffer = ""
        self._digit_deadline = 0.0
        self._digit_entry_timeout = 2.0

        # Pending "bridge" switch: keep the old show playing until this deadline,
        # then cut to the channel that was preloaded. The channel banner is shown
        # at the moment of the cut-over, not when the button is pressed.
        self._switch_deadline: Optional[float] = None
        self._pending_banner: Optional[tuple[int, str]] = None
        self._pending_request: Optional[PlayRequest] = None
        self._pending_channel_number: Optional[int] = None

        # Playback-finished events from the player (may arrive on any thread).
        self._ended: "queue.Queue[str]" = queue.Queue()
        self.player.on_end = self._ended.put

        # Filler assets.
        self._assets_dir = assets_dir or config.assets_dir or DEFAULT_ASSETS_DIR
        self._colorbars_path = self._resolve_asset(COLORBARS_FILENAME)
        # The channel-change transition clip depends on the configured effect.
        self._transition_path = self._resolve_transition_asset()

    # -- construction -------------------------------------------------------
    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        player: Optional[Player] = None,
        input_manager: Optional[InputManager] = None,
        dry_run: bool = False,
        assets_dir: Optional[Path] = None,
    ) -> "TVApp":
        """Build a fully wired app, creating real hardware backends by default.

        ``dry_run`` swaps in a :class:`MockPlayer` and disables all real input
        backends (a stdin backend is added if a TTY is available), which is how
        the box can be exercised on a development machine.
        """
        if player is None:
            if dry_run:
                player = MockPlayer(verbose=True)
            else:
                from .crt import write_shader
                from .player import MpvPlayer

                assets = assets_dir or config.assets_dir or DEFAULT_ASSETS_DIR
                shader_path = write_shader(config.crt)
                player = MpvPlayer(
                    glsl_shaders=str(shader_path) if shader_path else None,
                    fonts_dir=assets / "fonts",
                    force_4_3=config.force_4_3,
                    audio_device=config.audio_device,
                )

        if input_manager is None:
            if dry_run:
                backends = create_backends({"keyboard": False, "cec": False, "stdin": True})
            else:
                backends = create_backends(config.input_options)
            input_manager = InputManager(backends)

        return cls(config, player, input_manager, assets_dir=assets_dir)

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        """Power on: set volume, start input, and tune to the first channel."""
        self.player.set_volume(self.volume)
        self.player.set_mute(self.muted)
        self.input.start()
        self._select_start_channel()
        self.tune_current(show_static=False)

    def run(self) -> None:
        """Run the blocking main loop until a QUIT action is received."""
        previous_sigterm = None
        self._running = True
        try:
            # systemd stops the service with SIGTERM. Turn that into an orderly
            # loop exit so the final resume position is atomically flushed.
            previous_sigterm = signal.signal(signal.SIGTERM, self._handle_sigterm)
        except (OSError, ValueError):  # not the main thread / unsupported host
            pass
        try:
            self.start()
            log.info("NostalgiaBox is on the air. %d channels.", len(self.lineup))
            while self._running:
                self.step(block=True)
        except KeyboardInterrupt:  # pragma: no cover - interactive convenience
            log.info("interrupted; shutting down")
        finally:
            if previous_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, previous_sigterm)
                except (OSError, ValueError):
                    pass
            self.shutdown()

    def _handle_sigterm(self, _signum, _frame) -> None:
        log.info("termination requested; shutting down")
        self._running = False

    def shutdown(self) -> None:
        self._running = False
        self._flush_resume_position()
        try:
            self.overlay.clear_all()
        except Exception:  # noqa: BLE001
            pass
        self.input.stop()
        self.player.close()

    # -- main-loop step (small and testable) --------------------------------
    def step(self, *, block: bool = False, timeout: float = 0.1) -> None:
        """Advance the state machine by one iteration.

        Handles overlay expiry, channel-entry timeouts, finished episodes, and
        at most one queued input event.
        """
        now = self._clock()
        self.overlay.tick()
        self._maybe_commit_switch(now)
        self._maybe_commit_digits(now)
        self._drain_playback_events()

        event = self.input.get(timeout=timeout if block else 0.0)
        if event is not None:
            self.handle_event(event)

    def _maybe_commit_switch(self, now: float) -> None:
        """Cut over to the preloaded channel once the bridge window has elapsed."""
        if self._switch_deadline is not None and now >= self._switch_deadline:
            self._switch_deadline = None
            self.player.commit_switch()
            if self._pending_request is not None:
                self._playing_path = self._pending_request.path
                self._playing_is_commercial = self._pending_request.is_commercial
                self._playing_channel_number = self._pending_channel_number
                self._resume_capture_after = now
            self._pending_request = None
            self._pending_channel_number = None
            # Flash the channel banner right as the picture actually changes.
            if self._pending_banner is not None:
                self.overlay.show_channel_bug(*self._pending_banner)
                self._pending_banner = None

    # -- input handling -----------------------------------------------------
    def handle_event(self, event: InputEvent) -> None:
        action = event.action

        if action == Action.QUIT:
            self._running = False
            return
        if action == Action.POWER:
            self._toggle_standby()
            return

        # While in standby, ignore everything except POWER/QUIT (handled above).
        if self.standby:
            return

        handlers = {
            Action.CHANNEL_UP: self._channel_up,
            Action.CHANNEL_DOWN: self._channel_down,
            Action.VOLUME_UP: self._volume_up,
            Action.VOLUME_DOWN: self._volume_down,
            Action.MUTE: self._toggle_mute,
            Action.INFO: self._show_info,
            Action.LAST_CHANNEL: self._jump_last_channel,
            Action.ENTER: self._confirm_digits,
        }
        if action == Action.DIGIT:
            self._push_digit(event.value or 0)
        else:
            handler = handlers.get(action)
            if handler is not None:
                handler()

    # -- channel changing ---------------------------------------------------
    def _channel_up(self) -> None:
        self._remember_position()
        self._last_channel_number = self.lineup.current.number
        self.lineup.up()
        self.tune_current()

    def _channel_down(self) -> None:
        self._remember_position()
        self._last_channel_number = self.lineup.current.number
        self.lineup.down()
        self.tune_current()

    def _jump_last_channel(self) -> None:
        if self._last_channel_number is None:
            return
        target = self._last_channel_number
        if not self.lineup.has_number(target):
            return
        self._remember_position()
        self._last_channel_number = self.lineup.current.number
        self.lineup.select_number(target)
        self.tune_current()

    def select_channel_number(self, number: int) -> bool:
        """Tune directly to a channel number. Returns False if it doesn't exist."""
        if not self.lineup.has_number(number):
            self.overlay.show_message(f"CH {number:02d}  -  NO CHANNEL")
            return False
        if number == self.lineup.current.number:
            self._show_info()
            return True
        self._remember_position()
        self._last_channel_number = self.lineup.current.number
        self.lineup.select_number(number)
        self.tune_current()
        return True

    def tune_current(self, *, show_static: bool = True) -> None:
        """Tune into the currently selected channel."""
        channel = self.lineup.current
        self.overlay.clear_standby()

        request = channel.tune_in()
        self._pending_banner = None

        if request is None:
            # No episodes on this channel: show the "no signal" screen.
            self.overlay.show_channel_bug(channel.number, channel.name)
            self._show_no_signal(channel)
            return

        self._show_network_bug()

        if not show_static:
            # Not a channel change (first tune / waking from standby): play now.
            self._switch_deadline = None
            self._pending_request = None
            self._pending_channel_number = None
            self.overlay.show_channel_bug(channel.number, channel.name)
            self._play_request(request)
        elif self._transition_path is not None:
            # Transition clip (glitch/static) + preloaded episode.
            self._switch_deadline = None
            self._pending_request = None
            self._pending_channel_number = None
            self.overlay.show_channel_bug(channel.number, channel.name)
            self._playing_path = request.path
            self._playing_is_commercial = request.is_commercial
            self._playing_channel_number = channel.number
            # During the transition mpv is still showing the filler clip; do
            # not save its time position under the target episode.
            self._resume_capture_after = self._clock() + self.config.transition_duration
            self.player.play_transition(
                self._transition_path,
                request.path,
                start=request.start,
                static_seconds=self.config.transition_duration,
            )
        elif self.config.bridge_seconds > 0 and self._playing_path is not None:
            # No transition effect: keep the current show playing while the next
            # channel preloads, then cut over (no frozen frame). The banner is
            # shown at the cut-over (see _maybe_commit_switch), not right now.
            self.player.preload_next(request.path, start=request.start)
            self._switch_deadline = self._clock() + self.config.bridge_seconds
            self._pending_banner = (channel.number, channel.name)
            self._pending_request = request
            self._pending_channel_number = channel.number
        else:
            self._switch_deadline = None
            self._pending_request = None
            self._pending_channel_number = None
            self.overlay.show_channel_bug(channel.number, channel.name)
            self._play_request(request)

    def _play_request(self, request: PlayRequest) -> None:
        self._playing_path = request.path
        self._playing_is_commercial = request.is_commercial
        self._playing_channel_number = self.lineup.current.number
        self._resume_capture_after = self._clock()
        if request.is_commercial:
            log.info("commercial break: playing %s", request.path)
        self.player.play(request.path, start=request.start)

    def _show_no_signal(self, channel: Channel) -> None:
        self._switch_deadline = None
        self._pending_banner = None
        self._pending_request = None
        self._pending_channel_number = None
        self._playing_path = None
        self._playing_is_commercial = False
        self._playing_channel_number = None
        if self.config.network_bug.enabled:
            self.overlay.hide_bug()
        if self._colorbars_path is not None:
            self.player.play_loop(self._colorbars_path)
        else:
            self.player.stop()
        self.overlay.show_message(
            f"CH {channel.number:02d}  {channel.name}  -  NO SIGNAL", duration=6.0
        )

    # -- volume -------------------------------------------------------------
    def _volume_up(self) -> None:
        self._set_volume(self.volume + self.config.volume_step, unmute=True)

    def _volume_down(self) -> None:
        # One press below zero cleanly powers off the box (safe to unplug).
        if self.config.power_off_on_min_volume and not self.muted and self.volume <= 0:
            self._power_off()
            return
        self._set_volume(self.volume - self.config.volume_step, unmute=True)

    def _set_volume(self, value: int, *, unmute: bool = False) -> None:
        self.volume = max(0, min(100, value))
        if unmute and self.muted:
            self.muted = False
            self.player.set_mute(False)
        self.player.set_volume(self.volume)
        self.overlay.show_volume(self.volume, self.muted)

    def _power_off(self) -> None:
        """Cleanly shut the Pi down so it's safe to unplug."""
        log.info("powering off (volume floor)")
        self.powered_off = True
        # Capture state before player.stop() clears the time position and before
        # the OS power-off command can terminate this process.
        self._flush_resume_position()
        self._switch_deadline = None
        self._pending_banner = None
        self._pending_request = None
        self._pending_channel_number = None
        try:
            self.overlay.clear_all()
            self.overlay.show_message("GOODBYE", duration=0)
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass
        self._run_power_off_command()
        self._running = False  # exit the main loop

    def _run_power_off_command(self) -> None:
        command = list(self.config.power_off_command)
        if not command:
            return  # disabled / test mode
        try:
            subprocess.Popen(command)
        except Exception:  # noqa: BLE001
            log.exception("power-off command failed: %s", command)

    def _toggle_mute(self) -> None:
        self.muted = not self.muted
        self.player.set_mute(self.muted)
        self.overlay.show_volume(self.volume, self.muted)

    # -- info / standby -----------------------------------------------------
    def _show_info(self) -> None:
        channel = self.lineup.current
        self.overlay.show_channel_bug(channel.number, channel.name)

    def _toggle_standby(self) -> None:
        self.standby = not self.standby
        if self.standby:
            self._remember_position()
            self._switch_deadline = None
            self._pending_banner = None
            self._pending_request = None
            self._pending_channel_number = None
            self.player.stop()
            self.overlay.clear_all()
            self.overlay.show_standby()
        else:
            self.overlay.clear_standby()
            self.tune_current(show_static=False)

    # -- direct channel entry ----------------------------------------------
    def _push_digit(self, digit: int) -> None:
        self._digit_buffer = (self._digit_buffer + str(digit))[-3:]
        self._digit_deadline = self._clock() + self._digit_entry_timeout
        self.overlay.show_message(f"CH {self._digit_buffer}_", duration=self._digit_entry_timeout)

    def _confirm_digits(self) -> None:
        if not self._digit_buffer:
            return
        number = int(self._digit_buffer)
        self._digit_buffer = ""
        self._digit_deadline = 0.0
        self.select_channel_number(number)

    def _maybe_commit_digits(self, now: float) -> None:
        if self._digit_buffer and now >= self._digit_deadline:
            self._confirm_digits()

    # -- playback-finished handling ----------------------------------------
    def _drain_playback_events(self) -> None:
        # Snapshot the events that existed before advancing. A failed play can
        # synchronously queue END_ERROR; leaving that new event for the next
        # loop iteration prevents it from being coalesced away and freezing TV.
        reasons = []
        while True:
            try:
                reasons.append(self._ended.get_nowait())
            except queue.Empty:
                break
        if (
            any(reason in (END_EOF, END_ERROR) for reason in reasons)
            and not self.standby
        ):
            self._advance_current()

    def _advance_current(self) -> None:
        request = self.lineup.current.advance()
        if request is None:
            self._show_no_signal(self.lineup.current)
        else:
            self._play_request(request)

    # -- helpers ------------------------------------------------------------
    def _remember_position(self) -> None:
        if (
            self.config.tune_in != "resume"
            or self._playing_path is None
            or self._playing_is_commercial
            or self._playing_channel_number is None
            or self._clock() < self._resume_capture_after
        ):
            return
        pos = self.player.get_time_pos()
        if pos is not None:
            channel = next(
                (
                    item
                    for item in self.lineup
                    if item.number == self._playing_channel_number
                ),
                None,
            )
            if channel is None or self._playing_path not in channel.episodes:
                return
            channel.remember(self._playing_path, pos)
            if self._resume_store is not None:
                self._resume_store.remember(channel.number, self._playing_path, pos)

    def _flush_resume_position(self) -> None:
        """Capture and force-write resume state before a clean exit."""
        self._remember_position()
        if self._resume_store is not None:
            self._resume_store.flush(force=True)

    def _show_network_bug(self) -> None:
        # Avoid even an extra overlay-clear command when the feature is off so
        # the default playback path remains identical to upstream.
        if self.config.network_bug.enabled:
            self.overlay.show_bug()

    def _restore_resume_state(self, resume_state: Mapping[int, ResumeEntry]) -> None:
        """Apply valid saved positions as the lineup is initialized."""
        for channel in self.lineup:
            entry = resume_state.get(channel.number)
            if entry is None:
                continue
            # A removed/moved episode must never strand the channel on a stale
            # path; simply fall back to its normal tune-in selection.
            if entry.path in channel.episodes and entry.path.is_file():
                channel.remember(entry.path, entry.position)
            else:
                log.info(
                    "ignoring stale resume entry for channel %s: %s",
                    channel.number,
                    entry.path,
                )

    def _select_start_channel(self) -> None:
        if self.config.start_channel is not None and self.lineup.has_number(
            self.config.start_channel
        ):
            self.lineup.select_number(self.config.start_channel)

    def _resolve_asset(self, filename: str) -> Optional[Path]:
        path = self._assets_dir / filename
        return path if path.is_file() else None

    def _resolve_transition_asset(self) -> Optional[Path]:
        effect = self.config.transition_effect
        if effect == "none":
            return None
        filename = (
            glitch_filename(self.config.static_audio)
            if effect == "glitch"
            else static_filename(self.config.static_audio)
        )
        return self._resolve_asset(filename)


def run_from_config(
    config: Config,
    *,
    dry_run: bool = False,
    demo_ends: int = 0,
) -> None:
    """Convenience entry point used by the CLI."""
    app = TVApp.from_config(config, dry_run=dry_run)
    if demo_ends:
        if not dry_run or not isinstance(app.player, MockPlayer):
            raise RuntimeError("demo playback endings require --dry-run")
        app.start()
        try:
            for _ in range(demo_ends):
                app.player.finish_current(END_EOF)
                app._drain_playback_events()
        finally:
            app.shutdown()
    else:
        app.run()


__all__ = ["TVApp", "run_from_config"]
