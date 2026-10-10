#!/usr/bin/env python3
"""bugkb - run and inspect the local known-issues server (PocketBase).

  bugkb --start [--port 8090] [--host 127.0.0.1] [--detach]   start (idempotent; --detach = background)
  bugkb --stop                                                 stop a server started with --detach
  bugkb --status [--port 8090]                                 health, entry counts, paths
  bugkb --superuser EMAIL [--password PW]                      create/update the dashboard superuser

Binaries are picked by platform from  <this folder>/<os>_<arch>/pocketbase[.exe]
(e.g. linux_amd64, windows_amd64, darwin_arm64). Data, migrations, hooks and the web UI are shared
across platforms: pb_data/, pb_migrations/, pb_hooks/, pb_public/ next to this script.

Python 3.8+, standard library only. Exit codes: 0 ok, 1 failure.
"""
import argparse
import getpass
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "pb_data")
MIGS = os.path.join(HERE, "pb_migrations")
HOOKS = os.path.join(HERE, "pb_hooks")
PUBLIC = os.path.join(HERE, "pb_public")
PIDFILE = os.path.join(HERE, "bugkb.pid")
LOGFILE = os.path.join(HERE, "bugkb.log")
IS_WIN = os.name == "nt"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def die(msg, code=1):
    print(f"bugkb: {msg}", file=sys.stderr)
    sys.exit(code)


# ------------------------------------------------------------------ binary discovery
def platform_folder():
    system = platform.system().lower()  # windows / linux / darwin
    m = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)
    return f"{system}_{arch}"


def find_binary():
    folder = platform_folder()
    d = os.path.join(HERE, folder)
    if not os.path.isdir(d):
        have = sorted(x for x in os.listdir(HERE) if "_" in x and os.path.isdir(os.path.join(HERE, x))
                      and not x.startswith("pb_"))
        die(f"no binary folder '{folder}' next to bugkb.py (found: {', '.join(have) or 'none'}). "
            f"Download PocketBase for this platform and unzip it into {d}")
    p = os.path.join(d, "pocketbase.exe" if IS_WIN else "pocketbase")
    if not os.path.isfile(p):
        die(f"no pocketbase binary inside {d}")
    if not IS_WIN and not os.access(p, os.X_OK):
        os.chmod(p, 0o755)
    return p


# ---------------------------------------------------------------------- server probes
def base_url(host, port):
    h = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://[{h}]:{port}" if ":" in h else f"http://{h}:{port}"


def http_json(url, timeout=2):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def healthy(host, port):
    try:
        info = http_json(base_url(host, port) + "/api/kb/info")
        return info.get("hooks") == 1
    except Exception:
        return False


def read_pidfile():
    try:
        with open(PIDFILE) as f:
            return json.load(f)
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log_tail(n=15):
    try:
        with open(LOGFILE, "rb") as f:
            return b"\n".join(f.read().splitlines()[-n:]).decode(errors="replace")
    except Exception:
        return ""


# --------------------------------------------------------------------------- commands
def cmd_start(a):
    if a.host not in LOOPBACK and not a.allow_remote:
        die(f"refusing to bind {a.host}: the KB API is open (no auth) by design. "
            "Use --allow-remote only if you understand that anyone who can reach the port can read/write.")
    url = base_url(a.host, a.port)
    if healthy(a.host, a.port):
        print(f"bugkb already running at {url}")
        return
    binary = find_binary()
    first_run = not os.path.isdir(DATA)
    cmd = [binary, "serve", f"--http={a.host}:{a.port}", f"--dir={DATA}", f"--migrationsDir={MIGS}"]
    if os.path.isdir(HOOKS):
        cmd.append(f"--hooksDir={HOOKS}")
    if os.path.isdir(PUBLIC):
        cmd.append(f"--publicDir={PUBLIC}")

    if not a.detach:
        if first_run:
            print("first run: create your superuser with  bugkb --superuser you@local.dev  (or use the link PocketBase prints)")
        try:
            sys.exit(subprocess.call(cmd))
        except KeyboardInterrupt:
            sys.exit(0)

    log = open(LOGFILE, "ab")
    kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    with open(PIDFILE, "w") as f:
        json.dump({"pid": proc.pid, "host": a.host, "port": a.port}, f)
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            os.remove(PIDFILE)
            die(f"server exited immediately (code {proc.returncode}). Last log lines:\n{log_tail()}")
        if healthy(a.host, a.port):
            break
        time.sleep(0.3)
    else:
        die(f"server did not become healthy in 15s. Last log lines:\n{log_tail()}")
    try:  # warm the search index (rebuilds it if it drifted from the entries table)
        http_json(url + "/api/kb/info", timeout=120)
    except Exception:
        pass
    print(f"bugkb running at {url} (pid {proc.pid}); dashboard {url}/_/ ; log {LOGFILE}")
    if a.port != 8090 or a.host not in ("127.0.0.1", "localhost"):
        print(f"agents/CLI need:  KB_URL={url}")
    if first_run:
        print("first run: create your superuser with  bugkb --superuser you@local.dev")


def cmd_stop(a):
    info = read_pidfile()
    if not info:
        print("bugkb: no pid file, nothing to stop (a foreground server is stopped with Ctrl-C)")
        return
    pid = info["pid"]
    if not pid_alive(pid):
        os.remove(PIDFILE)
        print("bugkb was not running (removed stale pid file)")
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        if not pid_alive(pid):
            break
        time.sleep(0.3)
    else:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGKILL)
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    print(f"bugkb stopped (pid {pid})")


def cmd_status(a):
    info = read_pidfile() or {}
    host = info.get("host", a.host)
    port = a.port if a.port_given else info.get("port", a.port)
    url = base_url(host, port)
    up = healthy(host, port)
    print(f"server:   {'UP' if up else 'DOWN'} at {url}")
    if info:
        print(f"pid file: pid {info.get('pid')} ({'alive' if pid_alive(info.get('pid')) else 'dead'})")
    try:
        print(f"binary:   {find_binary()}")
    except SystemExit:
        print(f"binary:   missing for {platform_folder()}")
    print(f"data:     {DATA}{'' if os.path.isdir(DATA) else '  (not created yet)'}")
    if up:
        try:
            total = http_json(url + "/api/collections/entries/records?perPage=1")["totalItems"]
            unv = http_json(url + "/api/collections/entries/records?perPage=1&filter=" +
                            urllib.request.quote("status='unverified'"))["totalItems"]
            print(f"entries:  {total} total, {unv} unverified")
            try:
                info = http_json(url + "/api/kb/info")
                print(f"search:   hooks ok, {info['indexed']}/{info['entries']} entries indexed")
            except Exception:
                print("search:   KB hooks NOT loaded (pb_hooks missing?) - kb CLI will not work")
        except Exception as e:
            print(f"entries:  could not read ({e})")
    sys.exit(0 if up else 1)


def cmd_superuser(a):
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", a.superuser):
        die(f"'{a.superuser}' is not an email PocketBase accepts (the domain needs a dot, e.g. you@local.dev)")
    pw = a.password or os.environ.get("BUGKB_PASSWORD") or getpass.getpass("superuser password (min 8 chars): ")
    if len(pw) < 8:
        die("password must be at least 8 characters")
    r = subprocess.run([find_binary(), "superuser", "upsert", a.superuser, pw, f"--dir={DATA}"])
    sys.exit(r.returncode)


def main():
    p = argparse.ArgumentParser(prog="bugkb", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--superuser", metavar="EMAIL")
    p.add_argument("--port", type=int, default=None, help="default 8090")
    p.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (loopback only)")
    p.add_argument("--detach", action="store_true", help="with --start: run in the background")
    p.add_argument("--allow-remote", action="store_true", help="permit a non-loopback --host (API is unauthenticated)")
    p.add_argument("--password", help="with --superuser (or env BUGKB_PASSWORD, or prompt)")
    a = p.parse_args()
    a.port_given = a.port is not None
    a.port = a.port or 8090
    if a.start:
        cmd_start(a)
    elif a.stop:
        cmd_stop(a)
    elif a.status:
        cmd_status(a)
    else:
        cmd_superuser(a)


if __name__ == "__main__":
    main()
