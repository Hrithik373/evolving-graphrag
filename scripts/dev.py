"""Start the API and the console together, with one Ctrl+C to stop both.

    python scripts/dev.py                    # memory store, mock LLM, seeded
    python scripts/dev.py --store postgres   # against a real database
    python scripts/dev.py --llm anthropic    # needs ANTHROPIC_API_KEY
    python scripts/dev.py --no-seed --api-port 9000

Written in Python rather than as a shell script because it has to work the same in
PowerShell, Git Bash and a POSIX terminal, and because the two interesting problems here
are not shell-shaped:

* **Ports move.** Something else on the machine may already hold 8000 or 5173, so the
  script finds free ports and then *tells Vite about the one the API actually got* - the
  dev proxy target is passed through, so the console never points at the wrong backend.
* **Child processes outlive their parent on Windows.** ``npm run dev`` spawns node as a
  grandchild; killing npm alone orphans it and leaves the port held. Shutdown kills the
  whole process tree.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = platform.system() == "Windows"

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
BLUE, ORANGE, GREEN, RED, GREY = (
    "\033[38;5;33m",
    "\033[38;5;208m",
    "\033[38;5;28m",
    "\033[38;5;160m",
    "\033[38;5;244m",
)

if IS_WINDOWS:  # enable ANSI on the legacy console
    os.system("")

# Two output fixes, both learned the hard way:
#   encoding  - the Windows console defaults to cp1252 and Vite's banner contains U+279C,
#               which killed the log-forwarding thread on the first arrow and silenced the
#               console's output while the server kept running.
#   buffering - stdout is block-buffered when redirected to a file or a pipe, so the
#               "here are your URLs" banner sat unflushed with nothing after it to force
#               it out. Line buffering means every line lands as it is written.
for stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def venv_bin(name: str) -> Path:
    folder = "Scripts" if IS_WINDOWS else "bin"
    suffix = ".exe" if IS_WINDOWS else ""
    return ROOT / ".venv" / folder / f"{name}{suffix}"


def port_is_taken(port: int) -> bool:
    """True if anything is already listening on this port, on either IP stack.

    Probing by *connecting* rather than by binding, for two reasons. On Windows
    ``SO_REUSEADDR`` lets a bind succeed against a port that is actively in use, so a bind
    probe reports free ports that are not - which is how the console was first launched
    onto an occupied port. And `localhost` resolves to ::1 before 127.0.0.1 on Windows, so
    a service on the IPv6 loopback is invisible to an IPv4-only check; Vite binds exactly
    that way.
    """
    families = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_dualstack_ipv6() or socket.has_ipv6:
        families.append((socket.AF_INET6, "::1"))
    for family, host in families:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.35)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def free_port(preferred: int) -> int:
    """Return ``preferred`` if it is free, otherwise the next free port above it."""
    for candidate in range(preferred, preferred + 40):
        if not port_is_taken(candidate):
            return candidate
    raise SystemExit(f"no free port in {preferred}-{preferred + 40}")


def pump(stream, prefix: str, colour: str) -> None:
    """Forward a child's output, one prefixed line at a time, so both logs interleave
    readably in a single terminal."""
    for raw in iter(stream.readline, ""):
        line = raw.rstrip()
        if not line:
            continue
        try:
            print(f"{colour}{prefix}{RESET} {line}", flush=True)
        except UnicodeEncodeError:
            # Belt and braces: a console this thread cannot encode to must not take the
            # thread down and silence the log for the rest of the session.
            safe = line.encode("ascii", "replace").decode("ascii")
            print(f"{colour}{prefix}{RESET} {safe}", flush=True)
    stream.close()


def spawn(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.Popen:
    kwargs = {}
    if IS_WINDOWS:
        # Its own process group, so Ctrl+C in this terminal does not race the children
        # and shutdown can target the whole tree deliberately.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


def kill_tree(process: subprocess.Popen) -> None:
    """Terminate a child and everything it spawned.

    ``npm run dev`` is a launcher: the Vite server is its grandchild. Terminating only the
    process we hold leaves Vite running and the port held, so the whole tree goes.
    """
    if process.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def wait_for(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.4)
    return False


def preflight(skip_frontend: bool) -> None:
    problems = []
    if not venv_bin("python").exists():
        problems.append(f"  no virtualenv at .venv — run:  {BOLD}make install{RESET}")
    elif not venv_bin("uvicorn").exists():
        problems.append(f"  uvicorn missing — run:  {BOLD}make install{RESET}")
    if not skip_frontend and not (ROOT / "frontend" / "node_modules").is_dir():
        problems.append(
            f"  frontend dependencies missing — run:  {BOLD}cd frontend && npm install{RESET}"
        )
    if problems:
        print(f"{RED}cannot start:{RESET}")
        print("\n".join(problems))
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--store", default="memory", choices=["memory", "postgres", "arcadedb"])
    parser.add_argument("--llm", default="mock", choices=["mock", "anthropic", "gateway"])
    parser.add_argument("--postgres-dsn", default=os.getenv("EGRAPH_POSTGRES_DSN", ""))
    parser.add_argument(
        "--seed",
        dest="seed",
        action="store_true",
        default=True,
        help="load the mini-corpus if the index is empty (default)",
    )
    parser.add_argument("--no-seed", dest="seed", action="store_false")
    parser.add_argument("--api-only", action="store_true", help="skip the console")
    parser.add_argument("--reload", action="store_true", help="restart the API on code changes")
    args = parser.parse_args()

    preflight(args.api_only)

    api_port = free_port(args.api_port)
    web_port = free_port(args.web_port) if not args.api_only else 0
    for requested, actual, what in (
        (args.api_port, api_port, "API"),
        (args.web_port, web_port, "console"),
    ):
        if actual and actual != requested:
            print(f"{GREY}port {requested} is in use; the {what} will use {actual} instead{RESET}")

    env = os.environ.copy()
    env.update(
        {
            "EGRAPH_STORE_BACKEND": args.store,
            "EGRAPH_QUEUE_BACKEND": "inline",
            "EGRAPH_LLM_BACKEND": args.llm,
            "EGRAPH_SEED_ON_START": "true" if args.seed else "false",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if args.store == "memory":
        # Keep the demo index in memory only; a stale snapshot on disk is a confusing
        # thing to debug when you meant to start clean.
        env["EGRAPH_MEMORY_STORE_PATH"] = ""
    if args.postgres_dsn:
        env["EGRAPH_POSTGRES_DSN"] = args.postgres_dsn
    if args.llm == "anthropic" and not env.get("ANTHROPIC_API_KEY"):
        print(f"{RED}--llm anthropic needs ANTHROPIC_API_KEY in the environment{RESET}")
        return 1

    processes: list[subprocess.Popen] = []

    api_cmd = [
        str(venv_bin("uvicorn")),
        "egraph.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port),
        "--log-level",
        "info",
    ]
    if args.reload:
        api_cmd += ["--reload", "--reload-dir", "src"]

    print(f"{DIM}starting API   {' '.join(api_cmd[1:])}{RESET}")
    api = spawn(api_cmd, env, ROOT)
    processes.append(api)
    threading.Thread(target=pump, args=(api.stdout, "api ", BLUE), daemon=True).start()

    api_url = f"http://127.0.0.1:{api_port}"
    if not wait_for(f"{api_url}/health"):
        print(f"{RED}the API did not become healthy — see the log above{RESET}")
        for process in processes:
            kill_tree(process)
        return 1

    if not args.api_only:
        npm = "npm.cmd" if IS_WINDOWS else "npm"
        web_env = env.copy()
        # The console talks to /api on its own origin; Vite proxies that to whichever port
        # the API actually got.
        web_env["API_UPSTREAM"] = api_url
        web_cmd = [npm, "run", "dev", "--", "--port", str(web_port), "--strictPort"]
        print(f"{DIM}starting console  vite --port {web_port}{RESET}")
        web = spawn(web_cmd, web_env, ROOT / "frontend")
        processes.append(web)
        threading.Thread(target=pump, args=(web.stdout, "web ", ORANGE), daemon=True).start()
        # Readiness by port, not by HTTP: Vite binds the IPv6 loopback, so polling
        # 127.0.0.1 never succeeds and the banner would wait out the whole timeout.
        deadline = time.time() + 45
        while time.time() < deadline and not port_is_taken(web_port):
            time.sleep(0.3)

    console_url = f"http://localhost:{web_port}"
    bar = "─" * 64
    print(f"\n{GREEN}{bar}{RESET}")
    if not args.api_only:
        print(f"  {BOLD}console{RESET}    {console_url}")
    print(f"  {BOLD}API docs{RESET}   {api_url}/docs")
    print(f"  {BOLD}metrics{RESET}    {api_url}/metrics")
    print(
        f"  {DIM}store={args.store}  queue=inline  llm={args.llm}"
        f"{'  seeded' if args.seed else ''}{RESET}"
    )
    print(f"{GREEN}{bar}{RESET}")
    print(f"{DIM}Ctrl+C stops both.{RESET}\n")
    if not args.api_only and platform.system() == "Windows":
        # Vite binds IPv6 localhost; 127.0.0.1 can fail where `localhost` works.
        print(f"{GREY}note: use localhost, not 127.0.0.1, for the console{RESET}\n")

    try:
        while True:
            for process in processes:
                if process.poll() is not None:
                    name = "API" if process is api else "console"
                    print(
                        f"\n{RED}the {name} exited (code {process.returncode}); shutting down{RESET}"
                    )
                    raise KeyboardInterrupt
            time.sleep(0.4)
    except KeyboardInterrupt:
        print(f"\n{DIM}stopping…{RESET}")
    finally:
        for process in reversed(processes):
            kill_tree(process)
        print(f"{DIM}stopped.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
