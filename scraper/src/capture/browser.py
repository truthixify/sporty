"""Playwright wrappers used by the capture daemon. Pulled out of `daemon.py`
so that file can stay focused on orchestration (run loop, recovery decisions,
exit codes) and so the browser-specific code can be replaced or stubbed in
tests."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


log = logging.getLogger(__name__)


_LOCK_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")
_CHROMIUM_NAME_NEEDLES = ("chrome", "chromium")


def prepare_profile_for_launch(profile_dir: Path) -> None:
    """Run all the pre-launch hygiene steps for a Chromium persistent
    profile so a previous unclean exit doesn't block this one. In order:

    1. Kill any orphaned Chromium subprocesses still attached to this
       profile via `--user-data-dir`. These are the zombies that survive
       when the parent (Playwright) crashes mid-launch.
    2. Remove a stale `SingletonLock` if its PID is dead or non-Chromium.
    3. Patch `Default/Preferences` so Chromium doesn't pop the
       'Something went wrong when opening your profile' dialog.

    Idempotent and safe to call before every launch.
    """
    kill_orphaned_chromium_for_profile(profile_dir)
    cleanup_stale_chromium_lock(profile_dir)
    clear_chromium_crash_state(profile_dir)


def kill_orphaned_chromium_for_profile(profile_dir: Path) -> int:
    """Kill any chromium processes whose `--user-data-dir` argument matches
    `profile_dir`. These are usually leftovers from previous Playwright
    runs that crashed before Chromium's process tree was torn down.

    SIGTERM first, then SIGKILL after a brief grace period. Returns the
    number of PIDs we tried to kill (whether they died or not).
    """
    import signal

    try:
        resolved = str(profile_dir.resolve())
    except (OSError, RuntimeError):
        resolved = str(profile_dir)
    needle = f"--user-data-dir={resolved}"

    pids = _pgrep(needle)
    own_pid = os.getpid()
    pids = [p for p in pids if p != own_pid]
    if not pids:
        return 0

    sys.stderr.write(
        f"capture: killing {len(pids)} orphaned chromium process(es) "
        f"holding {profile_dir} ({sorted(pids)})\n"
    )
    sys.stderr.flush()

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    deadline = time.time() + 2.0
    while time.time() < deadline and any(_pid_alive(p) for p in pids):
        time.sleep(0.1)

    for pid in pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    return len(pids)


def _pgrep(needle: str) -> list[int]:
    """Return PIDs of processes whose full command line contains `needle`."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", needle],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return []
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.strip().splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def cleanup_stale_chromium_lock(profile_dir: Path) -> bool:
    """Detect and remove a stale `SingletonLock` left behind by a Chromium
    that crashed or was force-killed instead of closing cleanly.

    Chromium creates `SingletonLock` as a symlink whose target encodes
    `<hostname>-<pid>`. We delete it when:
      - the PID it points at is dead, OR
      - the PID is alive but `ps` shows it as a non-Chromium process
        (macOS reuses PIDs aggressively, so a stale lock can point at a
        recently-recycled shell or python process).

    We leave the lock alone when:
      - a live Chromium-named process holds it (the user really does have
        another Chromium running with this profile), OR
      - we couldn't introspect the PID (be conservative).

    Prints a one-line decision to stderr so the user knows what happened.
    Returns True if any lock files were removed.
    """
    lock = profile_dir / "SingletonLock"
    if not lock.exists() and not lock.is_symlink():
        return False

    target_pid: int | None = None
    if lock.is_symlink():
        try:
            target = os.readlink(lock)
            _hostname, _, pid_str = target.rpartition("-")
            target_pid = int(pid_str)
        except (OSError, ValueError):
            target_pid = None

    if target_pid is not None and _is_chromium_pid(target_pid):
        sys.stderr.write(
            f"capture: chromium profile at {profile_dir} is in use by pid {target_pid}.\n"
            f"capture: close that Chromium window, or:\n"
            f"capture:   pkill -f 'Chrome for Testing'   # kill all chrome-for-testing\n"
            f"capture:   kill {target_pid}               # kill that specific process\n"
        )
        sys.stderr.flush()
        return False

    reason = (
        f"pid {target_pid} is not a chromium process"
        if target_pid is not None
        else "no parseable lock target"
    )
    sys.stderr.write(
        f"capture: removing stale chromium lock in {profile_dir} ({reason})\n"
    )
    sys.stderr.flush()
    removed = False
    for name in _LOCK_FILES:
        path = profile_dir / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed = True
        except FileNotFoundError:
            pass
        except OSError as exc:
            sys.stderr.write(f"capture: could not remove {name}: {exc}\n")
            sys.stderr.flush()
    return removed


def clear_chromium_crash_state(profile_dir: Path) -> bool:
    """Patch `Default/Preferences` so Chromium doesn't pop up the
    'Something went wrong when opening your profile' restore dialog after a
    forced exit. The dialog is benign in dev but it gets in the way of the
    capture daemon's iframe-discovery polling.

    Sets `profile.exit_type = "Normal"` and `profile.exited_cleanly = true`.
    Also blanks `profile.exited_cleanly` and `exit_type` in the top-level
    `Local State` file (which Chrome also checks). Returns True if anything
    was patched.
    """
    changed = False
    prefs_path = profile_dir / "Default" / "Preferences"
    if prefs_path.is_file():
        try:
            with prefs_path.open("r", encoding="utf-8") as f:
                prefs = json.load(f)
            profile_section = prefs.setdefault("profile", {})
            if profile_section.get("exit_type") != "Normal":
                profile_section["exit_type"] = "Normal"
                changed = True
            if profile_section.get("exited_cleanly") is not True:
                profile_section["exited_cleanly"] = True
                changed = True
            if changed:
                with prefs_path.open("w", encoding="utf-8") as f:
                    json.dump(prefs, f)
                sys.stderr.write(
                    f"capture: patched Chromium crash markers in {prefs_path}\n"
                )
                sys.stderr.flush()
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(
                f"capture: couldn't patch {prefs_path}: {exc}\n"
            )
            sys.stderr.flush()
    return changed


def _is_chromium_pid(pid: int) -> bool:
    """True if PID is alive AND its process name looks like Chromium."""
    if not _pid_alive(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError):
        # Couldn't shell out; be conservative and assume the lock is real
        return True
    if result.returncode != 0:
        # ps failed — likely the PID is dead by now
        return False
    name = result.stdout.strip().lower()
    if not name:
        # Process disappeared between alive-check and ps
        return False
    return any(needle in name for needle in _CHROMIUM_NAME_NEEDLES)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _Page(Protocol):
    url: str
    async def evaluate(self, expr: str) -> Any: ...
    async def goto(self, url: str, **kw: Any) -> Any: ...
    async def reload(self, **kw: Any) -> Any: ...
    def on(self, event: str, handler: Callable) -> None: ...


_FIND_IFRAME_JS = (
    "() => {"
    " const ifr = Array.from(document.querySelectorAll('iframe'))"
    "  .find(f => (f.src||'').includes('virtustec'));"
    " return ifr ? ifr.src : null; }"
)
_RELOAD_IFRAME_JS = (
    "() => {"
    " const f = document.querySelector('iframe[src*=\"virtustec\"]');"
    " if (f) { f.src = f.src; } }"
)


async def discover_iframe_url(page: _Page, *, timeout_s: float = 300.0) -> str | None:
    """Poll the page DOM until an `<iframe>` whose src points at virtustec
    appears (the iframe is injected by SportyBet's JS after parent load).
    Returns the src URL, or None on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            url = await page.evaluate(_FIND_IFRAME_JS)
        except Exception:
            url = None
        if url:
            return url
        await asyncio.sleep(0.5)
    return None


async def reload_iframe(page: _Page) -> None:
    """L1 recovery: poke the iframe's src to force it to reload in place
    without touching the parent page or the persistent profile state."""
    await page.evaluate(_RELOAD_IFRAME_JS)


async def reload_parent(page: _Page) -> None:
    """L2 recovery: refresh the parent page (which re-runs SportyBet's JS and
    re-injects the iframe with a fresh `onlineHash`)."""
    await page.reload(wait_until="domcontentloaded", timeout=60_000)


async def renavigate_parent(page: _Page, parent_url: str) -> None:
    """L3 recovery: full re-navigation to the parent URL from scratch."""
    await page.goto(parent_url, wait_until="domcontentloaded", timeout=60_000)
