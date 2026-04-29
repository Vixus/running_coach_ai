#!/usr/bin/env python3
"""Launch both the Slack bot/scheduler and the web dashboard in one container."""

import signal
import subprocess
import sys
import time

processes = []


def run_migrations() -> None:
    """Apply any pending alembic migrations before the app starts.

    Without this, model columns added via migrations (e.g. athletes.email)
    are missing from the prod DB and queries fail at startup —
    Base.metadata.create_all only creates missing tables, never alters
    existing ones.
    """
    print("[start] running alembic upgrade head", flush=True)
    rc = subprocess.call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        stdout=sys.stdout, stderr=sys.stderr,
    )
    if rc != 0:
        print(f"[start] alembic upgrade failed (exit {rc}) — aborting", flush=True)
        sys.exit(rc)


def start_process(script_path: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, script_path], stdout=sys.stdout, stderr=sys.stderr)


def terminate_processes() -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()

    deadline = time.time() + 5
    while time.time() < deadline and any(p.poll() is None for p in processes):
        time.sleep(0.1)

    for process in processes:
        if process.poll() is None:
            process.kill()


def handle_shutdown(signum, frame) -> None:
    terminate_processes()
    sys.exit(0)


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

run_migrations()

processes.append(start_process("main.py"))
processes.append(start_process("web.py"))

try:
    while True:
        for process in processes:
            return_code = process.poll()
            if return_code is not None:
                terminate_processes()
                sys.exit(return_code)
        time.sleep(0.5)
except KeyboardInterrupt:
    terminate_processes()
    sys.exit(0)
