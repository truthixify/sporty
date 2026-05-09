"""Local development orchestrator. `scraper dev` launches capture + parse
loop + api + monitor as subprocesses in one terminal, prefixed-and-coloured
so you can read what each one is doing without four windows."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import IO


_COLORS = {
    "capture": "\033[36m",  # cyan
    "parse":   "\033[35m",  # magenta
    "api":     "\033[32m",  # green
    "monitor": "\033[33m",  # yellow
}
_RESET = "\033[0m"
_DIM = "\033[90m"
_NAME_WIDTH = max(len(n) for n in _COLORS)


@dataclass
class _Service:
    name: str
    proc: subprocess.Popen
    pump: threading.Thread


def run_dev_stack(
    *,
    api: bool = True,
    capture: bool = True,
    parse: bool = True,
    monitor: bool = True,
    parse_interval_s: int = 60,
    headless: bool | None = None,
) -> int:
    """Launch the requested services as subprocesses, multiplex their
    output, and wait for SIGINT. Returns 0 on clean shutdown."""
    services: list[_Service] = []
    stop_event = threading.Event()
    parse_thread: threading.Thread | None = None

    def _spawn(name: str, args: list[str]) -> _Service:
        cmd = [sys.executable, "-m", "src", *args]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        pump = threading.Thread(
            target=_pump_output, args=(name, proc.stdout), daemon=True,
        )
        pump.start()
        return _Service(name=name, proc=proc, pump=pump)

    _banner(f"starting dev stack: api={api} capture={capture} parse={parse} monitor={monitor}")

    if api:
        services.append(_spawn("api", ["api"]))
        time.sleep(3)  # give uvicorn a moment to bind before monitor starts polling
    if capture:
        cap_args = ["capture"]
        if headless is True:
            cap_args.append("--headless")
        elif headless is False:
            cap_args.append("--headed")
        services.append(_spawn("capture", cap_args))
    if parse:
        parse_thread = threading.Thread(
            target=_parse_loop, args=(parse_interval_s, stop_event), daemon=True,
        )
        parse_thread.start()
    if monitor:
        time.sleep(2)  # extra grace
        services.append(_spawn("monitor", ["monitor"]))

    _banner(f"started {len(services)} subprocess(es). Ctrl+C to stop.")

    try:
        while True:
            for s in services:
                if s.proc.poll() is not None:
                    _banner(f"{s.name} exited with code {s.proc.returncode}; tearing down")
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        _banner("shutdown requested")
    finally:
        stop_event.set()
        for s in services:
            try:
                s.proc.terminate()
            except Exception:
                pass
        deadline = time.time() + 10
        for s in services:
            remaining = max(0.5, deadline - time.time())
            try:
                s.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _banner(f"{s.name} did not stop in time, killing")
                try:
                    s.proc.kill()
                except Exception:
                    pass
        _banner("all subprocesses stopped")
    return 0


def _pump_output(name: str, stream: IO[str] | None) -> None:
    if stream is None:
        return
    color = _COLORS.get(name, "")
    prefix = f"{color}[{name:>{_NAME_WIDTH}}]{_RESET} "
    for line in stream:
        sys.stdout.write(prefix + line if line.endswith("\n") else prefix + line + "\n")
        sys.stdout.flush()


def _parse_loop(interval_s: int, stop_event: threading.Event) -> None:
    color = _COLORS.get("parse", "")
    prefix = f"{color}[{'parse':>{_NAME_WIDTH}}]{_RESET} "
    while not stop_event.is_set():
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "src", "parse"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in (proc.stdout or "").splitlines():
                sys.stdout.write(prefix + line + "\n")
            sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(prefix + f"loop error: {exc}\n")
            sys.stdout.flush()
        if stop_event.wait(interval_s):
            break


def _banner(text: str) -> None:
    sys.stdout.write(f"{_DIM}dev: {text}{_RESET}\n")
    sys.stdout.flush()
