#!/usr/bin/env python3
"""kb - CLI for the known-issues knowledge base (PocketBase server with the KB hooks).

Python 3.8+, standard library only. Thin client: validation, duplicate refusal, protected fields and
search ranking are enforced by the SERVER (pb_hooks), so curl and the dashboard obey the same rules.

  kb search  <words...>            find prior bugs / perf findings / gotchas (FTS5, stemmed, ranked)
  kb show    <id>                  full entry
  kb add     --type bug ...        record a new one (server refuses near-duplicates)
  kb confirm <id> --note "..."     "hit this again, the fix worked"
  kb update  <id> --fix "..."      improve an entry (old fix/cause is kept in notes)
  kb obsolete <id> --reason "..."  retire an entry (never deleted)
  kb recent | stats | health

Env: KB_URL (default http://127.0.0.1:8090), KB_PROJECT (default: git repo folder name), KB_SOURCE.
Exit codes: 0 ok, 2 server unreachable / hooks missing, 3 duplicate refused, 4 rejected or other error.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("KB_URL", "http://127.0.0.1:8090").rstrip("/")
COLL = "entries"
TYPES = ("bug", "perf", "gotcha")
ACTOR = None  # project name sent to the server (X-KB-Project)


def die(msg, code=4):
    print(msg, file=sys.stderr)
    sys.exit(code)


class ApiErr(Exception):
    def __init__(self, code, body):
        self.code = code
        self.body = body if isinstance(body, dict) else {"message": str(body)}

    def text(self):
        msg = self.body.get("message", "")
        extra = [f"{k}: {v.get('message', v)}" for k, v in (self.body.get("data") or {}).items()
                 if isinstance(v, dict) and "message" in v]
        return (msg + ("\n" + "\n".join(extra) if extra else "")).strip() or f"HTTP {self.code}"


class Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; 2 is reserved for 'server unreachable', so use 4 (rejected)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        die(f"{self.prog}: error: {message}", 4)


def detect_project():
    global ACTOR
    if ACTOR:
        return ACTOR
    if os.environ.get("KB_PROJECT"):
        ACTOR = os.environ["KB_PROJECT"]
        return ACTOR
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        if top:
            ACTOR = os.path.basename(top)
            return ACTOR
    except Exception:
        pass
    ACTOR = os.path.basename(os.getcwd())
    return ACTOR


def api(method, path, body=None, params=None, force=False):
    url = f"{BASE}/api/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    headers = {"Content-Type": "application/json", "X-KB-Project": detect_project()}
    if force:
        headers["X-KB-Force"] = "1"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode(errors="replace"))
        except Exception:
            data = {"message": f"HTTP {e.code}"}
        msg = str(data.get("message", "")).lower()
        if e.code == 404 and path.startswith("kb/") and "entry not found" not in msg:
            die("The server has no KB hooks (pb_hooks). Start it with `bugkb --start` from the KB server folder.", 2)
        if e.code == 404 and "collection" in msg:
            die("KB collection 'entries' not found. Start the server with `bugkb --start` so migrations run.", 2)
        raise ApiErr(e.code, data)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        die(f"KB server not reachable at {BASE} ({getattr(e, 'reason', e)}).\n"
            "If the `bugkb` command exists, try `bugkb --start --detach` ONCE. Otherwise do NOT retry in a loop: "
            "continue the task without the KB and tell the user it was offline.", 2)


def call(*a, **kw):
    try:
        return api(*a, **kw)
    except ApiErr as e:
        die(e.text())


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_tags(s):
    return uniq([t.strip().lower() for t in re.split(r"[,\s]+", s or "") if t.strip()])


def parse_json_arg(s, name):
    try:
        v = json.loads(s)
    except json.JSONDecodeError as e:
        die(f"--{name} must be valid JSON ({e}). Example: --{name} '{{\"key\": \"value\"}}'")
    if not isinstance(v, dict):
        die(f"--{name} must be a JSON object")
    return v


def read_stdin_json():
    try:
        v = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"stdin is not valid JSON: {e}")
    if not isinstance(v, dict):
        die("stdin JSON must be an object")
    return v


def card(rec, verbose=False):
    seen = ", ".join(rec.get("seen_in") or [])
    head = (f"#{rec['id']} [{rec['type']}|{rec['status']}|{rec.get('scope') or 'general'}] "
            f"hits={rec.get('hits') or 0}" + (f" seen_in: {seen}" if seen else ""))
    lines = [head, f"  {rec['title']}"]
    if verbose:
        for k in ("symptom", "cause", "fix"):
            if rec.get(k):
                lines.append(f"  {k}: {rec[k]}")
        for k in ("env", "metrics"):
            if rec.get(k):
                lines.append(f"  {k}: {json.dumps(rec[k])}")
        for k in ("tags", "related"):
            if rec.get(k):
                lines.append(f"  {k}: {', '.join(rec[k])}")
        for k in ("project", "supersedes", "source", "created", "updated"):
            if rec.get(k):
                lines.append(f"  {k}: {rec[k]}")
        if rec.get("notes"):
            lines.append("  notes:\n    " + rec["notes"].strip().replace("\n", "\n    "))
    else:
        if rec.get("symptom"):
            lines.append(f"  symptom: {clip(rec['symptom'], 220)}")
        if rec.get("fix"):
            lines.append(f"  fix: {clip(rec['fix'], 320)}")
        if rec.get("metrics"):
            lines.append(f"  metrics: {clip(json.dumps(rec['metrics']), 200)}")
    return "\n".join(lines)


def get(id_):
    return call("GET", f"collections/{COLL}/records/{urllib.parse.quote(id_)}")


# ---------------------------------------------------------------------------- commands
def cmd_health(a):
    r = call("GET", "health")
    info = call("GET", "kb/info")
    print(f"ok: {BASE} ({r.get('message', '')}); entries={info['entries']} indexed={info['indexed']}")


def cmd_search(a):
    q = " ".join(a.query)
    res = call("GET", "kb/search", params={"q": q, "type": a.type, "tag": a.tag, "project": a.project,
                                            "limit": a.n, "all": "1" if a.all else ""})
    items = res.get("items", [])
    if a.json:
        print(json.dumps(items, indent=2))
        return
    if not items:
        print(f'No matches for "{q}". If you solve this in a non-obvious way, record it with `kb add`.')
        return
    print(f'{len(items)} match(es) for "{q}" (use `kb show <id>` for full detail):\n')
    print("\n\n".join(card(c) for c in items))


def cmd_show(a):
    rec = get(a.id)
    print(json.dumps(rec, indent=2) if a.json else card(rec, verbose=True))


def cmd_recent(a):
    items = call("GET", f"collections/{COLL}/records", params={"sort": "-updated", "perPage": a.n}).get("items", [])
    print(json.dumps(items, indent=2) if a.json else "\n\n".join(card(c) for c in items) or "(empty)")


def cmd_stats(a):
    from collections import Counter
    items, page = [], 1
    while True:
        r = call("GET", f"collections/{COLL}/records", params={"perPage": 500, "page": page, "sort": "created"})
        items += r.get("items", [])
        if page >= r.get("totalPages", 1):
            break
        page += 1
    print(f"total: {len(items)}")
    for k in ("type", "status", "project"):
        c = Counter(i.get(k) or "-" for i in items)
        print(f"{k}: " + ", ".join(f"{n}={v}" for n, v in c.most_common()))
    print(f"unverified awaiting review: {sum(1 for i in items if i['status'] == 'unverified')}")


def cmd_add(a):
    src = read_stdin_json() if a.json_stdin else {}

    def pick(k):
        v = getattr(a, k, None)
        return v if v is not None else src.get(k)

    tags = pick("tags")
    tags = parse_tags(tags) if isinstance(tags, str) else [str(t) for t in (tags or [])]
    rec = {k: pick(k) or "" for k in ("type", "title", "symptom", "cause", "fix", "scope", "source")}
    rec["tags"] = tags
    rec["project"] = pick("project") or detect_project()
    rec["source"] = rec["source"] or os.environ.get("KB_SOURCE", "agent")
    rec["env"] = parse_json_arg(a.env, "env") if a.env else (src.get("env") or {})
    rec["metrics"] = parse_json_arg(a.metrics, "metrics") if a.metrics else (src.get("metrics") or {})
    rec["related"] = src.get("related") or []
    rec["supersedes"] = src.get("supersedes") or ""
    if not rec["scope"]:
        del rec["scope"]
    try:
        out = api("POST", f"collections/{COLL}/records", body=rec, force=a.force)
    except ApiErr as e:
        dups = None
        if e.code == 409:
            m = re.search(r"(\[.*\])", e.body.get("message", ""), re.S)
            try:
                dups = json.loads(m.group(1)) if m else None
            except ValueError:
                dups = None
        if dups:
            msg = ["Not saved - looks like a duplicate of:"]
            msg += [f"  #{d['id']} ({int(d['similarity'] * 100)}% similar) {d['title']}" for d in dups]
            msg += ["",
                    "Same problem?   kb confirm <id> --note \"what you did / any new detail\"",
                    "Better fix?     kb update <id> --fix \"...\"",
                    "Truly different? re-run the same command with --force"]
            die("\n".join(msg), 3)
        die(e.text())
    print(f"saved #{out['id']} [{out['type']}|{out['status']}] {out['title']}")


def cmd_confirm(a):
    body = {"project": a.project or detect_project(), "note": a.note or ""}
    if a.env:
        body["env"] = parse_json_arg(a.env, "env")
    out = call("POST", f"kb/confirm/{a.id}", body=body)
    print(f"confirmed #{out['id']} hits={out['hits']} status={out['status']} seen_in={', '.join(out['seen_in'])}")


def cmd_update(a):
    rec = get(a.id)
    src = read_stdin_json() if a.json_stdin else {}
    body = {}

    def pick(k):
        v = getattr(a, k, None)
        return v if v is not None else src.get(k)

    for k in ("title", "symptom", "cause", "fix", "scope"):
        v = pick(k)
        if v is not None:
            body[k] = v
    if a.tags is not None:
        body["tags"] = parse_tags(a.tags)
    if a.add_tags:
        body["tags"] = uniq((rec.get("tags") or []) + parse_tags(a.add_tags))
    if a.env:
        body["env"] = {**(rec.get("env") or {}), **parse_json_arg(a.env, "env")}
    if a.metrics:
        body["metrics"] = {**(rec.get("metrics") or {}), **parse_json_arg(a.metrics, "metrics")}
    if a.relate:
        body["related"] = uniq((rec.get("related") or []) + a.relate)
    if a.note:
        body["notes"] = ((rec.get("notes") or "").rstrip() + f"\n[{now()}] {detect_project()}: {a.note}").strip()
    if not body:
        die("Nothing to update. Pass at least one of --title/--symptom/--cause/--fix/--scope/--tags/--add-tags/"
            "--env/--metrics/--relate/--note")
    out = call("PATCH", f"collections/{COLL}/records/{a.id}", body=body)
    print(f"updated #{out['id']} [{out['type']}|{out['status']}] {out['title']}")


def cmd_obsolete(a):
    body = {"reason": a.reason, "project": a.project or detect_project()}
    if a.superseded_by:
        body["superseded_by"] = a.superseded_by
    call("POST", f"kb/obsolete/{a.id}", body=body)
    print(f"obsolete #{a.id}")


# --------------------------------------------------------------------------------- cli
def main():
    global ACTOR
    p = Parser(prog="kb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("health"); s.set_defaults(fn=cmd_health)

    s = sub.add_parser("search", help="search entries")
    s.add_argument("query", nargs="+", help="error text, symptom or keywords")
    s.add_argument("--type", choices=TYPES); s.add_argument("--project"); s.add_argument("--tag")
    s.add_argument("-n", type=int, default=5); s.add_argument("--all", action="store_true", help="include obsolete")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_search)

    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("recent"); s.add_argument("-n", type=int, default=10); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_recent)
    s = sub.add_parser("stats"); s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("add", help="record a new entry")
    s.add_argument("--type", choices=TYPES); s.add_argument("--title"); s.add_argument("--symptom")
    s.add_argument("--cause"); s.add_argument("--fix"); s.add_argument("--project")
    s.add_argument("--tags", help="comma-separated technologies, e.g. powershell,pester")
    s.add_argument("--scope", choices=("general", "project")); s.add_argument("--source")
    s.add_argument("--env", help='JSON, e.g. \'{"pwsh":"7.4.2","os":"ubuntu-24.04"}\'')
    s.add_argument("--metrics", help='JSON for perf: {"metric":..,"before":..,"after":..,"unit":..,"conditions":..}')
    s.add_argument("--json-stdin", action="store_true", help="read the entry as a JSON object from stdin (best for multi-line text)")
    s.add_argument("--force", action="store_true", help="skip the duplicate check"); s.set_defaults(fn=cmd_add)

    s = sub.add_parser("confirm", help="you hit this too / the fix worked")
    s.add_argument("id"); s.add_argument("--project"); s.add_argument("--note"); s.add_argument("--env")
    s.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("update", help="improve an existing entry")
    s.add_argument("id")
    for k in ("title", "symptom", "cause", "fix", "project", "note"):
        s.add_argument(f"--{k}")
    s.add_argument("--scope", choices=("general", "project"))
    s.add_argument("--tags", help="REPLACE tags"); s.add_argument("--add-tags")
    s.add_argument("--env"); s.add_argument("--metrics"); s.add_argument("--relate", action="append", help="related entry id (repeatable)")
    s.add_argument("--json-stdin", action="store_true"); s.set_defaults(fn=cmd_update)

    s = sub.add_parser("obsolete", help="retire an entry")
    s.add_argument("id"); s.add_argument("--reason", required=True); s.add_argument("--superseded-by"); s.add_argument("--project")
    s.set_defaults(fn=cmd_obsolete)

    a = p.parse_args()
    if getattr(a, "project", None) and a.cmd in ("update", "confirm", "obsolete"):
        ACTOR = a.project
    try:
        a.fn(a)
    except BrokenPipeError:  # e.g. `kb search ... | head`
        sys.exit(0)


if __name__ == "__main__":
    main()
