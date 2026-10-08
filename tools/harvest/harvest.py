#!/usr/bin/env python3
r"""harvest.py: bring every blitz run and major push home to the PC (the blitz-harvest skill).

Runs on the workstation (DESKTOP-GJ0EK81) with C:\Python313\python.exe. Read-only on every Mac: the only
remote commands are `hostname`, remote-find.sh (a read-only finder) and `tar -czf -` of a run folder.
No model is called anywhere. Plan: master-orchestrator\skills\blitz-harvest\ (SKILL.md, PLAN.md).

Commands
  plan              build the manifest: runs named by seeds, local planning docs, the fleet log and returns
  scan              find runs on the PC, agent2, the MacBook and agent1, match them, score other pushes
  copy [--dry-run]  copy new or changed run folders into blitz-returns\ (a changed run becomes -v2, -v3)
  push              put new RETURN.md files into the returns clone, commit and push (fleet.py pull first)
  report            write blitz-returns\HARVEST-<date>.md
  all [--dry-run]   check, plan, scan, copy, push, report
  check             credential-exclusion self-test, then an audit of blitz-returns\ for credential files
  seed DOC...       turn planning docs into a seed file for plan (the cloud thread runs this on Project docs)
  sync              zip the report, a redacted manifest and every non-restricted copied run for the Project
  confirm|drop NAME pattern-recognized pushes to confirm: harvest them from the next run on, or stop listing them

Options: --days N (trailing window, default 8, minimum 7)  --host NAME (repeatable)  --slow (300 s per host)
         --root DIR (default C:\Projects\master-orchestrator)  --push-restricted pointer|full
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

VERSION = 1
HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = Path(os.environ.get("HARVEST_ROOT", r"C:\Projects\master-orchestrator"))

HOST_ORDER = ["pc", "agent2", "macbook", "agent1"]       # section 5.2: most likely hits first
NEVER_SCAN = {"jjess", "jess@100.99.90.115", "100.99.90.115", "jjesss-mac-mini"}
SCAN_SECONDS, SLOW_SECONDS = 60, 300
FIND_SECONDS, SLOW_FIND_SECONDS = 25, 200
REACH_SECONDS = 8
FOLDER_CAP = 2 * 1024 ** 3          # above this, copy control files only
PROJECT_CAP = 200 * 1024 ** 2       # cloud copy cap per harvest
BIG_LOG = 50 * 1024 ** 2
RUNNING_MIN = 20                    # newest change this recent and no RETURN.md: the run is still going
PUSH_HARVEST, PUSH_CONFIRM = 4, 2   # section 3.7 thresholds

CACHE_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".cache", ".pnpm-store", ".next", ".turbo", ".npm"}
GIT_KEEP = re.compile(r"^(HEAD|config|packed-refs|refs/heads(/.*)?)$")
SECRET_NAME = re.compile(
    r"token|secret|credential|\.pem$|^id_|^\.env$|^\.env\.|\.keychain(-db)?$|^\.netrc$|^\.npmrc$|^\.pypirc$|\.p12$"
    r"|\.pfx$|\.key$|^auth\.json$|^\.git-credentials$", re.I)
JUNK = re.compile(r"^(\.DS_Store|\._.*)$")
SECRET_DIRS = {".ssh", ".gnupg", "Keychains", ".aws"}
TAR_EXCLUDES = sorted(CACHE_DIRS) + [
    ".git/objects", "*/.git/objects", ".git/lfs", "*/.git/lfs", ".git/logs", "*/.git/logs", ".git/modules",
    "*/.git/modules", ".git/worktrees", "*/.git/worktrees", ".git/index", "*/.git/index", ".ssh", ".gnupg", ".aws",
    "Keychains", ".config/orchestrator",
    "*token*", "*Token*", "*TOKEN*", "*secret*", "*Secret*", "*SECRET*", "*credential*", "*Credential*",
    "*.pem", "id_*", ".env", ".env.*", "*.keychain", "*.keychain-db", ".netrc", ".npmrc", "*.p12", "*.key",
    "auth.json", ".git-credentials", ".pypirc", "*.pfx", ".DS_Store", "._*"]
CONTROL_NAMES = {"state.json", "PROGRESS.md", "RETURN.md", "README.md", "MANIFEST.md", "GOAL.txt", "SPRINT.md",
                 "BRIEF.md", "RUBRIC.md", "REPORT.md", "ORIGINAL-PROMPT.md", "prompt.md", "status", "started_utc",
                 "ended_utc", "model", "config.env", "INDEX.md"}
RUN_MARKERS = ("state.json", "PROGRESS.md", "RETURN.md", "MANIFEST.md", "SPRINT.md", "GOAL.txt")

# Section 5.4. Strong words decide on their own; soft words lose to an explicit "Overall: normal" statement.
# PLAN 5.4 list, plus the Colombia/Project Playa engagement (client material Elliot keeps PC-only) and taxes
STRONG_WORDS = ["lawsuit", "litigation", "apeira", "toptal", "pave", "cureis", "deposition", "colombia", "playa",
                "tax", "taxes"]
# Home-level trees on the Macs that are infrastructure, not runs: ~/gt is Gas Town, agent2's always-on agent office
# (its worker clones are hundreds of MB each). remote-find.sh skips the same trees.
SKIP_TREES = ("gt",)
SOFT_WORDS = ["client", "clients", "nda", "family", "personal debt", "whatsapp", "messages export", "photos"]

SPRINT_ACCOUNTS = {"elliot@cybernovaequity.com", "luchansky.elliot.a@gmail.com", "elliot@nasarai.com"}
# weekday (Monday 0), hour, minute in UTC; or a fixed end time for an account that ended
RESETS = {"elliot@cybernovaequity.com": (3, 12, 0), "luchansky.elliot.a@gmail.com": (0, 9, 0),
          "el@elliotl.im": (6, 14, 0), "elliot@nasarai.com": "2026-10-01T04:15:00+00:00"}
ACCOUNT_WORDS = {"cybernova": "elliot@cybernovaequity.com", "gmail": "luchansky.elliot.a@gmail.com",
                 "nasarai team": "elliot@nasarai.com", "nasarai": "elliot@nasarai.com", "primary": "el@elliotl.im"}
USER_HOSTS = {"agent2": "agent2", "luchanskyelliot": "macbook", "agent1": "agent1", "jess": "jjess",
              "owner": "pc"}
LATEST_MODELS = re.compile(r"fable[- ]5[-.]1|opus[- ]5[-.]5|^fable$|^opus$", re.I)
REPORT_LINES = 149         # PLAN 6.3: the report stays under 150 lines
CREDENTIAL_LINE = re.compile(r"token|secret|passw|api[_-]?key|sk-[A-Za-z0-9]|ghp_|github_pat_|gh[ousr]_|xox[abpr]-|AKIA[0-9A-Z]"
                             r"|bearer|authorization|\.env\b|[A-Za-z0-9+/_=-]{32,}", re.I)


# ---------------------------------------------------------------- small helpers

def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_time(s):
    """ISO time or epoch seconds to epoch seconds, or None."""
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if re.fullmatch(r"\d{9,11}(\.\d+)?", s):
        return float(s)
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.timestamp()
    except ValueError:
        return None


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f GB" % n


def words(text):
    """Lower-case text with separators turned into spaces, for the keyword screen."""
    return " " + re.sub(r"[\s_./\\|:#()\[\]{}`'\",;*-]+", " ", (text or "").lower()) + " "


def keyword_hits(text, wordlist):
    w = words(text)
    return [k for k in wordlist if " %s " % k in w]


def is_secret(relpath):
    parts = [p for p in re.split(r"[\\/]", relpath) if p]
    if not parts:
        return False
    if any(p in SECRET_DIRS for p in parts):
        return True
    if "orchestrator" in parts and ".config" in parts:
        return True
    return bool(SECRET_NAME.search(parts[-1]))


def win_long(path):
    """Extended-length form of an absolute Windows path, so deep run folders copy past 260 characters."""
    p = str(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + os.path.abspath(p)
    return p


BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_part(name):
    """A path component Windows can hold."""
    if os.name != "nt":
        return name
    n = BAD_CHARS.sub("_", name).rstrip(" .")
    return n or "_"


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- context: paths, hosts, fleet.py

class Ctx:
    def __init__(self, root, days=8, slow=False, only_hosts=None, dry_run=False, push_restricted="pointer"):
        self.root = Path(root)
        self.tools = self.root / "tools"
        self.out = self.root / "blitz-returns"
        self.manifest_path = self.out / "harvest-manifest.json"
        self.ledger_path = self.out / "harvest-ledger.json"
        self.seeds = self.out / "seeds"
        self.scan_dir = self.out / ".scan"
        self.returns = self.root / "returns"
        self.days = max(7, int(days))
        self.slow = slow
        self.only_hosts = set(only_hosts or [])
        self.dry_run = dry_run
        self.push_restricted = push_restricted
        self.started = now_utc()
        self.window_start = self.started.timestamp() - self.days * 86400
        self._fleet = None

    @property
    def fleet(self):
        if self._fleet is None:
            sys.path.insert(0, str(self.tools))
            import fleet  # noqa: E402  (tools\fleet.py: SSH helper, hosts.conf parser, pull)
            self._fleet = fleet
        return self._fleet

    def hosts(self):
        """hosts.conf rows, harvest-only rows included, in sweep order, Jjess's Mac mini never included."""
        try:
            rows = self.fleet.load_hosts(harvest=True)
        except TypeError:          # an older fleet.py without the harvest_only column
            rows = self.fleet.load_hosts()
        rows = [h for h in rows if h["name"] not in NEVER_SCAN and h["ssh"] not in NEVER_SCAN
                and h["ssh"].split("@")[-1] not in NEVER_SCAN]
        if not any(h["name"] == "pc" for h in rows):
            rows.append({"name": "pc", "ssh": "local", "account": "el@elliotl.im", "account_key": "-",
                         "last_day": None, "sensitive_ok": True, "max_running": 0, "harvest_only": True})
        rows.sort(key=lambda h: HOST_ORDER.index(h["name"]) if h["name"] in HOST_ORDER else 99)
        if self.only_hosts:
            rows = [h for h in rows if h["name"] in self.only_hosts]
        return rows

    def load(self):
        return load_json(self.manifest_path, {"harvest": {}, "rows": []})

    def save(self, man):
        save_json(self.manifest_path, man)


def ssh_cmd(ctx, host, remote, timeout, data=None, connect=15):
    """Run one command on a host with fleet.py's ssh.exe and options. Returns (code, stdout, stderr, timed_out)."""
    f = ctx.fleet
    cmd = [f.SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % connect, host["ssh"], remote]
    try:
        r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout,
                           creationflags=getattr(f, "NO_WINDOW", 0))
        return r.returncode, r.stdout or b"", r.stderr or b"", False
    except subprocess.TimeoutExpired as e:
        return None, e.stdout or b"", e.stderr or b"", True


# ---------------------------------------------------------------- run folders: shared rules

def run_root(path, home):
    """Python twin of run_root() in remote-find.sh: the run folder a directory belongs to, or None."""
    d = path.rstrip("/")
    home = home.rstrip("/")
    if d in (home, "") or not d.startswith(home + "/"):
        return None                              # temp and system folders are never runs
    scratch = home + "/Library/Application Support/Claude/scratch-workspaces/"
    if d.startswith(scratch):                    # a Desktop Code-tab session started without a folder
        return scratch + d[len(scratch):].split("/")[0]
    aborted = home + "/orchestrator/sprints/_aborted"
    if d == aborted:
        return None
    for base in ("orchestrator/sprints/_aborted", "orchestrator/sprints", "orchestrator/returns"):
        pre = "%s/%s/" % (home, base)
        if d.startswith(pre):
            return pre + d[len(pre):].split("/")[0]
    if d == home + "/orchestrator" or d.startswith(home + "/orchestrator/"):
        return None
    if d.startswith(home + "/."):                # home dot folders (.claude, .config, .local, .Trash, ...)
        return None
    for skip in ("Library",) + SKIP_TREES:
        if d == "%s/%s" % (home, skip) or d.startswith("%s/%s/" % (home, skip)):
            return None
    for marker in ("/loops/", "/loop/"):
        if marker in d:
            d = d.split(marker)[0]
    for marker in ("/loops", "/loop"):
        if d.endswith(marker):
            d = d[: -len(marker)]
    for top in ("Downloads", "Desktop", "Documents"):
        base = "%s/%s" % (home, top)
        if d == base:
            return None
        if d.startswith(base + "/"):
            return base + "/" + d[len(base) + 1:].split("/")[0]
    for top in ("Pictures", "Movies", "Music", "Public"):
        if d.startswith("%s/%s" % (home, top)):
            return None
    return d or None


def expand_hint(path_hint, home):
    if not path_hint:
        return None
    p = path_hint
    if p.startswith("~/"):
        p = home.rstrip("/") + p[1:]
    m = re.match(r"^/Users/[^/]+(/.*)$", p)
    if m and home:
        p = home.rstrip("/") + m.group(1)
    return run_root(p, home) or p.rstrip("/")


# ---------------------------------------------------------------- plan: parse planning docs into rows

ID = r"[A-Za-z0-9][A-Za-z0-9._-]{2,90}"
DATE_IN = re.compile(r"20\d\d-\d\d-\d\d")
GENERIC_HEADINGS = {"paste", "plan", "how to run it", "the six runs", "folder", "while they run", "launch",
                    "inputs", "outputs", "run", "start each run", "get the files onto the macbook"}
PATTERNS = [
    ("path", re.compile(r"/Users/(?P<user>[A-Za-z0-9_.-]+)/orchestrator/sprints/(?P<id>%s)" % ID)),
    ("path", re.compile(r"(?<![A-Za-z0-9/])~/orchestrator/sprints/(?P<id>%s)" % ID)),
    ("returns", re.compile(r"/Users/(?P<user>[A-Za-z0-9_.-]+)/orchestrator/returns/(?P<id>%s)" % ID)),
    ("package", re.compile(r"/Users/(?P<user>[A-Za-z0-9_.-]+)/Downloads/(?P<id>%s)" % ID)),
    ("package", re.compile(r"(?<![A-Za-z0-9/])~/Downloads/(?P<id>%s)/" % ID)),
    ("id", re.compile(r"[Ss]print id[`:*\s]*`?(?P<id>%s)" % ID)),
    ("id", re.compile(r"(?:Resume sprint|^Sprint) (?P<id>%s)" % ID, re.M)),
    ("id", re.compile(r"run-sprint\.sh (?:--resume )?(?P<id>%s)" % ID)),
    ("id", re.compile(r"fleet\.py (?:dispatch|queue|send [A-Za-z0-9_-]+) (?P<id>%s)" % ID)),
]


def clean_id(raw):
    i = raw.strip().rstrip(".,;:)`*'\"")
    if re.search(r"\.(md|txt|zip|json|js|py|sh|csv|xlsx?|pptx?|html)$", i, re.I):
        return None
    if not (DATE_IN.search(i) or re.fullmatch(r"(smoke|test)-\d{3}", i)):
        return None
    return i


def line_host(text):
    t = text.lower()
    for word, host in (("macbook", "macbook"), ("agent2", "agent2"), ("agent-2", "agent2"), ("agent 2", "agent2"),
                       ("agent1", "agent1"), ("agent-1", "agent1"), ("workstation", "pc")):
        if word in t:
            return host
    return None


def line_account(text):
    m = re.search(r"[A-Za-z0-9._-]+@(cybernovaequity\.com|gmail\.com|nasarai\.com|elliotl\.im)", text)
    if m:
        return m.group(0)
    t = text.lower()
    for word, acct in ACCOUNT_WORDS.items():
        if word + " account" in t or word + " max" in t:
            return acct
    return None


def pc_home():
    """The PC user's home folder (HARVEST_PC_HOME overrides it; the tests use that to keep the real home out)."""
    return Path(os.environ.get("HARVEST_PC_HOME") or Path.home())


def parse_doc(text, source):
    """Rows for every run a planning doc names. Deterministic regexes, no model."""
    lines = text.splitlines()
    title_doc = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), source)
    doc_host = line_host(title_doc)
    doc_acct = line_account(title_doc)
    rows = {}
    for n, ln in enumerate(lines):
        for kind, rx in PATTERNS:
            for m in rx.finditer(ln):
                rid = clean_id(m.group("id"))
                if not rid:
                    continue
                user = m.groupdict().get("user")
                host = USER_HOSTS.get(user) if user else None
                ctx_lines = lines[max(0, n - 40): n + 1]
                target = next((c for c in reversed(ctx_lines) if re.search(r"TARGET|Target:|Launch:", c)), "")
                host = host or line_host(ln) or line_host(target) or doc_host
                # an account is taken from the line, then from a TARGET line about the same host, then the doc title
                tgt_host = line_host(target)
                acct = (line_account(ln) or (line_account(target) if tgt_host in (None, host) else None)
                        or (doc_acct if host == doc_host else None))
                path = None
                if kind in ("path", "returns", "package"):
                    path = m.group(0)
                    if kind == "returns":
                        path = None
                launch = None
                near = " ".join(ctx_lines[-12:]) + " " + target
                if re.search(r"Path A|run-sprint\.sh", near):
                    launch = "A"
                if re.search(r"Path B|pasted by Elliot|[Pp]aste the block|Code tab", near):
                    launch = launch or "B"
                if ln.lstrip().startswith("|"):
                    cells = [c.strip(" `*") for c in ln.strip().strip("|").split("|")]
                    cells = [c for c in cells if re.search(r"[A-Za-z]{3}", c) and "/" not in c and rid not in c]
                    title = max(cells, key=len) if cells else None
                else:
                    head = next((c.lstrip("# ").strip() for c in reversed(lines[: n + 1]) if c.startswith("#")), "")
                    title = head if head and head.lower() not in GENERIC_HEADINGS else None
                    if not title and (not doc_host or host == doc_host):
                        title = title_doc      # a doc about another host's run does not lend it its title
                near_lines = " ".join(lines[n: n + 3])
                sens = None
                if (keyword_hits(rid + " " + (title or "") + " " + title_doc, STRONG_WORDS)
                        or re.search(r"Sensitive[:.]|SENSITIVE=1|--sensitive|[Rr]estricted (material|project)", near_lines)):
                    sens = True
                row = rows.setdefault(rid, {"id": rid, "title": None, "source_doc": source, "host_hint": None,
                                            "path_hint": None, "account": None, "launch_path": None,
                                            "sensitive": None, "doc_title": title_doc})
                from_table = ln.lstrip().startswith("|")
                title = re.sub(r"^\d+[.)]\s+", "", title or "") or None    # "3. First drafts" -> "First drafts"
                title = re.sub(r",?\s*\(?\bpaste\b.*$", "", title or "", flags=re.I).strip() or None
                if title and re.match(r"^[\w.-]+\.(txt|md)$", title):
                    title = None                                          # a file name is not a title
                if title and (not row["title"] or (from_table and not row.get("title_from_table"))):
                    row["title"] = title[:120]
                    row["title_from_table"] = from_table
                row["host_hint"] = row["host_hint"] or host
                row["path_hint"] = row["path_hint"] or path
                row["account"] = row["account"] or acct
                row["launch_path"] = row["launch_path"] or launch
                if sens:
                    row["sensitive"] = True
    return list(rows.values())


def doc_files(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in (".md", ".txt"))
        elif p.is_file():
            out.append(p)
    return out


def merge_rows(rows):
    by = {}
    for r in rows:
        cur = by.get(r["id"])
        if not cur:
            by[r["id"]] = dict(r)
            continue
        for k, v in r.items():
            if k == "source_doc":
                docs = cur["source_doc"].split("; ")
                if v not in docs:
                    cur["source_doc"] = "; ".join(docs + [v])
            elif k == "sensitive":
                if v is True:
                    cur[k] = True
            elif k == "title" and v and r.get("title_from_table") and not cur.get("title_from_table"):
                cur["title"], cur["title_from_table"] = v, True
            elif not cur.get(k) and v:
                cur[k] = v
    return list(by.values())


def cmd_seed(ctx, docs, out, extra=None):
    rows = []
    for f in doc_files(docs):
        rows += parse_doc(f.read_text(encoding="utf-8", errors="replace"), f.name)
    if extra:
        rows += load_json(extra, [])
    rows = merge_rows(rows)
    seed = {"generated_utc": now_utc().isoformat(timespec="seconds"), "version": VERSION,
            "docs": [str(f) for f in doc_files(docs)], "rows": rows}
    save_json(out, seed)
    print("seed: %d rows from %d docs -> %s" % (len(rows), len(seed["docs"]), out))
    return seed


def cmd_plan(ctx):
    """Section 3 sources 1 to 4 (and 5 through seeds). Source 6 and 7 run in scan."""
    rows = []
    for seed in sorted(ctx.seeds.glob("*.json")) if ctx.seeds.exists() else []:
        for r in load_json(seed, {}).get("rows", []):
            r = dict(r)
            r["source_doc"] = r.get("source_doc") or seed.name
            rows.append(r)
    local_docs = [ctx.root / "blitz", ctx.root / "intake", ctx.root / "prompts"] + sorted(ctx.root.glob("macbook-*"))
    for f in doc_files([p for p in local_docs if p.exists()]):
        rows += parse_doc(f.read_text(encoding="utf-8", errors="replace"), str(f.relative_to(ctx.root)))
    # prompt folders the workstation staged (prompts\<sprint-id>\) are runs by name
    for d in sorted((ctx.root / "prompts").glob("*")) if (ctx.root / "prompts").exists() else []:
        rid = clean_id(d.stem if d.is_file() else d.name)
        if rid:
            rows.append({"id": rid, "title": None, "source_doc": "prompts\\" + d.name, "host_hint": "agent2",
                         "path_hint": "~/orchestrator/sprints/%s" % rid, "account": None, "launch_path": "A",
                         "sensitive": True if keyword_hits(d.name, STRONG_WORDS) else None})
    # fleet.py's dispatch log: every sprint it sent, with host and account
    log = ctx.root / "state" / "dispatch-log.jsonl"
    if log.exists():
        for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("id"):
                rows.append({"id": d["id"], "title": None, "source_doc": "fleet:dispatch-log",
                             "host_hint": d.get("host"), "path_hint": "~/orchestrator/sprints/%s" % d["id"],
                             "account": d.get("account"), "launch_path": "A", "sensitive": None,
                             "model": d.get("model"), "effort": d.get("effort")})
    rows = merge_rows(rows)
    # the returns repo: runs already home
    returned = returns_index(ctx)
    for r in rows:
        r.update({"kind": "planned", "status": "planned", "already_returned": r["id"] in returned})
    known = {r["id"] for r in rows}
    for rid, rel in sorted(returned.items()):
        if rid not in known:
            rows.append({"id": rid, "title": None, "source_doc": "returns-repo", "host_hint": None, "path_hint": None,
                         "account": None, "launch_path": None, "sensitive": None, "kind": "returns-repo",
                         "status": "already_returned", "already_returned": True, "repo_path": rel})
    man = {"harvest": {"version": VERSION, "date": ctx.started.strftime("%Y-%m-%d"),
                       "started_utc": ctx.started.isoformat(timespec="seconds"), "days": ctx.days,
                       "window_start_utc": iso(ctx.window_start), "root": str(ctx.root)},
           "rows": rows}
    ctx.save(man)
    print("plan: %d rows (%d planned, %d only in the returns repo)" % (
        len(rows), sum(r["kind"] == "planned" for r in rows), sum(r["kind"] == "returns-repo" for r in rows)))
    return man


def returns_index(ctx):
    """{sprint id: path relative to the returns clone} for every RETURN.md already in the repo."""
    out = {}
    if not ctx.returns.exists():
        return out
    for f in ctx.returns.rglob("RETURN.md"):
        if ".git" in f.parts:
            continue
        out.setdefault(f.parent.name, str(f.relative_to(ctx.returns)))
    return out


# ---------------------------------------------------------------- scan: the PC itself

def read_head_tail(path, head=40, tail=20):
    try:
        with open(path, "rb") as fh:
            first = [fh.readline() for _ in range(head)]
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            last = fh.read().splitlines()[-tail:]
        dec = lambda xs: "\n".join(x.decode("utf-8", "replace") for x in xs if x)
        return dec(first), dec(last), size
    except OSError:
        return "", "", 0


def session_meta(path, head_text, tail_text, size, mtime):
    g = lambda rx, t: (re.search(rx, t) or [None, None])[1]
    stamps = lambda t: re.findall(r'"timestamp"\s*:\s*"([^"]+)"', t)
    sdir = Path(str(path)[:-6]) if str(path).endswith(".jsonl") else None
    nsub = len(list(sdir.rglob("*.jsonl"))) if sdir and sdir.is_dir() else 0
    return {"kind": "session", "file": str(path), "size": size, "mtime": mtime,
            "first_ts": (stamps(head_text) or [""])[0], "last_ts": (stamps(tail_text) or [""])[-1],
            "cwd": (g(r'"cwd"\s*:\s*"([^"]*)"', head_text) or "").replace("\\\\", "\\"),
            "version": g(r'"(?:cli_)?version"\s*:\s*"([^"]*)"', head_text) or "",
            "models": sorted(set(re.findall(r'"model"\s*:\s*"((?:claude|gpt)-[^"]*)"', head_text + "\n" + tail_text))),
            "slash": sorted(set(re.findall(r"<command-name>(/[a-z-]+)", head_text))),
            "effort": sorted(set(x.lower() for x in re.findall(r'"(?:reasoning_)?effort[a-z_]*"\s*:\s*"(low|medium|high|xhigh|max)"',
                                                                 head_text, re.I))),
            "subagent_logs": nsub, "workflows": bool(sdir and (sdir / "workflows").is_dir())}


def pc_sessions(ctx):
    out = []
    home = pc_home()
    for base, pattern in ((home / ".claude" / "projects", "*/*.jsonl"),):
        if not base.exists():
            continue
        for f in base.glob(pattern):
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if mt < ctx.window_start:
                continue
            h, t, size = read_head_tail(f)
            out.append(session_meta(f, h, t, size, mt))
    codex = home / ".codex" / "sessions"
    if codex.exists():
        day = dt.date.fromtimestamp(ctx.window_start)
        while day <= ctx.started.date():
            for f in (codex / ("%04d" % day.year) / ("%02d" % day.month) / ("%02d" % day.day)).glob("*.jsonl"):
                h, t, size = read_head_tail(f, head=8, tail=8)
                rec = session_meta(f, h, t, size, f.stat().st_mtime)
                rec["kind"] = "codex-session"
                out.append(rec)
            day += dt.timedelta(days=1)
    return out


def local_folder_record(path, why, window_start):
    """The same fields remote-find.sh emits, for a folder on the PC (pointer rows only)."""
    p = Path(path)
    newest, nbytes, nfiles, ctl = 0, 0, 0, []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in CACHE_DIRS and d != ".git"]
        depth = len(Path(dirpath).relative_to(p).parts)
        for fn in filenames:
            fp = Path(dirpath) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            nfiles += 1
            nbytes += st.st_size
            newest = max(newest, st.st_mtime)
            if fn in CONTROL_NAMES and depth <= 4 and not is_secret(fn):
                ctl.append(str(fp.relative_to(p)).replace("\\", "/"))
    ret = next((c for c in sorted(ctl, key=len) if c.endswith("RETURN.md")), None)
    rjson = None
    if ret:
        txt = (p / ret).read_text(encoding="utf-8", errors="replace")
        rjson = {"path": ret, "cksum": hashlib.sha1(txt.encode("utf-8")).hexdigest()[:16],
                 "mtime": (p / ret).stat().st_mtime, "head": "\n".join(txt.splitlines()[:20]),
                 "headings": [ln[:160] for ln in txt.splitlines() if ln.startswith("#")][:40]}
    has = lambda n: any(c == n or c.endswith("/" + n) for c in ctl)
    heads = []
    for n in ("README.md", "MANIFEST.md", "00-README.md"):
        if (p / n).exists():
            heads += [ln[:160] for ln in (p / n).read_text(encoding="utf-8", errors="replace").splitlines()
                      if ln.startswith("#")][:30]
    return {"kind": "folder", "path": str(p), "why": why, "mtime": p.stat().st_mtime, "newest": newest,
            "bytes": nbytes, "nfiles": nfiles, "recent": newest >= window_start, "has_state": has("state.json"),
            "has_progress": has("PROGRESS.md"), "has_return": has("RETURN.md"), "has_manifest": has("MANIFEST.md"),
            "has_goal": has("GOAL.txt"), "has_sprint": has("SPRINT.md"), "control": ctl[:60], "return": rjson,
            "doc_headings": heads, "manifest_overall": "", "confidential_marks": 0, "runner": {}, "hints": [],
            "states": [], "progress_head": ""}


# Top-level C:\\Projects folders that hold no runs: the control plane and its local backup mirror
PC_SKIP_TOPS = ("_control",)


def pc_run_root(path, ctx):
    """The run folder a PC path belongs to, or None. Only C:\\Projects counts (never home, temp, system or personal
    folders). A run is C:\\Projects\\<project>\\work\\<lane>\\<run>, or <...>\\loops\\<loop>, or a dated
    run folder (a sprint id) inside a project. Project-level work, hidden folders, the control plane and this
    orchestrator's own folder are not runs; .claude\\worktrees fold into their project."""
    projects = ctx.root.parent
    try:
        rel = Path(os.path.abspath(str(path))).relative_to(Path(os.path.abspath(str(projects))))
    except ValueError:
        return None
    parts = rel.parts
    if not parts or parts[0].startswith(".") or parts[0] in PC_SKIP_TOPS or parts[0].lower() == ctx.root.name.lower():
        return None
    if ".claude" in parts:
        parts = parts[:parts.index(".claude")]
    if len(parts) >= 4 and parts[1].lower() == "work":
        return str(projects.joinpath(*parts[:4]))
    for i, part in enumerate(parts):
        if part.lower() in ("loops", "loop") and i + 1 < len(parts):
            return str(projects.joinpath(*parts[:i + 2]))
    for i in range(2, len(parts) + 1):
        if clean_id(parts[i - 1]):
            return str(projects.joinpath(*parts[:i]))
    return None


def pc_registry(ctx):
    """C:\\Projects\\<project>\\.project.json `sensitivity` per project (normal, sensitive, restricted, ...)."""
    if getattr(ctx, "_registry", None) is None:
        reg = {}
        projects = ctx.root.parent
        for top in sorted(projects.iterdir()) if projects.exists() else []:
            f = top / ".project.json"
            if f.is_file():
                try:
                    tier = json.loads(f.read_text(encoding="utf-8")).get("sensitivity")
                except (OSError, ValueError, AttributeError):
                    tier = "unreadable"
                if tier:
                    reg[top.name.lower()] = str(tier).lower()
        ctx._registry = reg
    return ctx._registry


def pc_tier(path, ctx):
    try:
        rel = Path(os.path.abspath(str(path))).relative_to(Path(os.path.abspath(str(ctx.root.parent))))
    except ValueError:
        return None
    return pc_registry(ctx).get(rel.parts[0].lower()) if rel.parts else None


def pc_folders(ctx, budget=30):
    """Run folders under C:\\Projects (never personal folders): each marker file in the window, mapped to its run."""
    projects = ctx.root.parent
    roots, t0 = set(), time.time()
    for top in sorted(projects.iterdir()) if projects.exists() else []:
        if not top.is_dir() or top.name.startswith(".") or top.name in PC_SKIP_TOPS or top.name.lower() == ctx.root.name.lower():
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            here = Path(dirpath)
            if time.time() - t0 > budget:
                dirnames[:] = []
                continue
            depth = len(here.relative_to(projects).parts)
            dirnames[:] = [d for d in dirnames if d not in CACHE_DIRS and d != ".git" and not d.startswith(".")
                           and depth < 7]
            hit = [f for f in filenames if f in ("state.json", "PROGRESS.md", "RETURN.md", "MANIFEST.md")]
            if not hit:
                continue
            try:
                if max((here / f).stat().st_mtime for f in hit) < ctx.window_start:
                    continue
            except OSError:
                continue
            root = pc_run_root(here, ctx)
            if root:
                roots.add(root)
    return [local_folder_record(r, "pc", ctx.window_start) for r in sorted(roots)]


def light_sweep(ctx, peers):
    """Section 3.6: evidence that another Tailscale peer had a blitz routed to it. One pass, matching lines only."""
    home = pc_home()
    files = []
    for p in [ctx.root / "prompts", ctx.root / "blitz", ctx.root / "intake", ctx.root / "state"]:
        if p.exists():
            files += [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in (".md", ".txt", ".jsonl", ".sh")]
    files += [ctx.tools / "hosts.conf",
              Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Microsoft" / "Windows" / "PowerShell"
              / "PSReadLine" / "ConsoleHost_history.txt", home / ".bash_history", home / ".ssh" / "known_hosts"]
    names = {}
    for p in peers:
        for key in [p["name"], p.get("dns", "")] + p.get("ips", []):
            if key:
                names[key.lower()] = peer_id(p)
    keyword = re.compile(r"sprint|loop|blitz|run-sprint|orchestrator-prompts|\bscp\b|rsync", re.I)
    addr = re.compile(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+|100\.\d+\.\d+\.\d+|[a-z0-9-]+(?:\.tail[0-9a-f]+\.ts\.net)?", re.I)
    evidence = {}
    for f in files:
        if not f.exists() or f.stat().st_size > 20 * 1024 ** 2:
            continue
        is_kh = f.name == "known_hosts"
        for n, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not is_kh and not keyword.search(ln):
                continue
            for tok in set(addr.findall(ln)):
                key = tok.split("@")[-1].lower().split(".tail")[0]
                peer = names.get(key) or names.get(tok.lower())
                if not peer:
                    continue
                if is_kh:   # host names only; the key that follows them is never shown
                    field = (ln.split() or [""])[0]
                    line = "hashed host entry" if field.startswith("|") else "host entry for " + field[:100]
                else:
                    line = "[line withheld: it may hold a credential]" if CREDENTIAL_LINE.search(ln) else ln.strip()[:160]
                ev = evidence.setdefault(peer, [])
                if len(ev) < 3:
                    ev.append({"file": str(f), "line": n, "text": line})
    return evidence


def norm_host(s):
    """agent’s mac mini, agents-mac-mini-1 and Agents-Mac-mini.local compare equal enough: letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def peer_id(p):
    return p.get("dns") or p.get("name") or (p.get("ips") or ["?"])[0]


def never_scan_peer(p):
    keys = [p.get("name", ""), p.get("dns", "")] + list(p.get("ips") or [])
    return any(k in NEVER_SCAN or norm_host(k).startswith("jjess") for k in keys if k)


def mark_fleet_peers(peers, hosts, hinfo):
    """Tag each Tailscale peer with the hosts.conf row it is (by IP, DNS name, ssh target or hostname), so a scanned
    Mac is never listed as a possible other host."""
    keys = {}
    for h in hosts:
        tgt = (h.get("ssh") or "").split("@")[-1].lower()
        hn = (hinfo.get(h["name"]) or {}).get("hostname", "")
        for k in (h["name"], tgt, tgt.split(".")[0] if not re.match(r"^\d+\.\d+\.\d+\.\d+$", tgt) else tgt,
                  hn, hn.split(".")[0]):
            if k and k != "local":
                keys[k.lower()] = h["name"]
                keys[norm_host(k)] = h["name"]
    for p in peers:
        hit = None
        for k in [p.get("dns", ""), p.get("name", "")] + list(p.get("ips") or []):
            if k and (keys.get(k.lower()) or keys.get(norm_host(k))):
                hit = keys.get(k.lower()) or keys.get(norm_host(k))
                break
        p["fleet"] = "pc" if p.get("self") else hit
        p["never_scan"] = never_scan_peer(p)
    return peers


def tailscale_peers():
    try:
        canned = os.environ.get("HARVEST_TAILSCALE_JSON")     # tests: a saved `tailscale status --json`
        if canned:
            data = json.loads(Path(canned).read_text(encoding="utf-8"))
        else:
            r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, timeout=20)
            data = json.loads(r.stdout.decode("utf-8", "replace"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    peers = []
    me = data.get("Self") or {}
    for p in [me] + list((data.get("Peer") or {}).values()):
        if not p:
            continue
        peers.append({"name": (p.get("HostName") or "").lower(), "dns": (p.get("DNSName") or "").split(".")[0].lower(),
                      "self": p is me,
                      "ips": [ip for ip in p.get("TailscaleIPs") or [] if "." in ip], "online": bool(p.get("Online")),
                      "os": p.get("OS", "")})
    return peers


# ---------------------------------------------------------------- scan: remote hosts

def reach(ctx, host):
    code, out, err, timed = ssh_cmd(ctx, host, "hostname", timeout=REACH_SECONDS + 12, connect=REACH_SECONDS)
    if code == 0:
        return True, out.decode("utf-8", "replace").strip()
    return False, ("timed out" if timed else err.decode("utf-8", "replace").strip()[-200:] or "exit %s" % code)


def remote_scan(ctx, host, named_paths):
    script = (HERE / "remote-find.sh").read_bytes().replace(b"\r\n", b"\n")
    args = ["--days", str(ctx.days), "--find-seconds", str(SLOW_FIND_SECONDS if ctx.slow else FIND_SECONDS)]
    for p in sorted(set(named_paths)):
        args += ["--path", p]
    remote = "sh -s -- " + " ".join(shlex.quote(a) for a in args)
    code, out, err, timed = ssh_cmd(ctx, host, remote, timeout=SLOW_SECONDS if ctx.slow else SCAN_SECONDS, data=script)
    recs, bad = [], 0
    for ln in out.decode("utf-8", "replace").splitlines():
        if not ln.startswith("{"):
            continue
        try:
            recs.append(json.loads(ln))
        except ValueError:
            bad += 1
    done = any(r.get("kind") == "done" for r in recs)
    ctx.scan_dir.mkdir(parents=True, exist_ok=True)
    (ctx.scan_dir / ("%s-%s.jsonl" % (host["name"], ctx.started.strftime("%Y%m%dT%H%M")))).write_bytes(out)
    info = {"partial": not done, "timed_out": timed, "bad_lines": bad, "exit": code,
            "stderr": err.decode("utf-8", "replace").strip()[-300:]}
    return recs, info


# ---------------------------------------------------------------- scan: sensitivity, status, push score

def classify(folder, row):
    """Section 5.4, in order. Returns (True|False|'unknown', reason)."""
    if folder is None or folder.get("missing"):
        return ("unknown", "no folder data") if row.get("sensitive") is not True else (True, "named restricted in its plan")
    name = posixpath.basename(folder["path"].replace("\\", "/"))
    if (folder.get("runner") or {}).get("sensitive"):
        return True, "runner config SENSITIVE=1"
    if row.get("sensitive") is True:
        return True, "restricted in its plan or prompt (%s)" % (row.get("source_doc") or "")[:60]
    strong = keyword_hits(name + " " + " ".join(folder.get("control") or []), STRONG_WORDS)
    if strong:
        return True, "keyword '%s' in folder or file names" % strong[0]
    if folder.get("confidential_marks"):
        return True, "package README/MANIFEST marks it confidential or a sensitive project"
    heads = " ".join((folder.get("doc_headings") or []) + ((folder.get("return") or {}).get("headings") or []))
    strong = keyword_hits(heads, STRONG_WORDS)
    if strong:
        return True, "keyword '%s' in README/MANIFEST/RETURN headings" % strong[0]
    if re.match(r"(?i)\s*overall\s*:\s*normal", folder.get("manifest_overall") or ""):
        return False, "package MANIFEST says Overall: normal"
    soft = keyword_hits(name + " " + heads, SOFT_WORDS)
    if soft:
        return True, "keyword '%s' in folder name or headings" % soft[0]
    return False, "no restricted signal (runner flag, plan, package statement, keyword screen)"


def run_status(folder):
    if not folder or folder.get("missing"):
        return "missing"
    st = ((folder.get("runner") or {}).get("status") or "").strip().upper()
    if st:
        return st
    head = (folder.get("return") or {}).get("head") or ""
    m = re.search(r"(?im)^\W*(?:\d+\.\s*)?status\W+\s*\**\s*([A-Z_]{3,})", head)
    if folder.get("has_return"):
        return m.group(1) if m else "finished"
    if folder.get("newest") and time.time() - float(folder["newest"]) < RUNNING_MIN * 60:
        return "running"
    if folder.get("has_state") or folder.get("has_progress"):
        return "partial (no RETURN)"
    return "no RETURN"


def return_sprint_id(folder):
    head = (folder.get("return") or {}).get("head") or ""
    m = re.search(r"(?im)sprint id\W*\s*`?([A-Za-z0-9][A-Za-z0-9._-]{2,90})", head)
    if m and clean_id(m.group(1)):
        return clean_id(m.group(1))
    m = re.search(r"(?im)^#\s*RETURN\W+([A-Za-z0-9][A-Za-z0-9._-]{2,90})", head)
    return clean_id(m.group(1)) if m and clean_id(m.group(1)) else None


def next_reset(account, t):
    rule = RESETS.get(account)
    if rule is None:
        return None
    if isinstance(rule, str):
        end = parse_time(rule)
        return end if end and end > t else None
    wd, hh, mm = rule
    day = dt.datetime.fromtimestamp(t, dt.timezone.utc)
    cand = day.replace(hour=hh, minute=mm, second=0, microsecond=0) + dt.timedelta(days=(wd - day.weekday()) % 7)
    if cand.timestamp() <= t:
        cand += dt.timedelta(days=7)
    return cand.timestamp()


def push_score(folder, sessions, host, account, row=None):
    """Section 3.7: count the five signal kinds (model, intensity, location, timing, shape)."""
    sig = []
    runner = folder.get("runner") or {}
    hints = " ".join(folder.get("hints") or [])
    models = set()
    for s in sessions:
        models.update(s.get("models") or [])
    if runner.get("model"):
        models.add(runner["model"])
    if (row or {}).get("model"):
        models.add(row["model"])
    if any(LATEST_MODELS.search(m.replace("claude-", "")) for m in models) or re.search(r"fable 5\.1|opus 5\.5|claude-(fable-5-1|opus-5-5)", hints):
        sig.append("model: %s" % (", ".join(sorted(models)) or "named in the prompt")[:80])
    why = []
    if re.search(r"(effort|at) (high|xhigh|max)|(high|xhigh|max) effort|ultracode|/goal|/loop|workflow tool|agent tool|max_hours", hints):
        why.append("prompt: " + hints[:60])
    if runner.get("max_hours") or "MAX_HOURS" in (runner.get("config_keys") or []):
        why.append("MAX_HOURS set")
    for s in sessions:
        if (s.get("subagent_logs") or 0) >= 2:
            why.append("%d subagent logs" % s["subagent_logs"])
        if s.get("workflows"):
            why.append("Workflow used")
        if set(s.get("slash") or []) & {"/goal", "/loop", "/ultracode"}:
            why.append("slash " + ",".join(sorted(set(s["slash"]) & {"/goal", "/loop", "/ultracode"})))
        if set(s.get("effort") or []) & {"high", "xhigh", "max"}:
            why.append("effort " + ",".join(s["effort"]))
        a, b = parse_time(s.get("first_ts")), parse_time(s.get("last_ts"))
        if a and b and b - a > 2 * 3600:
            why.append("session ran %.1f h" % ((b - a) / 3600))
    a, b = parse_time(runner.get("started")), parse_time(runner.get("ended"))
    if a and b and b - a > 2 * 3600:
        why.append("runner ran %.1f h" % ((b - a) / 3600))
    if why:
        sig.append("intensity: " + "; ".join(dict.fromkeys(why))[:120])
    if host != "pc" or (account in SPRINT_ACCOUNTS):
        sig.append("location: %s%s" % (host, (" on " + account) if account else ""))
    starts = [parse_time(s.get("first_ts")) for s in sessions] + [parse_time(runner.get("started"))]
    starts = [x for x in starts if x]
    start = min(starts) if starts else None
    if start and account:
        r = next_reset(account, start)
        if r and r - start <= 48 * 3600:
            sig.append("timing: started %s, %.0f h before %s's %s" % (
                iso(start), (r - start) / 3600, account.split("@")[0],
                "end" if isinstance(RESETS.get(account), str) else "weekly reset"))
    shape = [n for n, k in (("state.json", "has_state"), ("PROGRESS.md", "has_progress"), ("RETURN.md", "has_return"),
                            ("MANIFEST.md", "has_manifest"), ("GOAL.txt", "has_goal"), ("SPRINT.md", "has_sprint"))
             if folder.get(k)]
    if shape:
        sig.append("shape: " + ", ".join(shape))
    return len(sig), sig


def host_account(h, folder, row, key_to_account):
    runner_acct = ((folder or {}).get("runner") or {}).get("account") or ""
    if runner_acct in key_to_account:
        return key_to_account[runner_acct]
    if "@" in runner_acct:
        return runner_acct
    if (row or {}).get("account"):
        return row["account"]
    return h.get("account") if h.get("account") and "@" in h.get("account", "") else None


def pattern_hold(path, sens):
    """Why a push scoring 4+ still waits for Elliot's confirm instead of being copied: a pattern guess is not enough
    for restricted material or for a Desktop scratch workspace (it can hold anything a session touched)."""
    if sens is True:
        return "restricted: copied only if Elliot confirms"
    if "/Library/Application Support/Claude/scratch-workspaces/" in path.replace("\\", "/"):
        return "a Claude Desktop scratch workspace: copied only if Elliot confirms"
    return None


def run_base(rid):
    """L6-jev-conformed-2026-09-30-try1 and -v2 belong to L6-jev-conformed-2026-09-30."""
    return re.sub(r"-(try|v|attempt)\d+$", "", rid or "")


def propagate_sensitivity(rows):
    """One run, one answer: when any row of a run is restricted, every row of that run is (its returns copy,
    an aborted try, a PC copy), so no part of it reaches the Project."""
    restricted = {}
    for r in rows:
        if r.get("sensitive") is True:
            for k in {run_base(r.get("id")), run_base(r.get("return_id"))} - {""}:
                restricted.setdefault(k, r["id"])
    for r in rows:
        if r.get("sensitive") is True:
            continue
        for k in {run_base(r.get("id")), run_base(r.get("return_id"))} - {""}:
            if k in restricted:
                r["sensitive"], r["sensitive_why"] = True, "same run as %s, which is restricted" % restricted[k]
                break


def cmd_scan(ctx):
    man = ctx.load()
    if not man.get("rows"):
        man = cmd_plan(ctx)
    rows = man["rows"]
    hosts = ctx.hosts()
    key_to_account = {h.get("account_key"): h.get("account") for h in hosts if h.get("account_key") not in (None, "-", "PENDING")}
    hinfo, folders_by_host, sessions_by_host, prompts_by_host, zips = {}, {}, {}, {}, []
    peers = tailscale_peers()
    for h in hosts:
        name = h["name"]
        if name == "pc":
            t0 = time.time()
            folders_by_host[name] = pc_folders(ctx)
            sessions_by_host[name] = pc_sessions(ctx)
            have = {os.path.normcase(f["path"]) for f in folders_by_host[name]}
            for root in sorted({pc_run_root(x.get("cwd"), ctx) for x in sessions_by_host[name] if x.get("cwd")} - {None}):
                if os.path.normcase(root) not in have and os.path.isdir(root):
                    folders_by_host[name].append(local_folder_record(root, "pc-session", ctx.window_start))
            hinfo[name] = {"reachable": True, "hostname": os.environ.get("COMPUTERNAME", "pc"), "partial": False,
                           "elapsed": round(time.time() - t0, 1), "home": str(pc_home())}
            continue
        ok, detail = reach(ctx, h)
        if not ok:
            hinfo[name] = {"reachable": False, "error": detail, "ssh": h["ssh"]}
            continue
        named = []
        for r in rows:
            if r.get("kind") != "planned" or not r.get("path_hint"):
                continue
            if r.get("host_hint") in (name, None):
                named.append(r["path_hint"].replace("/Users/%s/" % r["path_hint"].split("/")[2], "~/")
                             if r["path_hint"].startswith("/Users/") else r["path_hint"])
        t0 = time.time()
        recs, info = remote_scan(ctx, h, named)
        hrec = next((r for r in recs if r.get("kind") == "host"), {})
        hinfo[name] = dict(info, reachable=True, hostname=detail, elapsed=round(time.time() - t0, 1),
                           home=hrec.get("home", ""), account_hint=hrec.get("account_hint", ""),
                           warnings=[r["msg"] for r in recs if r.get("kind") == "warn"])
        folders_by_host[name] = [r for r in recs if r.get("kind") == "folder"]
        sessions_by_host[name] = [r for r in recs if r.get("kind") == "session"]
        prompts_by_host[name] = [r for r in recs if r.get("kind") == "prompt"]
        zips += [dict(r, host=name) for r in recs if r.get("kind") == "zip"]

    # match planned rows to folders (section 5.1)
    extra_rows = []
    claimed = {}
    for r in rows:
        if r.get("kind") not in ("planned", "returns-repo"):
            continue
        best = None
        for h in hosts:
            home = hinfo.get(h["name"], {}).get("home", "")
            for f in folders_by_host.get(h["name"], []):
                if f.get("missing"):
                    continue
                fp = f["path"].replace("\\", "/")
                reason = None
                if posixpath.basename(fp) == r["id"]:
                    reason = "folder name is the sprint id"
                elif r.get("path_hint") and home and expand_hint(r["path_hint"], home) == fp:
                    reason = "folder is the path the plan names"
                elif any(r["id"] in s for s in f.get("states") or []) or r["id"] in (f.get("progress_head") or ""):
                    reason = "state.json or PROGRESS.md names the sprint id"
                elif (f.get("return") or {}) and return_sprint_id(f) == r["id"]:
                    reason = "RETURN.md names the sprint id"
                elif r.get("match_words") and keyword_hits(posixpath.basename(fp), [w.lower() for w in r["match_words"]]):
                    reason = "folder name has a word the plan names (%s)" % ", ".join(
                        keyword_hits(posixpath.basename(fp), [w.lower() for w in r["match_words"]]))
                if reason and (best is None or (h["name"] == r.get("host_hint") and best[0] != r.get("host_hint"))):
                    best = (h["name"], f, reason)
        if best and r.get("match_words"):
            # a run named only by topic in a thread: every folder whose name carries the topic becomes its own row
            for h in hosts:
                for f in folders_by_host.get(h["name"], []):
                    hits = keyword_hits(posixpath.basename(f["path"].replace("\\", "/")), [w.lower() for w in r["match_words"]])
                    if hits and not f.get("missing"):
                        extra_rows.append(dict(r, id=posixpath.basename(f["path"].replace("\\", "/")), kind="planned",
                                               status="pointer" if h["name"] == "pc" else "found", host=h["name"],
                                               path=f["path"], topic_row=r["id"],
                                               match_reason="folder name has '%s', named in %s" % (hits[0], r["source_doc"])))
                        claimed.setdefault((h["name"], f["path"]), []).append(extra_rows[-1]["id"])
            r.update({"status": "expanded", "note": "matched %d folders by topic" % sum(1 for x in extra_rows if x.get("topic_row") == r["id"])})
        elif best:
            hname, f, reason = best
            claimed.setdefault((hname, f["path"]), []).append(r["id"])
            r.update({"status": "found", "host": hname, "path": f["path"], "match_reason": reason})
        else:
            hh = r.get("host_hint")
            staged = [dict(p, host=hn) for hn, ps in prompts_by_host.items() for p in ps if Path(p["name"]).stem == r["id"]]
            if hh in NEVER_SCAN:
                r.update({"status": "not_scanned", "note": "named on Jjess's Mac mini, which is never scanned"})
            elif hh and hinfo.get(hh, {}).get("reachable") is False:
                r.update({"status": "not_found_yet", "note": "%s was unreachable; looked for again next run" % hh})
            elif r.get("already_returned"):
                r.update({"status": "already_returned", "note": "its RETURN.md is in the returns repo; no folder found"})
            elif staged:
                r.update({"status": "not_found", "note": "prompt staged on %s in %s but no run folder: never started" % (
                    staged[0]["host"], staged[0]["dir"])})
            elif r.get("note"):     # the plan or thread already says what happened to it
                r.update({"status": "not_found", "note": "no folder found; its source says: " + r["note"]})
            else:
                r.update({"status": "not_found", "note": "no folder or session on any reachable host: probably never launched"
                          + ("" if all(v.get("reachable") for v in hinfo.values()) else ", or on a host that was unreachable")})

    rows.extend(extra_rows)
    choices = load_json(ctx.out / "choices.json", {"confirm": [], "drop": []})
    # every folder: classify, status, push score; add unmatched ones as discovered or pattern rows
    folder_rows, returns_copies = [], {}
    for h in hosts:
        name = h["name"]
        home = hinfo.get(name, {}).get("home", "")
        sess_by_root = {}
        for s in sessions_by_host.get(name, []):
            cwd = (s.get("cwd") or "").replace("\\", "/")
            root = run_root(cwd, home.replace("\\", "/")) if name != "pc" else pc_run_root(s.get("cwd"), ctx)
            if root:
                sess_by_root.setdefault(root.replace("\\", "/"), []).append(s)
        sprint_dirs = {f["path"].replace("\\", "/") for f in folders_by_host.get(name, [])
                       if "/orchestrator/sprints/" in f["path"].replace("\\", "/")}
        for f in folders_by_host.get(name, []):
            if f.get("missing"):
                continue
            fp = f["path"].replace("\\", "/")
            twin = fp.replace("/orchestrator/returns/", "/orchestrator/sprints/")
            if twin != fp and twin in sprint_dirs and not claimed.get((name, f["path"])):
                returns_copies.setdefault((name, twin), fp)   # fleet.py pull carries it; the run is its sprint folder
                continue
            ids = claimed.get((name, f["path"]), [])
            rowref = next((r for r in rows if r["id"] in ids), None) if ids else None
            acct = host_account(h, f, rowref, key_to_account)
            sess = sess_by_root.get(fp, [])
            score, signals = push_score(f, sess, name, acct, rowref)
            sens, why = classify(f, rowref or {})
            tier = pc_tier(f["path"], ctx) if name == "pc" else None
            if tier and tier != "normal" and sens is not True:
                sens, why = True, "project registry marks it %s" % tier
            common = {"bytes": f.get("bytes"), "nfiles": f.get("nfiles"), "newest": f.get("newest"),
                      "recent": f.get("recent"), "run_status": run_status(f), "return": f.get("return"),
                      "push_score": score, "push_signals": signals, "sensitive": sens, "sensitive_why": why,
                      "account": acct, "sessions": len(sess), "return_id": return_sprint_id(f) if f.get("return") else None}
            if ids:
                for r in rows:
                    if r["id"] in ids:
                        r.update(common)
                continue
            in_orch = "/orchestrator/sprints/" in fp or "/orchestrator/returns/" in fp
            confirmed = posixpath.basename(fp) in choices.get("confirm", []) or fp in choices.get("confirm", [])
            empty = f.get("link") or (f.get("nfiles") == 0)
            if name == "pc":
                kind, status = "pc", ("pointer" if score >= PUSH_HARVEST or f.get("has_return") else "listed")
            elif in_orch:
                kind, status = "discovered", ("found" if f.get("recent") else "old")
            elif posixpath.basename(fp) in choices.get("drop", []) or fp in choices.get("drop", []):
                kind, status = "pattern", "dropped"
            elif empty and (score >= PUSH_CONFIRM or confirmed):
                kind, status = "pattern", "listed"
                common["note"] = ("a symbolic link, never followed, so nothing to copy" if f.get("link")
                                  else "0 readable files, so nothing to copy (empty, or not readable over SSH)")
            elif score >= PUSH_HARVEST or confirmed:
                hold = None if confirmed else pattern_hold(fp, sens)
                kind, status = "pattern", ("confirm" if hold else "found")
                if hold:
                    common["hold"] = hold
            elif score >= PUSH_CONFIRM:
                kind, status = "pattern", "confirm"
                hold = pattern_hold(fp, sens)
                if hold:
                    common["hold"] = hold
            else:
                kind, status = "discovered", "listed"
            rid = return_sprint_id(f) or posixpath.basename(fp)
            folder_rows.append(dict(common, id=rid, title=None, kind=kind, status=status,
                                    source_doc=("pattern:%s" % name) if kind == "pattern" else ("discovered:%s" % name),
                                    host=name, path=f["path"], match_reason=f.get("why")))
    # sessions whose run folder the finder did not describe: one confirm row per run root (Macs; on the PC every run
    # root already has a folder record). Sessions with no run root (home, temp, system folders) are only summarised.
    loose = {}
    for h in hosts:
        name = h["name"]
        if name == "pc":
            continue
        home = hinfo.get(name, {}).get("home", "").replace("\\", "/")
        paths = {f["path"].replace("\\", "/") for f in folders_by_host.get(name, [])}
        acct = h.get("account") if "@" in (h.get("account") or "") else None
        groups = {}
        for s_ in sessions_by_host.get(name, []):
            cwd = (s_.get("cwd") or "").replace("\\", "/")
            root = run_root(cwd, home) if cwd else None
            if root and root in paths:
                continue
            groups.setdefault(root, []).append(s_)
        for root, ss in groups.items():
            score, signals = push_score({"runner": {}, "hints": []}, ss, name, acct)
            if root is None:
                strong = [x for x in ss if push_score({"runner": {}, "hints": []}, [x], name, acct)[0] >= PUSH_HARVEST]
                if strong:
                    ts = sorted(t for x in strong for t in (x.get("first_ts"), x.get("last_ts")) if t)
                    loose[name] = {"sessions": len(strong), "first": ts[0] if ts else "", "last": ts[-1] if ts else "",
                                   "models": sorted({m for x in strong for m in x.get("models") or []}),
                                   "cwds": sorted({(x.get("cwd") or "?").replace(home, "~", 1) for x in strong})[:5]}
                continue
            if score < PUSH_CONFIRM:
                continue
            base = posixpath.basename(root)
            if base in choices.get("drop", []) or root in choices.get("drop", []):
                status = "dropped"
            else:
                status = "confirm"
            hits = keyword_hits(root, STRONG_WORDS + SOFT_WORDS)
            folder_rows.append({"id": base, "title": None, "kind": "pattern", "status": status,
                                "source_doc": "pattern:%s" % name, "host": name, "path": root,
                                "match_reason": "session logs only (%d); the finder did not list the folder" % len(ss),
                                "push_score": score, "push_signals": signals, "sessions": len(ss),
                                "sensitive": True if hits else None,
                                "sensitive_why": ("keyword '%s' in its path" % hits[0]) if hits else "folder not read"})
    rows[:] = [r for r in rows if r.get("kind") not in ("discovered", "pattern", "pc")] + folder_rows
    for r in rows:
        rc = returns_copies.get((r.get("host"), (r.get("path") or "").replace("\\", "/")))
        if rc:
            r["returns_copy"] = rc
    propagate_sensitivity(rows)
    # sweep for other hosts (section 3.6)
    evidence = light_sweep(ctx, peers)
    mark_fleet_peers(peers, hosts, hinfo)
    fleet_names = {peer_id(p) for p in peers if p.get("fleet")} | {h["name"] for h in hosts}
    man["harvest"].update({"scanned_utc": now_utc().isoformat(timespec="seconds"), "hosts": hinfo,
                           "tailscale": peers, "zips": zips, "loose_sessions": loose,
                           "other_hosts": {k: v for k, v in evidence.items() if k not in fleet_names}})
    ctx.save(man)
    for name, info in hinfo.items():
        print("%-8s %s" % (name, ("reachable, %d folders, %d sessions%s" % (
            len(folders_by_host.get(name, [])), len(sessions_by_host.get(name, [])), ", PARTIAL" if info.get("partial") else ""))
            if info.get("reachable") else "UNREACHABLE: " + info.get("error", "")))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("scan: " + ", ".join("%s %d" % kv for kv in sorted(counts.items())))
    return man


# ---------------------------------------------------------------- copy

def tar_remote_command(path, control_only=False):
    parent, name = posixpath.split(path.rstrip("/"))
    excl = " ".join(shlex.quote("--exclude=" + e) for e in TAR_EXCLUDES)
    if control_only:
        inc = " ".join(shlex.quote("%s/%s" % (name, c)) for c in sorted(CONTROL_NAMES))
        return "cd %s && find %s -maxdepth 4 -type f \\( %s \\) -print | tar -czf - %s -T -" % (
            shlex.quote(parent), shlex.quote(name),
            " -o ".join("-name %s" % shlex.quote(c) for c in sorted(CONTROL_NAMES)), excl)
    return "cd %s && tar -czf - %s %s" % (shlex.quote(parent), excl, shlex.quote(name))


def member_allowed(rel, size, control_only):
    """The local half of the never-copy rule (the remote half is TAR_EXCLUDES). Returns (ok, reason)."""
    parts = [p for p in rel.split("/") if p]
    if not parts or rel.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", rel):
        return False, "unsafe path"
    if is_secret(rel):
        return False, "credential rule"
    if any(p in CACHE_DIRS for p in parts):
        return False, "cache"
    if ".git" in parts:
        i = parts.index(".git")
        sub = "/".join(parts[i + 1:])
        if sub and not GIT_KEEP.match(sub):
            return False, "git internals"
    if JUNK.match(parts[-1]):
        return False, "macOS junk file"
    if parts[-1].endswith(".log") and size > BIG_LOG:
        return False, "log over 50 MB"
    if control_only and parts[-1] not in CONTROL_NAMES:
        return False, "over the 2 GB cap: control files only"
    return True, ""


def extract_stream(stream, dest, control_only=False):
    """Unpack a tar.gz stream into dest (whose final name replaces the archive's top folder). Returns stats."""
    dest = Path(dest)
    stats = {"files": 0, "bytes": 0, "excluded": {}, "excluded_names": [], "renamed": 0}
    with tarfile.open(fileobj=stream, mode="r|gz") as tf:
        for m in tf:
            name = m.name.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if len(parts) < 2:
                continue  # the top folder itself
            rel = "/".join(parts[1:])
            if not (m.isfile() or m.isdir()):
                stats["excluded"]["link or device"] = stats["excluded"].get("link or device", 0) + 1
                continue
            ok, why = member_allowed(rel, m.size, control_only)
            if not ok:
                stats["excluded"][why] = stats["excluded"].get(why, 0) + 1
                if why in ("credential rule", "unsafe path") and len(stats["excluded_names"]) < 40:
                    stats["excluded_names"].append(rel)
                continue
            safe = [safe_part(p) for p in parts[1:]]
            if safe != parts[1:]:
                stats["renamed"] += 1
            target = dest.joinpath(*safe)
            if m.isdir():
                os.makedirs(win_long(target), exist_ok=True)
                continue
            os.makedirs(win_long(target.parent), exist_ok=True)
            src = tf.extractfile(m)
            with open(win_long(target), "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            try:
                os.utime(win_long(target), (m.mtime, m.mtime))
            except OSError:
                pass
            stats["files"] += 1
            stats["bytes"] += m.size
    return stats


def copy_folder(ctx, host, path, dest, control_only=False):
    """Stream one remote folder to dest through ssh + tar. The remote side only reads."""
    f = ctx.fleet
    cmd = [f.SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host["ssh"], tar_remote_command(path, control_only)]
    tmp = Path(str(dest) + ".partial")
    if tmp.exists():
        shutil.rmtree(win_long(tmp))
    # stderr goes to a file so a long warning list can never block the stdout stream
    with tempfile.TemporaryFile() as errf, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                                                           creationflags=getattr(f, "NO_WINDOW", 0)) as proc:
        try:
            stats = extract_stream(proc.stdout, tmp, control_only)
            code = proc.wait(timeout=1800)
        except Exception:
            proc.kill()
            raise
        errf.seek(0)
        err = errf.read().decode("utf-8", "replace")
    if code not in (0, 1):   # bsdtar exits 1 when a file changed while it was read
        raise RuntimeError("tar over ssh exit %s: %s" % (code, err.strip()[-200:]))
    os.makedirs(win_long(tmp), exist_ok=True)
    os.replace(win_long(tmp), win_long(dest))
    stats["warnings"] = err.strip()[-200:] if err.strip() else ""
    return stats


def ledger_key(row):
    return "%s:%s" % (row["host"], row["path"])


def dest_base(row):
    p = row["path"].replace("\\", "/").rstrip("/")
    name = posixpath.basename(p)
    if "/orchestrator/returns/" in p:
        return "%s/returns/%s" % (row["host"], name)
    return "%s/%s" % (row["host"], name)


def to_copy(rows):
    pick = []
    for r in rows:
        if not r.get("path") or r.get("host") == "pc":
            continue
        if r["status"] == "found" and r.get("kind") in ("planned", "discovered", "pattern"):
            if r.get("nfiles") == 0:
                r["copy"] = "nothing to copy: 0 readable files"
                continue
            pick.append(r)
    seen, out = set(), []
    for r in pick:
        k = ledger_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def cmd_copy(ctx):
    man = ctx.load()
    rows = man["rows"]
    ledger = load_json(ctx.ledger_path, {})
    hosts = {h["name"]: h for h in ctx.hosts()}
    todo, results = [], []
    for r in to_copy(rows):
        k = ledger_key(r)
        led = ledger.get(k)
        cks = (r.get("return") or {}).get("cksum")
        same = led and led.get("cksum") == cks and led.get("newest") == r.get("newest") and led.get("nfiles") == r.get("nfiles")
        if same:
            action, dest = "unchanged", led["dest"]
        elif r.get("run_status") == "running" and led:
            action, dest = "unchanged", led["dest"]   # still running: keep the last copy until it settles
            r["note"] = "still running; last copy kept"
        else:
            base = dest_base(r)
            others = [v for kk, v in ledger.items() if kk != k and v.get("dest", "").split("-v")[0] == base]
            if others:
                base = "%s-%s" % (base, hashlib.sha1(k.encode()).hexdigest()[:6])
            n, dest = 1, base
            while (ctx.out / dest).exists() or any(v.get("dest") == dest for v in ledger.values()):
                n += 1
                dest = "%s-v%d" % (base, n)
            action = "changed" if led else "new"
        over = (r.get("bytes") or 0) > FOLDER_CAP
        todo.append((r, action, dest, over))
    need = sum(min(r.get("bytes") or 0, FOLDER_CAP) for r, a, d, o in todo if a != "unchanged")
    free = shutil.disk_usage(ctx.out if ctx.out.exists() else ctx.root).free
    man["harvest"]["disk"] = {"free": free, "need": need}
    if not ctx.dry_run and free < 2 * need:
        man["harvest"]["disk"]["refused"] = True
        for r, a, d, o in todo:
            r["copy"] = "refused: disk"
        ctx.save(man)
        print("copy refused: %s free on C:, %s needed (2 x %s)" % (human(free), human(2 * need), human(need)))
        return man
    total = 0
    for r, action, dest, over in todo:
        same_rows = [x for x in rows if x.get("host") == r["host"] and x.get("path") == r["path"]]
        if action == "unchanged":
            for x in same_rows:
                x.update({"copy": "unchanged", "dest": dest})
            print("unchanged  %-8s %s" % (r["host"], r["path"]))
            continue
        line = "%-9s  %-8s %s -> blitz-returns\\%s (%s%s)" % (action, r["host"], r["path"], dest.replace("/", "\\"),
                                                            human(r.get("bytes")), ", control files only" if over else "")
        if ctx.dry_run:
            print("would copy " + line)
            for x in same_rows:
                x.update({"copy": "dry-run (%s)" % action, "dest": dest})
            continue
        h = hosts.get(r["host"])
        try:
            st = copy_folder(ctx, h, r["path"], ctx.out / dest, control_only=over)
        except Exception as e:   # one failed folder never stops the harvest
            for x in same_rows:
                x.update({"copy": "error: %s" % str(e)[:160]})
            print("FAILED     " + line + ": " + str(e)[:160])
            continue
        total += st["bytes"]
        ledger[ledger_key(r)] = {"dest": dest, "cksum": (r.get("return") or {}).get("cksum"), "newest": r.get("newest"),
                                 "nfiles": r.get("nfiles"), "bytes": st["bytes"], "utc": now_utc().isoformat(timespec="seconds"),
                                 "versions": (ledger.get(ledger_key(r), {}).get("versions") or []) + [dest]}
        for x in same_rows:
            x.update({"copy": "over-cap: control files only" if over else ("copied" if action == "new" else "copied (changed: new version)"),
                      "dest": dest, "copied_files": st["files"], "excluded": st["excluded"],
                      "excluded_names": st["excluded_names"], "copy_warnings": st.get("warnings", "")})
        save_json(ctx.ledger_path, ledger)
        print("copied     " + line)
    for r in rows:
        if r.get("host") == "pc" and r.get("status") == "pointer" and not ctx.dry_run:
            r["dest"] = write_pointer(ctx, r)
            r["copy"] = "pointer"
    man["harvest"]["copied_bytes"] = total
    man["harvest"]["dry_run"] = ctx.dry_run
    man["harvest"]["audit"] = audit(ctx)
    ctx.save(man)
    return man


def write_pointer(ctx, r):
    """PC runs are not duplicated: blitz-returns\\pc\\<name>\\RUNS-HERE.md points at them."""
    name = safe_part(Path(r["path"]).name)
    d = ctx.out / "pc" / name
    body = "\n".join(["# %s runs on this PC" % name, "", "Path: %s" % r["path"], "Status: %s" % r.get("run_status"),
                      "Push score: %s (%s)" % (r.get("push_score"), "; ".join(r.get("push_signals") or [])),
                      "Sensitive: %s (%s)" % (r.get("sensitive"), r.get("sensitive_why")), ""])
    f = d / "RUNS-HERE.md"
    old = f.read_text(encoding="utf-8") if f.exists() else None
    if old != body:
        d.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    return "pc/" + name


def audit(ctx):
    """Every file under blitz-returns\\ whose name looks like a credential (there should be none)."""
    hits = []
    if not ctx.out.exists():
        return hits
    for dirpath, dirnames, filenames in os.walk(ctx.out):
        dirnames[:] = [d for d in dirnames if d not in (".scan", "seeds")]
        for fn in filenames:
            rel = str((Path(dirpath) / fn).relative_to(ctx.out)).replace("\\", "/")
            if rel.startswith("harvest-") or rel.startswith("HARVEST-") or rel.startswith("sync-"):
                continue
            if is_secret(rel):
                hits.append(rel)
    return hits


# ---------------------------------------------------------------- check: the credential self-test

def cmd_check(ctx, quiet=False):
    """Pack a fixture with credential, cache and git files using this PC's tar and the remote exclude list,
    unpack it with the same code path as a real copy, and assert only the allowed files land."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src" / "run-2026-10-07"
        files = {"RETURN.md": True, "PROGRESS.md": True, "work/out.md": True, "repo/.git/HEAD": True,
                 "repo/.git/config": True, "repo/.git/objects/ab/cdef": False, "repo/node_modules/x/i.js": False,
                 ".env": False, ".env.local": False, "token": False, "claude-token.txt": False, "my_secret.json": False,
                 "id_ed25519": False, "server.pem": False, "creds/credentials.json": False, "login.keychain-db": False,
                 ".npmrc": False, ".ssh/config": False, "auth.json": False, ".config/orchestrator/account": False,
                 "work/__pycache__/m.pyc": False, "notes/tokens.css": False, "api.key": False}
        for rel in files:
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("fixture\n", encoding="utf-8")
        results = []
        # the Macs run bsdtar (libarchive); Windows ships the same tar in System32, so prefer it over Git's GNU tar
        bsdtar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        tar = str(bsdtar) if os.name == "nt" and bsdtar.exists() else shutil.which("tar")
        raw_note = ""
        for label, packer in (("%s with the remote excludes" % (tar or "no tar found; python tar"), "tar"),
                              ("python tar, no excludes", "py")):
            dest = Path(tmp) / ("dest-" + packer)
            if packer == "tar" and tar:
                cmd = [tar, "-czf", "-"] + ["--exclude=" + e for e in TAR_EXCLUDES] + ["-C", str(src.parent), src.name]
                p = subprocess.run(cmd, capture_output=True)
                stream = io.BytesIO(p.stdout)
                with tarfile.open(fileobj=io.BytesIO(p.stdout), mode="r:gz") as tf:
                    sent = {m.name.split("/", 1)[1] for m in tf.getmembers() if m.isfile() and "/" in m.name}
                over_wire = sorted(k for k in sent if not files.get(k, True))
                raw_note = "remote excludes alone stopped %d of %d never-copy files%s" % (
                    sum(1 for k, v in files.items() if not v) - len(over_wire), sum(1 for v in files.values() if not v),
                    "; the local filter caught %s" % over_wire if over_wire else "")
            else:
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                    tf.add(src, arcname=src.name)
                buf.seek(0)
                stream = buf
            extract_stream(stream, dest)
            landed = {str(x.relative_to(dest)).replace("\\", "/") for x in dest.rglob("*") if x.is_file()}
            want = {k for k, v in files.items() if v}
            leaked = sorted(landed - want)
            missing = sorted(want - landed)
            results.append((label, leaked, missing))
    ok = all(not leaked and not missing for _, leaked, missing in results)
    hits = audit(ctx)
    if not quiet:
        for label, leaked, missing in results:
            print("self-test (%s): %s%s" % (label, "PASS" if not leaked and not missing else "FAIL",
                                            ("; leaked %s" % leaked if leaked else "") + ("; missing %s" % missing if missing else "")))
        if raw_note:
            print("  " + raw_note)
        print("audit of blitz-returns: %s" % ("no credential-looking files" if not hits else "FOUND %s" % hits))
    if not ok or hits:
        raise SystemExit("credential check failed; nothing will be copied")
    return True


# ---------------------------------------------------------------- push

def git(ctx, *args, check=True):
    return subprocess.run(["git", "-C", str(ctx.returns)] + list(args), capture_output=True, text=True, check=check)


def cmd_push(ctx):
    man = ctx.load()
    rows = man["rows"]
    res = {"fleet_pull": None, "added": [], "pointer": [], "skipped": [], "commit": None, "pushed": False, "error": None}
    if ctx.dry_run:
        res["error"] = "dry run: nothing pushed"
        man["harvest"]["push"] = res
        ctx.save(man)
        return man
    # 1. fleet.py pull already brings ~/orchestrator/returns from the sprint hosts (non-sensitive, not ingested)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            ctx.fleet.pull()
        res["fleet_pull"] = buf.getvalue().strip()[-400:] or "ok"
    except BaseException as e:   # pull() can sys.exit or raise on git; harvest carries on
        res["fleet_pull"] = "failed: %s %s" % (type(e).__name__, (str(e) or buf.getvalue())[-200:])
    # 2. what pull misses: Path B runs, package runs, other hosts
    added = []
    for r in rows:
        dest = r.get("dest")
        ret = (r.get("return") or {}).get("path")
        if not dest or not ret or r.get("host") == "pc" or str(r.get("copy", "")).startswith(("error", "dry-run", "refused")):
            continue
        src = ctx.out / dest / ret
        if not src.exists():
            continue
        rid = r.get("return_id") or (r["id"] if clean_id(r["id"]) else None) or safe_part(Path(r["path"]).name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,90}", rid or ""):
            res["skipped"].append("%s: no usable sprint id" % r["id"])
            continue
        target = ctx.returns / rid
        status = (target / "status").read_text(encoding="utf-8").strip() if (target / "status").exists() else ""
        if status in ("DONE", "MOVED") or (target / ".ingested").exists():
            res["skipped"].append("%s: already ingested" % rid)
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        if r.get("sensitive") is not False and ctx.push_restricted != "full":
            text = "\n".join([
                "# RETURN (pointer): %s" % rid, "",
                "Restricted or unclassified run: the full RETURN.md stays on the PC (returns repo rule: pointers only).",
                "", "1. Sprint id: %s" % rid, "2. Host and folder: %s %s" % (r["host"], r["path"]),
                "3. PC copy: C:\\Projects\\master-orchestrator\\blitz-returns\\%s\\%s" % (dest.replace("/", "\\"), ret.replace("/", "\\")),
                "4. Run status: %s" % r.get("run_status"), "5. Why restricted: %s" % r.get("sensitive_why"),
                "6. Harvested: %s" % now_utc().isoformat(timespec="seconds"), ""])
            kind = "pointer"
        else:
            kind = "full"
        cur = (target / "RETURN.md").read_text(encoding="utf-8", errors="replace") if (target / "RETURN.md").exists() else None
        if cur == text:
            continue
        if cur is not None and kind == "pointer" and not cur.startswith("# RETURN (pointer)"):
            res["skipped"].append("%s: repo already holds a return; pointer not written over it" % rid)
            continue
        target.mkdir(parents=True, exist_ok=True)
        (target / "RETURN.md").write_text(text, encoding="utf-8")
        if not (target / "host").exists():
            (target / "host").write_text(r["host"] + "\n", encoding="utf-8")
        added.append("%s/RETURN.md" % rid)
        (res["pointer"] if kind == "pointer" else res["added"]).append(rid)
        r["pushed"] = "%s/RETURN.md (%s)" % (rid, kind)
    if added:
        try:
            git(ctx, "add", "--", *sorted({a.split("/")[0] for a in added}))
            msg = "Harvest blitz returns %s\n\n%d full, %d pointer-only (restricted)." % (
                now_utc().strftime("%Y-%m-%d %H:%M UTC"), len(res["added"]), len(res["pointer"]))
            git(ctx, "commit", "-q", "-m", msg)
            res["commit"] = git(ctx, "rev-parse", "--short", "HEAD").stdout.strip()
        except subprocess.CalledProcessError as e:
            res["error"] = "commit failed: %s" % (e.stderr or e.stdout or "")[-200:]
    ahead = git(ctx, "rev-list", "--count", "@{u}..HEAD", check=False)
    if ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0"):
        for i, wait in enumerate((2, 4, 8, 16, 0)):
            p = git(ctx, "push", "-q", check=False)
            if p.returncode == 0:
                res["pushed"] = True
                break
            res["error"] = "push failed: %s" % (p.stderr or "")[-200:]
            if wait:
                time.sleep(wait)
        if not res["pushed"]:
            res["error"] = (res["error"] or "") + " (commit left local)"
    elif ahead.returncode == 0:
        res["pushed"] = res["commit"] is not None
    res["head"] = git(ctx, "rev-parse", "--short", "HEAD", check=False).stdout.strip()
    man["harvest"]["push"] = res
    ctx.save(man)
    print("push: %d full, %d pointer, commit %s, pushed %s%s" % (len(res["added"]), len(res["pointer"]), res["commit"],
                                                                res["pushed"], (" (" + res["error"] + ")") if res["error"] else ""))
    return man


# ---------------------------------------------------------------- report

def report_path(ctx, dry_run=False):
    base = "HARVEST-%s" % ctx.started.strftime("%Y-%m-%d")
    if dry_run:          # dry runs never use up a real report's name
        return ctx.out / (base + "-dryrun.md")
    p = ctx.out / (base + ".md")
    n = 1
    while p.exists():
        n += 1
        p = ctx.out / ("%s-%d.md" % (base, n))
    return p


def cell(s, n=60):
    s = str(s if s is not None else "").replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def unpacked_zips(zips, rows):
    """Zips whose name matches no folder on the same host: packages that were delivered but never unpacked."""
    out = []
    for z in zips:
        stem = re.sub(r"(?i)\.zip$", "", posixpath.basename(z.get("path", "")))
        base = re.sub(r"-v\d+$", "", stem)
        names = {posixpath.basename(r["path"].replace("\\", "/")) for r in rows if r.get("host") == z.get("host") and r.get("path")}
        names |= {r["id"] for r in rows if r.get("host") == z.get("host") and r.get("status") in ("found", "confirm")}
        if not any(n and (n.startswith(base) or base.startswith(n)) for n in names):
            out.append(z)
    return out


def cmd_report(ctx, path=None):
    man = ctx.load()
    hv, rows = man["harvest"], man["rows"]
    hosts = hv.get("hosts", {})
    path = Path(path) if path else report_path(ctx, dry_run=bool(hv.get("dry_run")))
    L = ["# Blitz harvest %s" % hv.get("date", ""), "",
         "Window: last %s days (since %s). Run %s from %s. Hosts: %s." % (
             hv.get("days"), hv.get("window_start_utc"), now_utc().strftime("%Y-%m-%d %H:%M UTC"), hv.get("root"),
             ", ".join("%s %s" % (k, "ok" if v.get("reachable") and not v.get("partial") else
                                  ("partial" if v.get("reachable") else "unreachable")) for k, v in hosts.items())),
         ""]
    if hv.get("dry_run"):
        L += ["**Dry run: nothing was copied or pushed.**", ""]
    # 1. needed from Elliot
    need = []
    for k, v in hosts.items():
        if not v.get("reachable"):
            step = ("wake the MacBook (lid open, plugged in) and check Tailscale is connected" if k == "macbook" else
                    "check the Mac is on and on Tailscale; if it answers but refuses the key, run on the PC: "
                    "`type %%USERPROFILE%%\\.ssh\\id_ed25519.pub | ssh %s \"cat >> ~/.ssh/authorized_keys\"`" % v.get("ssh", k))
            need.append("- %s unreachable (%s): %s. Then re-run `harvest.py all`." % (k, cell(v.get("error"), 60), step))
        elif v.get("partial"):
            need.append("- %s scan was cut at its time limit: re-run `harvest.py scan --host %s --slow`." % (k, k))
    for r in rows:
        if str(r.get("copy", "")).startswith("over-cap"):
            need.append("- %s on %s is %s: only its control files were copied. Say whether to copy the rest." % (
                r["id"], r["host"], human(r.get("bytes"))))
        if r.get("status") == "found" and r.get("sensitive") == "unknown":
            need.append("- Confirm whether %s on %s is restricted (%s). Until then it stays PC only." % (
                r["id"], r["host"], r.get("sensitive_why")))
        if str(r.get("copy", "")).startswith("error"):
            need.append("- Copy of %s on %s failed: %s" % (r["id"], r["host"], cell(r["copy"], 100)))
    if (hv.get("disk") or {}).get("refused"):
        need.append("- Copy refused: %s free on C:, %s needed. Free space, then re-run." % (
            human(hv["disk"]["free"]), human(2 * hv["disk"]["need"])))
    push = hv.get("push") or {}
    if push.get("error") and not hv.get("dry_run"):
        need.append("- Returns push: %s" % cell(push["error"], 120))
    if hv.get("audit"):
        need.append("- Credential-looking files found in blitz-returns: %s" % ", ".join(hv["audit"][:5]))
    S1 = ["## 1. Needed from Elliot", ""] + (need or ["Nothing."]) + [""]
    home_of = lambda r: hosts.get(r.get("host"), {}).get("home") or "~"
    short = lambda r, n=48: cell((r.get("path") or "").replace(home_of(r), "~", 1), n)
    restricted = lambda r: r.get("sensitive") is True
    # 2. found and copied (one line per run; a host's returns\<id> copy travels with its sprint folder)
    found = [r for r in rows if r.get("status") == "found" and r.get("host") != "pc"]
    found.sort(key=lambda r: (r.get("kind") != "planned", HOST_ORDER.index(r["host"]) if r["host"] in HOST_ORDER else 9, r["id"]))
    S2 = ["## 2. Found and copied", "", "| Sprint | Title | Host | Path | Size | Status | Copy | Destination |",
          "|---|---|---|---|---|---|---|---|"]
    T2 = ["| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        cell(r["id"], 44), cell(r.get("title") or "", 34), r["host"], short(r), human(r.get("bytes")),
        cell(r.get("run_status"), 18), cell(r.get("copy", "not copied"), 22),
        cell("blitz-returns\\" + r["dest"].replace("/", "\\") if r.get("dest") else "", 50)) for r in found] or ["| none |  |  |  |  |  |  |  |"]
    pcs = [r for r in rows if r.get("host") == "pc" and r.get("status") in ("pointer", "found")]
    P2 = (["", "PC runs (pointer files in blitz-returns\\pc\\, not duplicated): " +
           ", ".join(cell(Path(r["path"]).name, 40) for r in pcs[:15]) + (" and %d more" % (len(pcs) - 15) if len(pcs) > 15 else "")]
          if pcs else []) + [""]
    # 3. not found
    nf = [r for r in rows if r.get("kind") == "planned" and r.get("status") in ("not_found", "not_found_yet", "not_scanned", "already_returned")]
    S3 = ["## 3. Not found anywhere", ""] + (["- %s (%s, from %s): %s" % (
        r["id"], r.get("host_hint") or "host?", cell(r.get("source_doc"), 50), r.get("note") or r["status"]) for r in nf]
        or ["None."]) + [""]
    # 4. discovered and pattern-recognized: one line per run folder, restricted ones only by reference
    S4 = ["## 4. Discovered runs not in any plan, and pushes to confirm", ""]
    T4 = []
    disc = [r for r in rows if r.get("kind") == "discovered" and r.get("status") == "found" and r.get("host") != "pc"]
    if disc:
        T4.append("- Harvested sprint folders no plan names: " + ", ".join(
            "%s (%s)" % (r["id"], r["host"]) for r in sorted(disc, key=lambda r: r["id"])))
    for r in sorted([r for r in rows if r.get("kind") == "pattern" and r.get("status") == "found"],
                    key=lambda r: -(r.get("push_score") or 0)):
        T4.append("- Harvested a restricted run on %s, score %s/5 (named in section 5)" % (r["host"], r.get("push_score"))
                  if restricted(r) else "- Harvested %s on %s, score %s/5 (%s): %s" % (
                      cell(r["id"], 40), r["host"], r.get("push_score"), short(r, 50), cell("; ".join(r.get("push_signals") or []), 160)))
    conf = sorted([r for r in rows if r.get("status") == "confirm"], key=lambda r: (-(r.get("push_score") or 0), r["host"], r["id"]))
    C4 = []
    for r in conf:
        C4.append("- Confirm or drop: a restricted folder on %s, score %s/5 (named in section 5)" % (r["host"], r.get("push_score"))
                  if restricted(r) else "- Confirm or drop: %s on %s, score %s/5%s%s (%s): %s" % (
                      cell(r["id"], 40), r["host"], r.get("push_score"),
                      (", %d session%s" % (r["sessions"], "" if r["sessions"] == 1 else "s")) if r.get("sessions") else "",
                      ", " + r["hold"] if r.get("hold") else "",
                      short(r, 50), cell("; ".join(r.get("push_signals") or []), 140)))
    held_empty = [r for r in rows if r.get("kind") == "pattern" and r.get("status") == "listed" and r.get("note")]
    if held_empty:
        C4.append("- Push-like folders with nothing to copy: " + "; ".join(
            ("a restricted folder on %s" % r["host"]) if restricted(r) else "%s on %s: %s" % (cell(r["id"], 40), r["host"], r["note"])
            for r in held_empty[:6]))
    if conf:
        C4.append("- To harvest one from now on: `harvest.py confirm <name>`; to stop listing it: `harvest.py drop <name>`.")
    X4 = []
    for hname, ls in sorted((hv.get("loose_sessions") or {}).items()):
        X4.append("- %s: %d push-like session%s with no run folder (working folder %s), %s to %s, %s. Session logs are "
                  "never copied; their outputs, if any, are in the runs above." % (
                      hname, ls["sessions"], "" if ls["sessions"] == 1 else "s", ", ".join(ls.get("cwds") or []) or "?",
                      iso(parse_time(ls.get("first"))) or "?", iso(parse_time(ls.get("last"))) or "?",
                      ", ".join(ls.get("models") or []) or "model not recorded"))
    repo_only = [r for r in rows if r.get("kind") == "returns-repo"]
    if repo_only:
        X4.append("- Already in the returns repo with no plan: " + ", ".join(r["id"] for r in repo_only[:15]))
    zl = unpacked_zips(hv.get("zips") or [], rows)
    if zl:
        X4.append("- Package zips in a Mac's Downloads with no unpacked run folder (listed, not copied): " +
                  "; ".join("%s: %s" % (z["host"], posixpath.basename(z["path"])) for z in zl[:10]))
    E4 = [] if (T4 or C4 or X4) else ["None."]
    # 5. excluded for privacy (reproduced verbatim in the cloud reply): every restricted item by name, and every
    # copied item whose sensitivity is unknown
    S5 = ["## 5. Excluded for privacy", ""]
    excl = [r for r in rows if (restricted(r) and (r.get("status") in ("found", "confirm", "pointer") or r.get("kind") == "planned"))
            or (r.get("sensitive") is not False and r.get("status") == "found")]
    for r in excl:
        if r.get("dest") and r.get("host") != "pc":
            done = ("Would be copied to the PC only (dry run)" if hv.get("dry_run") else "Copied to the PC only") + \
                   "; not synced to the Project."
        elif r.get("status") == "expanded":
            done = "Topic: each matching folder is its own line here."
        elif r.get("status") in ("not_found", "not_found_yet", "not_scanned", "planned"):
            done = "Not found this run (%s); if found it stays PC-only." % r["status"].replace("_", " ")
        elif r.get("status") == "confirm":
            done = "A push to confirm; not copied. If confirmed it stays PC-only."
        elif r.get("status") == "pointer":
            done = "Runs on the PC; pointer only; not synced to the Project."
        else:
            done = "Not copied; not synced to the Project."
        why = r.get("sensitive_why") or "restricted in its plan (%s)" % cell(r.get("source_doc") or "", 50)
        S5.append("- %s (%s): %s. %s" % (cell(r["id"], 50), r.get("host") or r.get("host_hint") or "host?", why, done))
    names = sorted({n for r in rows for n in (r.get("excluded_names") or [])})
    if names:
        S5.append("- Files never copied under the credential rule: %s%s" % (", ".join(names[:12]),
                                                                           " and %d more" % (len(names) - 12) if len(names) > 12 else ""))
    if not excl and not names:
        S5.append("Nothing excluded.")
    S5 += ["- Not scanned: Jjess's Mac mini (not a Claude host, by decision); on the PC, anything outside C:\\Projects "
           "(personal folders, temp, system), hidden C:\\Projects folders and the C:\\Projects\\_control backup mirror; on the "
           "Macs, home dot folders, temp folders and ~/gt (Gas Town, agent2's always-on agent office: infrastructure, not runs).", ""]
    # 6. possible other hosts
    S6 = ["## 6. Possible other hosts", ""]
    oh = hv.get("other_hosts") or {}
    ts = hv.get("tailscale") or []
    by_id = {peer_id(p): p for p in ts}
    listed = 0
    for peer, ev in oh.items():
        p = by_id.get(peer, {"name": peer, "dns": peer})
        if p.get("fleet"):
            continue
        listed += 1
        jj = p.get("never_scan") or never_scan_peer(p)
        S6.append("- %s: %s Evidence: %s" % (
            p.get("name") or peer, "never scanned, by decision." if jj else
            "not scanned. To include it, add a `harvest_only` row for it to tools\\hosts.conf.",
            "; ".join("%s:%s %s" % (Path(e["file"]).name, e["line"],
                                    "[line withheld: it may hold a credential]" if CREDENTIAL_LINE.search(e["text"]) else cell(e["text"], 90))
                      for e in ev[:2])))
    if not listed:
        S6.append("None.")
    if ts:
        S6.append("- Tailscale peers at scan time: " + ", ".join("%s %s%s" % (
            p["name"] or p["dns"], "online" if p["online"] else "offline",
            " (scanned as %s)" % p["fleet"] if p.get("fleet") else (" (never scanned)" if p.get("never_scan") else ""))
            for p in ts if p.get("name") or p.get("dns")))
    S6.append("")
    # 7. pushed
    S7 = ["## 7. Pushed to orchestrator-returns", ""]
    if push:
        S7.append("- Commit %s, pushed %s. Full returns: %s. Pointer-only (restricted): %s." % (
            push.get("commit") or "none", "yes" if push.get("pushed") else "no",
            ", ".join(push.get("added") or []) or "none", ", ".join(push.get("pointer") or []) or "none"))
        S7.append("- fleet.py pull: %s" % cell(push.get("fleet_pull"), 160))
        if push.get("skipped"):
            S7.append("- Skipped: " + "; ".join(push["skipped"][:8]))
    else:
        S7.append("Not run.")
    S7 += ["", "Details: harvest-manifest.json beside this file (and harvest-ledger.json for what each folder held "
           "when copied)."]
    # fit in 150 lines: sections 1, 3, 5, 6 and 7 always whole; the run table and the confirm list give way
    fixed = len(L) + len(S1) + len(S2) + len(P2) + len(S3) + len(S4) + len(X4) + len(E4) + 1 + len(S5) + len(S6) + len(S7)
    room = max(0, REPORT_LINES - fixed)
    def fit(lines, n, what):
        if len(lines) <= n:
            return lines
        n = max(n, 1)
        return lines[:n - 1] + ["- ... %d more %s in harvest-manifest.json" % (len(lines) - n + 1, what)]
    t2 = fit(T2, max(min(len(T2), room - min(len(T4) + len(C4), room // 3)), 1), "runs")
    room -= len(t2)
    t4 = fit(T4, max(min(len(T4), room // 2), 1) if T4 else 0, "harvested runs") if T4 else []
    room -= len(t4)
    c4 = fit(C4, max(room, 1), "pushes to confirm") if C4 else []
    L += S1 + S2 + t2 + P2 + S3 + S4 + t4 + c4 + X4 + E4 + [""] + S5 + S6 + S7
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    man["harvest"]["report"] = str(path)
    ctx.save(man)
    write_readme(ctx)
    print("report: %s (%d lines)" % (path, len(L)))
    return path


def write_readme(ctx):
    f = ctx.out / "README.md"
    if f.exists():
        return
    f.write_text("\n".join([
        "# blitz-returns", "",
        "Run folders harvested by `tools\\harvest\\harvest.py` (skill: blitz-harvest).", "",
        "- `HARVEST-<date>.md`: one report per harvest (same-day re-runs get -2, -3).",
        "- `harvest-manifest.json`: every run the last harvest planned, found, copied or skipped.",
        "- `harvest-ledger.json`: what each copied folder held when copied (decides unchanged vs changed).",
        "- `<host>\\<sprint-id>\\`: a run folder as copied, minus caches, git objects and credential files.",
        "- `<name>-v2`, `-v3`: a later harvest of a run that changed. Earlier copies are never overwritten.",
        "- `pc\\<name>\\RUNS-HERE.md`: a run that lives on this PC, pointed to rather than duplicated.",
        "- `seeds\\`: runs named by the Project's planning docs, written by the cloud procedure.",
        "- `.scan\\`: raw finder output per host (PC only, never synced).",
        "", "Restricted runs stay here and in the private returns repo (as pointers); they never go to the Project.", ""]),
        encoding="utf-8")


# ---------------------------------------------------------------- sync: the Project's allowed subset

REDACT_KEYS = ("return", "progress_head", "states", "doc_headings", "title", "excluded_names", "push_signals", "path",
               "path_hint", "session_file")


def cmd_choose(ctx, verdict, names):
    """Record Elliot's answer on pattern-recognized pushes: confirm (harvest next run) or drop (stop listing)."""
    f = ctx.out / "choices.json"
    ch = load_json(f, {"confirm": [], "drop": []})
    other = "drop" if verdict == "confirm" else "confirm"
    for n in names:
        if n in ch[other]:
            ch[other].remove(n)
        if n not in ch[verdict]:
            ch[verdict].append(n)
    save_json(f, ch)
    print("%s: %s (takes effect at the next scan)" % (verdict, ", ".join(names)))


def cmd_sync(ctx):
    man = ctx.load()
    rows, hv = man["rows"], man["harvest"]
    date = hv.get("date") or ctx.started.strftime("%Y-%m-%d")
    zpath = ctx.out / ("sync-%s.zip" % date)
    n = 1
    while zpath.exists():
        n += 1
        zpath = ctx.out / ("sync-%s-%d.zip" % (date, n))
    red = json.loads(json.dumps(man))
    for r in red["rows"]:
        if r.get("sensitive") is not False:
            for k in REDACT_KEYS:
                if k in r and k not in ("path",):
                    r[k] = "[withheld: restricted]"
            r["id"] = r["id"]
    budget, used, listed = PROJECT_CAP, 0, []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        rep = hv.get("report")
        if rep and Path(rep).exists():
            z.write(rep, Path(rep).name)
        z.writestr("harvest-manifest.json", json.dumps(red, indent=1))
        seen = set()
        for r in rows:
            d = r.get("dest")
            if not d or d in seen or r.get("sensitive") is not False or r.get("host") == "pc":
                continue
            seen.add(d)
            src = ctx.out / d
            if not src.exists():
                continue
            files = [f for f in src.rglob("*") if f.is_file() and not is_secret(str(f.relative_to(src)))]
            size = sum(f.stat().st_size for f in files)
            if used + size > budget:
                files = [f for f in files if f.name in CONTROL_NAMES]
                listed.append("%s (%s): control files only, over the Project cap" % (d, human(size)))
            for f in files:
                z.write(f, "%s/%s" % (d, str(f.relative_to(src)).replace("\\", "/")))
                used += f.stat().st_size
        z.writestr("SYNC-NOTES.md", "\n".join(["# Sync %s" % date, "", "Restricted and unclassified runs are not in this zip.",
                                               "Over the 200 MB cap (control files only):"] + (listed or ["none"])) + "\n")
    hv["sync"] = {"zip": str(zpath), "bytes": zpath.stat().st_size, "over_cap": listed}
    ctx.save(man)
    print("sync: %s (%s, %d folders over the cap)" % (zpath, human(zpath.stat().st_size), len(listed)))
    return zpath


def cmd_find(ctx, named_paths):
    """Run remote-find.sh on each selected Mac and print what it saw (no manifest change). Stage-1 test and debugging aid."""
    for h in ctx.hosts():
        if h["name"] == "pc":
            continue
        ok, info = reach(ctx, h)
        if not ok:
            print("%s (%s): unreachable: %s" % (h["name"], h["ssh"], info))
            continue
        t0 = time.time()
        recs, meta = remote_scan(ctx, h, named_paths)
        home = next((r.get("home") for r in recs if r.get("kind") == "host"), "") or "~"
        kinds = {}
        for r in recs:
            kinds[r.get("kind")] = kinds.get(r.get("kind"), 0) + 1
        print("%s (%s = %s): %.0fs, %s, records %s" % (h["name"], h["ssh"], info, time.time() - t0,
                                                     "PARTIAL" if meta["partial"] else "complete", kinds))
        if meta["stderr"]:
            print("  stderr: " + cell(meta["stderr"], 200))
        for r in recs:
            k = r.get("kind")
            if k == "folder":
                rn = r.get("runner") or {}
                flags = [n for n, f in (("RETURN", "has_return"), ("state", "has_state"), ("PROGRESS", "has_progress"),
                                        ("MANIFEST", "has_manifest")) if r.get(f)]
                print("  folder %s [%s] %s, %s files, %s%s%s%s" % (
                    r["path"].replace(home, "~", 1), r.get("why"), human(r.get("bytes")), r.get("nfiles"),
                    "recent " if r.get("recent") else "old ", "+".join(flags) or "no markers",
                    (" runner=%s/%s" % (rn.get("status") or "-", rn.get("account") or "-")) if rn else "",
                    " CONFIDENTIAL-MARKS" if r.get("confidential_marks") else ""))
            elif k == "session":
                print("  session cwd=%s models=%s %s..%s subagents=%s" % (
                    (r.get("cwd") or "").replace(home, "~", 1), ",".join(r.get("models") or []) or "-",
                    r.get("first_ts") or "?", r.get("last_ts") or "?", r.get("subagent_logs")))
            elif k in ("zip", "warn"):
                print("  %s %s" % (k, cell(r.get("path") or r.get("msg"), 160)))
        names = [r.get("name") for r in recs if r.get("kind") == "prompt"]
        if names:
            print("  prompts (%d): %s" % (len(names), cell(", ".join(names), 400)))
        print("  raw: %s" % (ctx.scan_dir / ("%s-%s.jsonl" % (h["name"], ctx.started.strftime("%Y%m%dT%H%M")))))


# ---------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "scan", "copy", "push", "report", "all", "check", "seed", "sync", "confirm",
                                    "drop", "find"])
    ap.add_argument("docs", nargs="*", help="seed: planning docs or folders; confirm/drop: folder names or paths; "
                                           "find: extra remote paths to describe")
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--host", action="append")
    ap.add_argument("--slow", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--push-restricted", choices=["pointer", "full"], default="pointer")
    ap.add_argument("--out", help="seed: output file")
    ap.add_argument("--extra", help="seed: extra rows (JSON list), e.g. runs named only in Project threads")
    a = ap.parse_args(argv)
    ctx = Ctx(a.root, a.days, a.slow, a.host, a.dry_run, a.push_restricted)
    if a.days < 7:
        print("note: --days is at least 7 (section 5.5); using 7")
    if a.cmd == "seed":
        return cmd_seed(ctx, a.docs, a.out or "seed-%s.json" % ctx.started.strftime("%Y-%m-%d"), a.extra)
    ctx.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "plan":
        cmd_plan(ctx)
    elif a.cmd == "scan":
        cmd_scan(ctx)
    elif a.cmd == "copy":
        if not ctx.dry_run:
            cmd_check(ctx)
        cmd_copy(ctx)
    elif a.cmd == "push":
        cmd_push(ctx)
    elif a.cmd == "report":
        cmd_report(ctx)
    elif a.cmd == "check":
        cmd_check(ctx)
    elif a.cmd == "sync":
        cmd_sync(ctx)
    elif a.cmd in ("confirm", "drop"):
        cmd_choose(ctx, a.cmd, a.docs)
    elif a.cmd == "find":
        cmd_find(ctx, a.docs)
    elif a.cmd == "all":
        cmd_check(ctx)
        cmd_plan(ctx)
        cmd_scan(ctx)
        cmd_copy(ctx)
        cmd_push(ctx)
        cmd_report(ctx)


if __name__ == "__main__":
    main()
