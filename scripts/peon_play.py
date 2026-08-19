#!/usr/bin/env python3
"""
Detached audio player for peon-py.

    peon_play.py <audio-file> [volume]

Runs under the managed venv and plays through `soundfile` + `sounddevice`.
When that stack is unavailable — the venv is still building, the wheels do not
work on this host, or there is no output device — it falls back to whichever
system player is installed, following the list in the OpenPeon integration
guide. Failure is always silent: nothing here may disturb the coding agent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:  # stdlib-only sibling module; never fatal if it cannot be imported
    import pack as pack_mod
except Exception:  # pragma: no cover - defensive, the file sits next to us
    pack_mod = None

# Windows: keep every helper process console-less, or the agent's terminal
# would blink a black window on each event.
NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} \
    if os.name == "nt" else {}

# Ordered list of system fallbacks: (binary, argv builder). First match wins.
# Volume conventions differ per backend, so each one scales the 0.0-1.0 master
# volume into its own range.
SYSTEM_PLAYERS: list[tuple[str, object]] = [
    ("pw-play",  lambda p, v: ["pw-play", f"--volume={v:.3f}", p]),
    ("paplay",   lambda p, v: ["paplay", f"--volume={int(v * 65536)}", p]),
    ("ffplay",   lambda p, v: ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                               "-volume", str(int(v * 100)), p]),
    ("mpv",      lambda p, v: ["mpv", "--no-terminal", f"--volume={int(v * 100)}", p]),
    ("play",     lambda p, v: ["play", "-q", "-v", f"{v:.3f}", p]),
    ("afplay",   lambda p, v: ["afplay", "-v", f"{v:.3f}", p]),          # macOS
    ("aplay",    lambda p, v: ["aplay", "-q", p]),                       # no volume control
]


def system_player_name() -> str:
    """Name of the system player we would fall back to, or "" if none."""
    for binary, _ in SYSTEM_PLAYERS:  # ffplay/mpv also exist on Windows
        if shutil.which(binary):
            return binary
    if os.name == "nt":
        # Always available: winsound is stdlib (WAV only) and MediaPlayer ships
        # with the OS for the compressed formats.
        return "winsound/powershell"
    return ""


def play_with_python(path: Path, volume: float) -> bool:
    """Play through the managed audio stack. False if it is unusable."""
    try:
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
    except Exception:
        return False
    try:
        data, samplerate = sf.read(str(path), dtype="float32", always_2d=False)
        if volume != 1.0:
            data = np.asarray(data) * float(volume)
        sd.play(data, samplerate)
        sd.wait()
        return True
    except Exception:
        return False


def play_with_system(path: Path, volume: float) -> bool:
    for binary, build in SYSTEM_PLAYERS:
        if not shutil.which(binary):
            continue
        try:
            subprocess.run(
                build(str(path), volume),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
                **NO_WINDOW)
            return True
        except Exception:
            continue
    if os.name == "nt":
        return _play_windows(path, volume)
    return False


# PowerShell fallback for the compressed formats. The path arrives through the
# environment, never through string interpolation, so a quote in a filename
# cannot break (or inject into) the script. MediaPlayer loads asynchronously,
# hence the wait for NaturalDuration before playing.
_PS_SCRIPT = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$player = New-Object System.Windows.Media.MediaPlayer
$player.Open([Uri]::new($env:PEON_PLAY_FILE))
$player.Volume = [double]$env:PEON_PLAY_VOLUME
$deadline = (Get-Date).AddSeconds(5)
while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 50
}
$player.Play()
if ($player.NaturalDuration.HasTimeSpan) {
    Start-Sleep -Milliseconds ($player.NaturalDuration.TimeSpan.TotalMilliseconds + 250)
} else {
    Start-Sleep -Seconds 5
}
$player.Close()
"""


def _play_windows(path: Path, volume: float) -> bool:
    """WAV through stdlib `winsound`, everything else through MediaPlayer."""
    if path.suffix.lower() == ".wav":
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return True
        except Exception:
            pass  # fall through to MediaPlayer, which also handles WAV
    env = dict(os.environ,
               PEON_PLAY_FILE=str(path),
               PEON_PLAY_VOLUME=f"{volume:.3f}")
    for shell in ("pwsh", "powershell"):
        if not shutil.which(shell):
            continue
        try:
            subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30, check=False, env=env,
                **NO_WINDOW)
            return True
        except Exception:
            continue
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    path = Path(sys.argv[1])
    try:
        volume = min(1.0, max(0.0, float(sys.argv[2]))) if len(sys.argv) > 2 else 0.5
    except ValueError:
        volume = 0.5
    if not path.is_file():
        return 0
    # Spec 4.4: a file that does not match its extension MUST be rejected. The
    # installer already checked registry packs; this covers the ones copied
    # into `~/.openpeon/packs` by hand, and costs one 12-byte read.
    if pack_mod is not None and not pack_mod.looks_like_audio(path):
        return 0
    if play_with_python(path, volume):
        return 0
    play_with_system(path, volume)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
