#!/usr/bin/env python3
"""Launch both the Slack bot/scheduler and the web dashboard in one container."""

import signal
import subprocess
import sys
import time

processes = []


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
