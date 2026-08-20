"""Persistent resume positions for channels using ``tune_in: resume``.

The state file is intentionally tiny and dependency-free.  Updates are kept in
memory and written at most once per interval, then flushed on clean shutdown.
Writes use ``os.replace`` so a power loss cannot leave a half-written JSON file.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

log = logging.getLogger(__name__)

RESUME_STATE_FILENAME = "resume.json"
DEFAULT_WRITE_INTERVAL = 15.0


@dataclass(frozen=True)
class ResumeEntry:
    """One channel's last known playback position."""

    path: Path
    position: float
    saved_at: float


class ResumeStateStore:
    """Load and atomically persist per-channel resume entries."""

    def __init__(
        self,
        state_dir: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        write_interval: float = DEFAULT_WRITE_INTERVAL,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / RESUME_STATE_FILENAME
        self._clock = clock
        self._wall_clock = wall_clock
        self._write_interval = max(0.0, float(write_interval))
        self._entries: Dict[int, ResumeEntry] = {}
        self._dirty = False
        self._last_write: Optional[float] = None

    @property
    def entries(self) -> Mapping[int, ResumeEntry]:
        return dict(self._entries)

    def load(self) -> Mapping[int, ResumeEntry]:
        """Read valid entries, ignoring a missing or malformed state file."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._entries = _parse_entries(raw)
        except FileNotFoundError:
            self._entries = {}
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
            log.warning("ignoring corrupt resume state file: %s", self.path)
            self._entries = {}
        self._dirty = False
        return self.entries

    def remember(self, channel_number: int, path: Path, position: float) -> None:
        """Update an entry and write it if the throttle interval has elapsed."""
        position = max(0.0, float(position))
        if not math.isfinite(position):
            return
        self._entries[int(channel_number)] = ResumeEntry(
            path=Path(path),
            position=position,
            saved_at=float(self._wall_clock()),
        )
        self._dirty = True
        self.flush()

    def flush(self, *, force: bool = False) -> bool:
        """Persist dirty state, returning whether a write occurred."""
        if not self._dirty:
            return False
        now = float(self._clock())
        if (
            not force
            and self._last_write is not None
            and now - self._last_write < self._write_interval
        ):
            return False
        try:
            self._write_atomic()
        except OSError:
            log.warning("could not write resume state file: %s", self.path, exc_info=True)
            return False
        self._last_write = now
        self._dirty = False
        return True

    def _write_atomic(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            str(number): {
                "path": str(entry.path),
                "position": entry.position,
                "saved_at": entry.saved_at,
            }
            for number, entry in sorted(self._entries.items())
        }
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_dir,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass


def _parse_entries(raw: object) -> Dict[int, ResumeEntry]:
    if not isinstance(raw, dict):
        raise ValueError("resume state root must be an object")
    entries: Dict[int, ResumeEntry] = {}
    for raw_number, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            continue
        try:
            number = int(raw_number)
            raw_path = raw_entry["path"]
            position = float(raw_entry["position"])
            saved_at = float(raw_entry["saved_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            number < 0
            or not isinstance(raw_path, str)
            or not raw_path
            or not math.isfinite(position)
            or position < 0
            or not math.isfinite(saved_at)
        ):
            continue
        entries[number] = ResumeEntry(Path(raw_path), position, saved_at)
    return entries


__all__ = [
    "DEFAULT_WRITE_INTERVAL",
    "RESUME_STATE_FILENAME",
    "ResumeEntry",
    "ResumeStateStore",
]
