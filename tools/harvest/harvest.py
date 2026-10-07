#!/usr/bin/env python3
"""blitz-harvest: find every blitz and major push the fleet ran, and bring it home.

Reads the weekly-blitz plans and run prompts to learn what was launched, finds
each run on the PC, agent2, the MacBook and agent1 (read-only over SSH), also
recognizes unplanned pushes by pattern, copies the run folders to
C:\\Projects\\master-orchestrator\\blitz-returns\\, pushes new RETURN.md files to
the orchestrator-returns clone, and writes HARVEST-<date>.md.

    python harvest.py all [--days 8] [--dry-run]
    python harvest.py plan | scan | copy | push | report | sync-zip | selftest

Plain Python and shell. No model calls. Nothing is ever written on a Mac.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
MO_ROOT = HERE.parents[1]                      # C:\Projects\master-orchestrator
TOOLS = MO_ROOT / "tools"
REMOTE_FIND = HERE / "remote-find.sh"


def finder_bytes() -> bytes:
    return REMOTE_FIND.read_bytes().replace(b"\r\n", b"\n")
SCHEMA = 1

# --------------------------------------------------------------------------
# Fixed rules

# Never copied, from any host, in any mode. Matched case-insensitively against
# every file name. Asserted by test_harvest.py.
NEVER_COPY = (
    "*token*", "*secret*", "*.pem", "id_*", ".env", ".env.*",
    "*.keychain", "*.keychain-db", "credentials*.json", ".credentials.json",
    "auth.json", ".netrc", ".npmrc", ".pypirc", "*.p12", "*.pfx", "*.key",
)
# Folders never copied (caches, dependencies, credentials stores).
SKIP_DIRS = (
    "node_modules", ".venv", "venv", "__pycache__", ".pnpm-store", ".turbo",
    ".cache", ".npm", ".yarn", ".ssh", ".gnupg", ".Trash", ".next",
)
# Inside a .git folder only these survive (provenance, not history).
GIT_KEEP = ("HEAD", "config")
JUNK = (".DS_Store", "._*")
BIG_LOG_BYTES = 50 * 1024 * 1024
FOLDER_CAP_BYTES = 2 * 1024 ** 3
PROJECT_SYNC_CAP_BYTES = 200 * 1024 ** 2
CONTROL_FILES = (
    "RETURN.md", "PROGRESS.md", "state.json", "REPORT.md", "README.md",
    "MANIFEST.md", "SPRINT.md", "GOAL.txt", "BRIEF.md", "RUBRIC.md",
    "ORIGINAL-PROMPT.md", "INDEX.md", "RUN-NOTES.md",
)

# Sensitivity screen (plan 5.4). Word-bounded, case-insensitive. Extend with
# tools\harvest\sensitive-keywords.txt (one per line) without editing code.
SENSITIVE_WORDS = (
    "lawsuit", "litigation", "apeira", "toptal", "pave", "us pave", "cureis",
    "client", "nda", "deposition", "family", "personal debt", "whatsapp",
    "messages export", "photos", "colombia", "project playa", "playa",
)
RESTRICTED_MARK = re.compile(
    r"(?i)(--sensitive\b|\bsensitive\s*[:=]\s*(yes|true)\b|\brestricted\s*(material|:)|\bclaude[- ]only\b)")

# Pattern pass (plan 3.7).
LATEST_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "fable 5.1", "opus 5.5")
INTENSITY_MARKERS = (
    "xhigh", "max effort", "high effort", "effort:high", "effort:max", "effort:xhigh",
    "ultracode", "workflow tool", "/goal", "/loop", "max_hours", "watchdog", "run-sprint",
)
# Weekly resets, UTC: (weekday Mon=0, hour). Keys match account labels below.
RESETS = {"cybernova": (3, 12), "gmail": (0, 9), "primary": (6, 14)}
ACCOUNT_KEYS = {
    "elliot@cybernovaequity.com": "cybernova", "cybernova": "cybernova",
    "luchansky.elliot.a@gmail.com": "gmail", "gmail": "gmail",
    "el@elliotl.im": "primary", "primary": "primary",
}

# Hosts and their run roots (plan section 4). hosts.conf wins for targets and
# accounts when it names the host.
DEFAULT_HOSTS = (
    {"name": "agent2", "target": "agent2@agents-mac-mini-1", "user": "agent2",
     "aliases": ("agents-mac-mini-1", "100.67.171.116", "agent-2")},
    {"name": "macbook", "target": "luchanskyelliot@100.116.248.10", "user": "luchanskyelliot",
     "aliases": ("luchanskys-macbook-air", "100.116.248.10", "macbook air")},
    {"name": "agent1", "target": "agent1@100.82.254.11", "user": "agent1",
     "aliases": ("100.82.254.11", "agent-1")},
)
NEVER_SCAN = {"jess": ("100.99.90.115", "jjess", "jess@")}
PC_NAMES = ("desktop-gj0ek81",)

UTC = dt.timezone.utc


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso(ts: float | None) -> str:
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


def parse_ts(s: str) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Configuration

class Cfg:
    def __init__(self, args: argparse.Namespace):
        self.root = Path(args.root) if args.root else MO_ROOT
        self.dest = Path(args.dest) if args.dest else self.root / "blitz-returns"
        self.returns = Path(args.returns) if args.returns else self.root / "returns"
        self.state = self.root / "state"
        self.tools = self.root / "tools"
        self.days = args.days
        self.since = (now() - dt.timedelta(days=args.days)).timestamp()
        self.dry_run = getattr(args, "dry_run", False)
        self.only_host = getattr(args, "host", None)
        self.slow = getattr(args, "slow", False)
        self.docs = [Path(d) for d in (getattr(args, "docs", None) or [])]
        self.extra_rows = [Path(p) for p in (getattr(args, "extra_rows", None) or [])]
        self.projects = Path(args.projects) if getattr(args, "projects", None) else self.root.parent
        self.date = args.date or now().strftime("%Y-%m-%d")
        self.manifest_path = self.dest / "harvest-manifest.json"
        self.scan_dir = self.dest / "_scan" / self.date
        self.fleet_pull = not getattr(args, "no_fleet_pull", False)
        self.from_project = bool(getattr(args, "from_project", False))

    def sensitive_words(self) -> tuple[str, ...]:
        extra = HERE / "sensitive-keywords.txt"
        words = list(SENSITIVE_WORDS)
        if extra.exists():
            words += [w.strip().lower() for w in extra.read_text(encoding="utf-8").splitlines()
                      if w.strip() and not w.startswith("#")]
        return tuple(dict.fromkeys(words))


# --------------------------------------------------------------------------
# Hosts: hosts.conf (shared with fleet.py and the sprint-cap-balancer)

def _fleet():
    """fleet.py, imported for its SSH options and host parsing when present."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    try:
        import fleet  # type: ignore
        return fleet
    except Exception:
        return None


def read_hosts_conf(path: Path) -> dict[str, dict]:
    """Parse hosts.conf without assuming its column order: a line's first word
    is the host name; user@host is the SSH target; key=value pairs and the
    words yes/no/harvest_only are read by name."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    header: list[str] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            cols = line.lstrip("#").split()
            if len(cols) >= 3 and cols[0].lower() in ("name", "host"):
                header = [c.lower() for c in cols]
            continue
        parts = line.split()
        name = parts[0]
        rec: dict = {"name": name, "raw": line}
        if header and len(header) == len(parts):
            rec.update(dict(zip(header, parts)))
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                rec[k.lower()] = v
            elif "@" in p and "target" not in rec and not re.search(r"\.(com|im|org|net)$", p):
                rec["target"] = p
            elif "@" in p:
                rec.setdefault("account", p)
        rec["harvest_only"] = "harvest_only" in line.replace("-", "_") and (
            "harvest_only=yes" in line.replace("-", "_") or "harvest_only: yes" in line.replace("-", "_")
            or re.search(r"\bharvest_only\b(?!\s*=\s*no)", line.replace("-", "_")) is not None)
        out[name] = rec
    return out


class Host:
    def __init__(self, name: str, target: str | None, account: str = "", user: str = "",
                 aliases: tuple = (), source: str = "default"):
        self.name, self.target, self.account = name, target, account
        self.user, self.aliases, self.source = user, aliases, source
        self.reachable: bool | None = None
        self.error = ""
        self.info: dict = {}
        self.partial = False

    @property
    def account_key(self) -> str:
        a = (self.account or self.info.get("account_hint") or "").lower()
        for k, v in ACCOUNT_KEYS.items():
            if k in a:
                return v
        return ""

    def to_json(self) -> dict:
        return {"target": self.target, "account": self.account, "source": self.source,
                "reachable": self.reachable, "error": self.error, "partial": self.partial,
                "account_hint": self.info.get("account_hint", ""),
                "hostname": self.info.get("hostname", "")}


def load_hosts(cfg: Cfg) -> list[Host]:
    conf = read_hosts_conf(cfg.tools / "hosts.conf")
    hosts = []
    for d in DEFAULT_HOSTS:
        rec = None
        for name, r in conf.items():
            keys = {name.lower(), str(r.get("target", "")).lower()}
            if d["name"] in keys or d["target"].lower() in keys or any(
                    a.lower() in " ".join(keys) for a in d["aliases"]):
                rec = r
                break
        target = (rec or {}).get("target") or d["target"]
        account = (rec or {}).get("account", "")
        hosts.append(Host(d["name"], target, account, d["user"], d["aliases"],
                          "hosts.conf" if rec else "default"))
    if cfg.only_host:
        hosts = [h for h in hosts if h.name == cfg.only_host]
    return hosts


def ssh_base(target: str, connect_timeout: int = 8) -> list[str]:
    exe = shutil.which("ssh") or "ssh"
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    fleet = _fleet()
    extra = getattr(fleet, "HARVEST_SSH_OPTS", None) if fleet else None
    if isinstance(extra, (list, tuple)):
        opts += list(extra)
    return [exe, *opts, target]


def run_stream(argv: list[str], timeout: int, stdin_bytes: bytes | None = None) -> tuple[list[str], int | None, str, bool]:
    """Run a command, collecting stdout lines as they arrive. On timeout the
    process is killed and the lines read so far are returned (partial)."""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return [], None, str(e), False
    lines: list[str] = []
    err: list[bytes] = []

    def rd():
        for b in p.stdout:  # type: ignore[union-attr]
            lines.append(b.decode("utf-8", "replace").rstrip("\r\n"))

    def re_():
        err.append(p.stderr.read())  # type: ignore[union-attr]

    t1, t2 = threading.Thread(target=rd, daemon=True), threading.Thread(target=re_, daemon=True)
    t1.start(); t2.start()
    if stdin_bytes is not None:
        try:
            p.stdin.write(stdin_bytes)  # type: ignore[union-attr]
            p.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
    timed_out = False
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        p.wait()
    t1.join(5); t2.join(5)
    return lines, p.returncode, b"".join(err).decode("utf-8", "replace").strip(), timed_out


# --------------------------------------------------------------------------
# Source 1 and 2: blitz plans and run prompts

MAC_USERS = {d["user"]: d["name"] for d in DEFAULT_HOSTS}
RX_ABS = re.compile(r"/Users/(?P<user>[\w.-]+)/(?P<area>orchestrator/sprints|orchestrator/returns|Downloads)/(?P<id>[A-Za-z0-9][\w.-]*)")
RX_TILDE = re.compile(r"(?<![\w/])~/(?P<area>orchestrator/sprints|orchestrator/returns|Downloads)/(?P<id>[A-Za-z0-9][\w.-]*)")
RX_SPRINT = re.compile(r"(?i)\b(?:resume\s+sprint|sprint\s+id|sprint)(?:\s*:\s*|\s+)`?(?P<id>[A-Za-z0-9][\w.-]*-20\d\d-\d\d-\d\d(?:-v\d+)?)\b")
RX_RUNSPRINT = re.compile(r"run-sprint\.sh\s+(?:--\S+\s+)*(?P<id>[A-Za-z0-9][\w.-]+)\s+(?P<prompt>\S+)")
RX_FLEET = re.compile(r"fleet\.py\s+(?:dispatch|queue|send\s+(?P<host>\S+))\s+(?P<id>[A-Za-z0-9][\w.-]+)\s+(?P<prompt>\S+)")
RX_WINZIP = re.compile(r"(?i)Downloads\\(?P<id>[\w.-]+?)\.zip")
RX_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RX_MODEL = re.compile(r"(?i)\b(fable 5\.1|opus 5\.5|sonnet 5\.5|claude-(?:fable|opus|sonnet)-\d-\d)\b")
RX_EFFORT = re.compile(r"(?i)\b(xhigh|max effort|high effort|at max|at high|effort high|effort max)\b")
RX_DATE = re.compile(r"20\d\d-\d\d-\d\d")
FILE_EXT = re.compile(r"\.(md|txt|csv|json|html|pdf|png|jpg|py|sh|log)$", re.I)


def doc_files(cfg: Cfg) -> list[Path]:
    bases = [cfg.root] + cfg.docs
    pats = ("blitz/*.md", "*/RUN-*-PROMPT.txt", "intake/run-*.md", "prompts/*.md", "prompts/*.txt")
    out: list[Path] = []
    for b in bases:
        for pat in pats:
            out += sorted(b.glob(pat))
    seen, res = set(), []
    for p in out:
        key = p.name.lower()
        if key in seen or "loop-package-prompt" in key:
            continue
        seen.add(key)
        res.append(p)
    return res


def doc_in_window(p: Path, since: float) -> bool:
    try:
        if p.stat().st_mtime >= since:
            return True
    except OSError:
        return False
    m = RX_DATE.search(p.name) or RX_DATE.search(p.parent.name)
    if m:
        d = dt.datetime.strptime(m.group(0), "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        return d + 86400 >= since
    return False


def _sections(text: str) -> list[tuple[str, str]]:
    out, title, buf = [], "", []
    for line in text.splitlines():
        if re.match(r"^#{1,4} ", line):
            if buf:
                out.append((title, "\n".join(buf)))
            title, buf = line.lstrip("#").strip(), [line]
        else:
            buf.append(line)
    if buf:
        out.append((title, "\n".join(buf)))
    return out


def _clean_title(t: str) -> str:
    t = re.sub(r"^\d+\.\s*", "", t)
    t = re.sub(r"(?i),?\s*(type `[^`]*` first, then )?paste.*$", "", t)
    return t.strip(" :-")[:80]


def _host_from_doc(p: Path, text: str) -> str:
    n = p.name.lower() + " " + p.parent.name.lower()
    if "agent2" in n:
        return "agent2"
    if "macbook" in n:
        return "macbook"
    if "agent1" in n:
        return "agent1"
    m = re.search(r"(?i)\bTARGET\b[^\n]*", text)
    if m:
        return _host_from_text(m.group(0))
    return ""


def _host_from_text(s: str) -> str:
    s = s.lower()
    if "agent2" in s or "agent-2" in s or "agents-mac-mini" in s:
        return "agent2"
    if "macbook" in s or "luchanskyelliot" in s:
        return "macbook"
    if "agent1" in s or "agent-1" in s:
        return "agent1"
    if "desktop-gj0ek81" in s or "workstation" in s or "c:\\projects" in s:
        return "pc"
    return ""


def _norm_id(raw: str) -> str | None:
    i = raw.strip().rstrip(".,;:)`'\"")
    if i.lower().endswith(".zip"):
        i = i[:-4]
    if FILE_EXT.search(i):
        return None
    if not (RX_DATE.search(i) or re.search(r"(?i)\bL\d+\b|smoke|-L\d", i)):
        return None
    if i.startswith("<") or "{{" in i:
        return None
    return i


def parse_doc(p: Path) -> list[dict]:
    text = p.read_text(encoding="utf-8", errors="replace")
    kind = "weekly-blitz" if p.name.startswith("weekly-blitz") else "prompt"
    doc_host = _host_from_doc(p, text)
    rows: dict[str, dict] = {}
    for title, body in _sections(text):
        sec_host = _host_from_text(" ".join(re.findall(r"(?i)\bTARGET\b[^\n]*", body))) or doc_host
        acct = ""
        for e in RX_EMAIL.findall(body):
            if e.lower() in ACCOUNT_KEYS:
                acct = e.lower()
                break
        models = sorted({m.lower() for m in RX_MODEL.findall(body)})
        efforts = sorted({m.lower() for m in RX_EFFORT.findall(body)})
        launch = "B" if re.search(r"(?i)path b|pasted by elliot|paste its block|, paste", body + title) else (
            "A" if re.search(r"run-sprint\.sh|fleet\.py (dispatch|send)", body) else "")
        restricted = bool(RESTRICTED_MARK.search(body))
        target_line = bool(re.search(r"(?i)\bTARGET\b", body))
        sec_strength = 2 if target_line else (1 if sec_host else 0)
        found: list[tuple[str, str, str, str, int]] = []   # id, host, path_hint, prompt, host strength
        for m in RX_ABS.finditer(body):
            i = _norm_id(m.group("id"))
            if i:
                found.append((i, MAC_USERS.get(m.group("user"), m.group("user")), m.group(0), "", 3))
        for m in RX_TILDE.finditer(body):
            i = _norm_id(m.group("id"))
            if i:
                found.append((i, sec_host, m.group(0), "", min(sec_strength, 1)))
        for m in RX_SPRINT.finditer(body):
            i = _norm_id(m.group("id"))
            if i:
                found.append((i, sec_host, "", "", min(sec_strength, 1)))
        for m in RX_RUNSPRINT.finditer(body):
            i = _norm_id(m.group("id")) or m.group("id")
            found.append((i, sec_host or "agent2", "", Path(m.group("prompt")).name, 2))
        for m in RX_FLEET.finditer(body):
            i = _norm_id(m.group("id")) or m.group("id")
            if i.startswith("<"):
                continue
            found.append((i, m.group("host") or sec_host, "", Path(m.group("prompt")).name, 2 if m.group("host") else 1))
        for m in RX_WINZIP.finditer(body):
            i = _norm_id(m.group("id"))
            if i:
                found.append((i, sec_host or "macbook", "", "", 1))
        for i, host, path_hint, prompt, strength in found:
            if "/" in i or i in ("sprints", "returns"):
                continue
            r = rows.get(i)
            if not r:
                r = rows[i] = {
                    "id": i, "title": _clean_title(title) or i, "source_doc": f"{kind}:{p.name}",
                    "sources": [], "host_hint": host if strength else "", "host_strength": strength if host else 0,
                    "path_hint": path_hint, "account": acct, "titles": [],
                    "launch_path": launch, "model_hint": models, "effort_hint": efforts,
                    "prompt_file": prompt, "doc_file": p.name,
                    "restricted_mark": restricted, "status": "planned",
                }
            if f"{kind}:{p.name}" not in r["sources"]:
                r["sources"].append(f"{kind}:{p.name}")
            if host and strength > r.get("host_strength", 0):
                r["host_hint"], r["host_strength"] = host, strength
                r["account"] = acct
            t = _clean_title(title)
            if t and t not in r["titles"]:
                r["titles"].append(t)
            r["path_hint"] = r["path_hint"] or path_hint
            r["account"] = r["account"] or acct
            r["restricted_mark"] = r["restricted_mark"] or restricted
    for r in rows.values():
        r["title"] = _best_title(r["id"], r.pop("titles")) or r["title"]
    return list(rows.values())


def _best_title(rid: str, titles: list[str]) -> str:
    """Prefer a section title that names the run (e.g. "L7" for L7-...)."""
    tokens = [t for t in re.split(r"[-_]", rid.lower()) if len(t) >= 2 and not t.isdigit()]
    for t in titles:
        low = t.lower()
        if any(re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", low) for tok in tokens):
            return t
    return titles[0] if titles else ""


def merge_rows(rows: list[dict], new: list[dict]) -> list[dict]:
    """Merge by run id (ids carry a package name and a date, so they are unique
    across hosts); the strongest host evidence wins."""
    by_id = {r["id"]: r for r in rows}
    for n in new:
        target = by_id.get(n["id"])
        if target is None:
            rows.append(n)
            by_id[n["id"]] = n
            continue
        for src in n.get("sources", [n.get("source_doc")]):
            if src and src not in target.setdefault("sources", []):
                target["sources"].append(src)
        if n.get("host_hint") and n.get("host_strength", 1) > target.get("host_strength", 0 if not target.get("host_hint") else 1):
            target["host_hint"], target["host_strength"] = n["host_hint"], n.get("host_strength", 1)
            target["account"] = n.get("account", "")
        for k in ("path_hint", "account", "launch_path", "prompt_file"):
            if not target.get(k) and n.get(k):
                target[k] = n[k]
        for k in ("model_hint", "effort_hint"):
            target[k] = sorted(set(target.get(k) or []) | set(n.get(k) or []))
        target["restricted_mark"] = bool(target.get("restricted_mark") or n.get("restricted_mark"))
        if n.get("fleet_sensitive"):
            target["fleet_sensitive"] = True
        if target.get("title") == target["id"] and n.get("title"):
            target["title"] = n["title"]
    return rows


# --------------------------------------------------------------------------
# Source 3: fleet.py state; source 4: the returns repo

def read_fleet_state(cfg: Cfg) -> list[dict]:
    rows = []
    for name in ("dispatch-log.jsonl", "queue.jsonl"):
        p = cfg.state / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(o, dict):
                continue
            i = o.get("id") or o.get("sprint") or o.get("sprint_id")
            if not i:
                continue
            ts = parse_ts(str(o.get("ts") or o.get("time") or o.get("at") or "")) or 0
            host = str(o.get("host") or o.get("to") or "")
            rows.append({
                "id": str(i), "title": str(i), "source_doc": f"fleet:{name}", "sources": [f"fleet:{name}"],
                "host_hint": _host_from_text(host) or host, "host_strength": 2 if host else 0,
                "path_hint": "", "account": str(o.get("account") or ""),
                "launch_path": "A", "model_hint": [str(o["model"])] if o.get("model") else [],
                "effort_hint": [str(o["effort"])] if o.get("effort") else [],
                "prompt_file": Path(str(o.get("prompt") or o.get("prompt_file") or "")).name,
                "restricted_mark": False, "fleet_sensitive": bool(o.get("sensitive")),
                "status": "planned", "_ts": ts,
            })
    # A planned run is never dropped for age, but fleet history older than the
    # window is not a plan: keep only dispatches inside the window.
    return [r for r in rows if not r["_ts"] or r["_ts"] >= cfg.since]


def returns_index(cfg: Cfg) -> dict[str, dict]:
    """Every RETURN.md already in the orchestrator-returns clone, by run id."""
    idx: dict[str, dict] = {}
    if not cfg.returns.exists():
        return idx
    for p in cfg.returns.rglob("RETURN.md"):
        if ".git" in p.parts:
            continue
        rid = p.parent.name
        date_m = RX_DATE.search(str(p.relative_to(cfg.returns)))
        ts = p.stat().st_mtime
        if date_m:
            ts = dt.datetime.strptime(date_m.group(0), "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        head = p.read_text(encoding="utf-8", errors="replace")[:3000]
        idx[rid] = {"path": str(p), "sha256": sha256_file(p), "ts": ts,
                    "host_hint": _host_from_text(head)}
    return idx


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Source 6: light-touch evidence sweep on the PC, and Tailscale peers

RX_IP = re.compile(r"\b100\.(?:\d{1,3}\.){2}\d{1,3}\b")
SWEEP_WORDS = re.compile(r"(?i)\b(sprint|loop|blitz|run-sprint|orchestrator-prompts|scp|rsync)\b")
RX_SECRETISH = re.compile(r"(?i)((token|key|secret|password|passwd|pwd|bearer)\S*\s*[=:]\s*\S+|sk-[\w-]{10,}|[A-Za-z0-9+_=-]{40,})")


def tailscale_peers() -> list[dict]:
    exe = shutil.which("tailscale")
    win = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    if not exe and os.name == "nt" and win.exists():
        exe = str(win)
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=20, text=True)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return []
    peers = []
    for p in (data.get("Peer") or {}).values():
        peers.append({"name": p.get("HostName", ""), "dns": (p.get("DNSName") or "").rstrip("."),
                      "ip": (p.get("TailscaleIPs") or [""])[0], "online": bool(p.get("Online")),
                      "os": p.get("OS", "")})
    return peers


def known_host_for(token: str) -> str:
    t = token.lower()
    for d in DEFAULT_HOSTS:
        if d["name"] in t or any(a.lower() in t for a in d["aliases"]) or d["target"].split("@")[-1].lower() in t:
            return d["name"]
    for name, al in NEVER_SCAN.items():
        if any(a in t for a in al):
            return name
    if any(n in t for n in PC_NAMES):
        return "pc"
    return ""


def evidence_sweep(cfg: Cfg, peers: list[dict]) -> list[dict]:
    """One grep pass over orchestrator files, shell histories and known_hosts
    for Tailscale peers named near sprint words. Never reads other files."""
    files: list[Path] = []
    for sub in ("prompts", "blitz", "intake", "state"):
        d = cfg.root / sub
        if d.exists():
            files += [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".txt", ".jsonl", ".json", ".log", ".sh")]
    files.append(cfg.tools / "hosts.conf")
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    files += [appdata / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt",
              home / ".bash_history", home / ".zsh_history"]
    names = {}
    for p in peers:
        for k in (p["name"], p["dns"].split(".")[0] if p["dns"] else "", p["ip"]):
            if k:
                names[k.lower()] = p
    hits: dict[str, dict] = {}
    for f in files:
        if not f.exists():
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            if not SWEEP_WORDS.search(line):
                continue
            low = line.lower()
            cands = set(RX_IP.findall(line)) | {k for k in names if len(k) > 3 and k in low}
            for c in cands:
                if known_host_for(c) in ("agent1", "agent2", "macbook", "pc"):
                    continue
                peer = names.get(c.lower(), {"name": "", "ip": c})
                key = peer.get("ip") or c
                if key in hits:
                    hits[key]["count"] += 1
                    continue
                shown = RX_SECRETISH.sub("[redacted]", line.strip())[:140]
                hits[key] = {"peer": peer.get("name") or c, "ip": peer.get("ip", c),
                             "file": str(f), "line": n, "evidence": shown, "count": 1,
                             "never_scan": known_host_for(c) in NEVER_SCAN}
    kh = home / ".ssh" / "known_hosts"
    if kh.exists():
        for n, line in enumerate(kh.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            first = line.split(" ", 1)[0]
            if first.startswith("|1|"):
                continue  # hashed entries cannot be read
            for h in first.split(","):
                h = h.strip("[]").split("]:")[0]
                if not h or known_host_for(h) in ("agent1", "agent2", "macbook", "pc"):
                    continue
                if h in hits or not (RX_IP.match(h) or h.lower() in names):
                    continue
                peer = names.get(h.lower(), {"name": h, "ip": h})
                hits[h] = {"peer": peer.get("name") or h, "ip": peer.get("ip", h), "file": str(kh),
                           "line": n, "evidence": "the PC has connected to it over SSH (known_hosts)",
                           "count": 1, "never_scan": known_host_for(h) in NEVER_SCAN}
    return list(hits.values())


# --------------------------------------------------------------------------
# Step: plan

def new_manifest(cfg: Cfg) -> dict:
    return {"schema": SCHEMA, "harvest_date": cfg.date, "generated": now().isoformat(timespec="seconds"),
            "window_days": cfg.days, "window_since": iso(cfg.since), "pc": socket.gethostname(),
            "mode": "dry-run" if cfg.dry_run else "real", "hosts": {}, "rows": [], "candidates": [],
            "returns_index": {}, "tailscale_peers": [], "possible_hosts": [], "pushed": {},
            "steps": {}, "project_sync": "not run",
            "project_sync_line": ("Run from the Master Orchestrator Project: the thread syncs the non-restricted runs "
                                  "into the Project files after this report." if cfg.from_project else "")}


def load_manifest(cfg: Cfg) -> dict:
    if cfg.manifest_path.exists():
        m = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
        if m.get("harvest_date") == cfg.date:
            return m
    return new_manifest(cfg)


def save_manifest(cfg: Cfg, m: dict) -> None:
    cfg.dest.mkdir(parents=True, exist_ok=True)
    m["generated"] = now().isoformat(timespec="seconds")
    tmp = cfg.manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=False), encoding="utf-8")
    os.replace(tmp, cfg.manifest_path)


def cmd_plan(cfg: Cfg) -> dict:
    m = new_manifest(cfg)
    rows: list[dict] = []
    docs = [p for p in doc_files(cfg) if doc_in_window(p, cfg.since)]
    for p in docs:
        rows = merge_rows(rows, parse_doc(p))
    rows = merge_rows(rows, read_fleet_state(cfg))
    for f in cfg.extra_rows:
        extra = json.loads(f.read_text(encoding="utf-8"))
        for r in extra:
            r.setdefault("sources", [r.get("source_doc", f"extra:{f.name}")])
            r.setdefault("status", "planned")
        rows = merge_rows(rows, extra)
    idx = returns_index(cfg)
    for r in rows:
        if r["id"] in idx:
            r["already_returned"] = idx[r["id"]]["sha256"]
    # Returns-repo entries inside the window that no plan names.
    planned = {r["id"] for r in rows}
    for rid, info in idx.items():
        if rid not in planned and info["ts"] >= cfg.since:
            rows.append({"id": rid, "title": rid, "source_doc": "returns-repo", "sources": ["returns-repo"],
                         "host_hint": info["host_hint"], "path_hint": "", "account": "", "launch_path": "",
                         "model_hint": [], "effort_hint": [], "prompt_file": "", "restricted_mark": False,
                         "status": "planned", "already_returned": info["sha256"]})
    for r in rows:
        r.pop("_ts", None)
    m["rows"] = rows
    m["returns_index"] = {k: {"path": v["path"], "sha256": v["sha256"]} for k, v in idx.items()}
    m["docs_read"] = [str(p) for p in docs]
    m["tailscale_peers"] = tailscale_peers()
    try:
        m["possible_hosts"] = evidence_sweep(cfg, m["tailscale_peers"])
    except Exception as e:  # a refusal or unreadable file is a report line, never a stop
        m["possible_hosts"] = []
        m["steps"]["sweep_error"] = str(e)
    m["steps"]["plan"] = now().isoformat(timespec="seconds")
    save_manifest(cfg, m)
    log(f"plan: {len(rows)} rows from {len(docs)} documents, {len(idx)} returns in the repo")
    return m


# --------------------------------------------------------------------------
# Step: scan

def scan_remote(cfg: Cfg, host: Host, patterns: list[str]) -> list[dict]:
    budget = 300 if cfg.slow else 60
    lines, rc, err, _ = run_stream(ssh_base(host.target) + ["hostname"], timeout=20)
    if rc != 0 or not lines:
        host.reachable = False
        host.error = (err.splitlines() or ["no answer"])[-1][:200]
        return []
    host.reachable = True
    pats = sorted({p for p in patterns if p and len(p) < 120 and "'" not in p})[:200]
    remote_cmd = "sh -s -- find " + " ".join(shlex.quote(x) for x in [str(cfg.days), str(budget), *pats])
    lines, rc, err, timed_out = run_stream(ssh_base(host.target) + [remote_cmd], timeout=budget + 15,
                                           stdin_bytes=finder_bytes())
    recs = []
    for line in lines:
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    done = [r for r in recs if r.get("kind") == "done"]
    host.partial = timed_out or not done or bool(done and done[-1].get("partial")) or any(r.get("cut_off") for r in recs)
    for r in recs:
        if r.get("kind") == "host":
            host.info = r
    if err and not recs:
        host.error = err.splitlines()[-1][:200]
    return recs


PC_SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "blitz-returns", ".next", ".turbo", ".cache"}
MARKERS = ("state.json", "PROGRESS.md", "RETURN.md", "SPRINT.md", "GOAL.txt", "MANIFEST.md")


def _pc_marker(d: Path, name: str) -> Path | None:
    for c in [d / name, d / "loop" / name, *d.glob(f"loops/*/{name}"), *d.glob(f"*/{name}")]:
        if c.is_file():
            return c
    return None


def _pc_root(cfg: Cfg, marker: Path) -> Path | None:
    """The run folder that owns a marker file under C:\\Projects: the folder
    holding it, lifted past loop/, loops/<ID>/ and work/<ID>/."""
    d = marker.parent
    if d.name == "loop":
        d = d.parent
    if d.parent.name in ("loops", "work"):
        d = d.parent.parent
    try:
        rel = d.relative_to(cfg.projects)
    except ValueError:
        return None
    if not rel.parts or rel.parts[0].startswith("."):
        return None
    if d == cfg.root or d == cfg.returns or cfg.dest in d.parents or d == cfg.dest:
        return None
    return d


def _folder_record(d: Path, patterns: list[str], since_ok: bool = True) -> dict:
    st, pr, rt = _pc_marker(d, "state.json"), _pc_marker(d, "PROGRESS.md"), _pc_marker(d, "RETURN.md")
    gl, sp, mf = _pc_marker(d, "GOAL.txt"), _pc_marker(d, "SPRINT.md"), _pc_marker(d, "MANIFEST.md")
    newest = d.stat().st_mtime
    for c in (st, pr, rt):
        if c:
            newest = max(newest, c.stat().st_mtime)
    heads = []
    for h in (d / "README.md", mf, rt):
        if h and h.is_file():
            heads += [ln[:120] for ln in h.read_text(encoding="utf-8", errors="replace").splitlines()
                      if re.match(r"^#{1,3} ", ln)][:15]
    status = ""
    if st:
        mm = re.search(r'"(status|state|phase)"\s*:\s*"([A-Za-z_ -]{1,30})"', st.read_text(encoding="utf-8", errors="replace"))
        status = mm.group(2) if mm else ""
    ctl = "\n".join(c.read_text(encoding="utf-8", errors="replace")[:20000]
                    for c in (st, pr, gl, sp, d / "ORIGINAL-PROMPT.md") if c and c.is_file())
    models = sorted({x.lower() for x in re.findall(
        r"(?i)claude-(?:fable|opus|sonnet|haiku)-\d+(?:-\d+)?|(?:fable|opus|sonnet|haiku) \d+(?:\.\d+)?", ctl)})[:8]
    markers = sorted({x.lower() for x in re.findall(
        r"(?i)/goal|/loop|ultracode|workflow tool|MAX_HOURS=\d+|xhigh|max effort|high effort|watchdog|run-sprint", ctl)})
    matches = sorted({p for p in patterns if p and p in ctl})
    return {"kind": "folder", "path": str(d), "name": d.name, "mtime": newest,
            "has_state": bool(st), "has_progress": bool(pr), "has_return": bool(rt), "has_goal": bool(gl),
            "has_sprint": bool(sp), "has_manifest": bool(mf), "has_loops": (d / "loops").is_dir(),
            "return_sha256": sha256_file(rt) if rt else "", "run_status": status, "headings": heads,
            "models": models, "markers": markers, "matches": matches}


def scan_pc(cfg: Cfg, patterns: list[str]) -> list[dict]:
    """The PC's own runs: sprint-shaped folders under C:\\Projects (no hidden
    or personal folders) and Claude Code / Codex session metadata whose cwd is
    under C:\\Projects."""
    recs: list[dict] = [{"kind": "host", "hostname": socket.gethostname(), "account_hint": "el@elliotl.im"}]
    roots: dict[str, Path] = {}
    base = cfg.projects
    if base.exists():
        for dirpath, dirnames, filenames in os.walk(base):
            depth = len(Path(dirpath).relative_to(base).parts)
            dirnames[:] = [d for d in dirnames if d not in PC_SKIP and not d.startswith(".")
                           and Path(dirpath, d) != cfg.returns]
            if depth >= 6:
                dirnames[:] = []
            for f in filenames:
                if f in MARKERS:
                    mp = Path(dirpath, f)
                    try:
                        if mp.stat().st_mtime < cfg.since:
                            continue
                    except OSError:
                        continue
                    r = _pc_root(cfg, mp)
                    if r:
                        roots[str(r)] = r
    # Session metadata: only cwd, model ids, timestamps and counts.
    home = Path.home()
    for tool, sdir in (("claude", home / ".claude" / "projects"), ("codex", home / ".codex" / "sessions")):
        if not sdir.exists():
            continue
        for f in sdir.rglob("*.jsonl"):
            try:
                stt = f.stat()
            except OSError:
                continue
            if stt.st_mtime < cfg.since:
                continue
            rec = session_meta(f, tool)
            cwd = rec.get("cwd", "")
            root = ""
            if cwd:
                cp = Path(cwd)
                try:
                    rel = cp.relative_to(base)
                    if rel.parts and not rel.parts[0].startswith("."):
                        r = cp
                        # Lift to the nearest ancestor that holds sprint control files.
                        for anc in [cp, *cp.parents]:
                            if anc == base:
                                break
                            if any((anc / mk).exists() for mk in MARKERS) or (anc / "loop").is_dir():
                                r = anc
                                break
                        if r != base:
                            root = str(r)
                            if r.exists() and r != cfg.root:
                                roots[str(r)] = r
                except ValueError:
                    pass
            rec["root"] = root
            recs.append(rec)
    for r in roots.values():
        try:
            recs.append(_folder_record(r, patterns))
        except OSError:
            continue
    # Blitz ledger pages saved in Downloads: names only, left in place.
    dl = home / "Downloads"
    if dl.exists():
        for f in dl.glob("*.html"):
            if re.search(r"(?i)blitz|ledger|sprint|loop|initiative", f.name):
                try:
                    if f.stat().st_mtime >= cfg.since:
                        recs.append({"kind": "page", "path": str(f), "mtime": f.stat().st_mtime})
                except OSError:
                    pass
    recs.append({"kind": "done", "partial": False})
    return recs


def session_meta(f: Path, tool: str) -> dict:
    head_lines = []
    try:
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 80:
                    break
                head_lines.append(line)
    except OSError:
        return {"kind": "session", "tool": tool, "file": str(f)}
    head = "".join(head_lines)
    cwd_m = re.search(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    cwd = json.loads('"' + cwd_m.group(1) + '"') if cwd_m else ""
    first_ts = re.search(r'"timestamp"\s*:\s*"([^"]*)"', head)
    models: dict[str, int] = {}
    agents = workflows = 0
    last_ts = ""
    size = f.stat().st_size
    with open(f, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            for mm in re.findall(r'"model"\s*:\s*"([^"]*)"', line):
                models[mm] = models.get(mm, 0) + 1
            agents += len(re.findall(r'"name"\s*:\s*"(?:Agent|Task)"', line))
            workflows += len(re.findall(r'"name"\s*:\s*"Workflow"', line))
            t = re.search(r'"timestamp"\s*:\s*"([^"]*)"', line)
            if t:
                last_ts = t.group(1)
    markers = sorted({x.lower() for x in re.findall(
        r"(?i)/goal|/loop|ultracode|ultrathink|MAX_HOURS|xhigh|max effort|high effort|run-sprint", head)})
    for e in re.findall(r'"effort"\s*:\s*"([a-z]+)"', head):
        markers.append(f"effort:{e}")
    return {"kind": "session", "tool": tool, "file": str(f), "cwd": cwd,
            "models": [k for k, _ in sorted(models.items(), key=lambda kv: -kv[1])][:5],
            "first_ts": first_ts.group(1) if first_ts else "", "last_ts": last_ts,
            "mtime": f.stat().st_mtime, "bytes": size, "agent_calls": agents,
            "workflow_calls": workflows, "markers": sorted(set(markers))}


def _patterns(rows: list[dict]) -> list[str]:
    pats = set()
    for r in rows:
        pats.add(r["id"])
        if r.get("prompt_file"):
            pats.add(r["prompt_file"])
    return sorted(pats)


def _is_known_run_root(path: str) -> bool:
    return bool(re.search(r"/orchestrator/(sprints|returns)/[^/]+$", path))


def _sessions_for(folder: dict, sessions: list[dict]) -> list[dict]:
    path = folder["path"]
    out = []
    for s in sessions:
        cwd = s.get("cwd") or ""
        if not cwd:
            continue
        if s.get("root") == path or cwd == path or cwd.startswith(path.rstrip("/\\") + ("/" if "/" in path else "\\")):
            out.append(s)
            continue
        # A session opened on the container (e.g. ~/orchestrator/sprints) that
        # was active when this folder last changed.
        parent = path.rsplit("/", 1)[0] if "/" in path else str(Path(path).parent)
        if cwd.rstrip("/\\") == parent.rstrip("/\\"):
            a, b = parse_ts(s.get("first_ts", "")), parse_ts(s.get("last_ts", ""))
            if a and b and a - 3600 <= folder.get("mtime", 0) <= b + 3600:
                out.append(s)
    return out


def push_signals(host: Host, folder: dict | None, sessions: list[dict], source_returns: bool = False,
                 account: str = "") -> list[str]:
    sig = []
    models = set(m.lower() for m in (folder or {}).get("models", []))
    for s in sessions:
        models |= {m.lower() for m in s.get("models", [])}
    if any(m in models for m in LATEST_MODELS):
        sig.append("model")
    marks = set((folder or {}).get("markers", []))
    for s in sessions:
        marks |= set(s.get("markers", []))
    long_run = False
    for s in sessions:
        a, b = parse_ts(s.get("first_ts", "")), parse_ts(s.get("last_ts", ""))
        if a and b and b - a >= 2 * 3600:
            long_run = True
    fanout = any(s.get("workflow_calls", 0) > 0 or s.get("agent_calls", 0) >= 3 for s in sessions)
    if any(any(k in m for k in INTENSITY_MARKERS) for m in marks) or long_run or fanout:
        sig.append("intensity")
    if host.name != "pc" or host.account_key in ("cybernova", "gmail"):
        sig.append("location")
    acct = ACCOUNT_KEYS.get((account or "").lower()) or host.account_key or ("primary" if host.name == "pc" else "")
    starts = [parse_ts(s.get("first_ts", "")) for s in sessions]
    starts = [t for t in starts if t]
    start = min(starts) if starts else (folder or {}).get("mtime")
    if acct in RESETS and start and _hours_before_reset(start, *RESETS[acct]) <= 48:
        sig.append("timing")
    if folder and (folder.get("has_state") or folder.get("has_progress") or folder.get("has_return")
                   or folder.get("has_goal") or folder.get("has_sprint") or folder.get("has_manifest")
                   or _is_known_run_root(folder.get("path", ""))) or source_returns:
        sig.append("shape")
    return sig


def _hours_before_reset(ts: float, weekday: int, hour: int) -> float:
    t = dt.datetime.fromtimestamp(ts, UTC)
    reset = t.replace(hour=hour, minute=0, second=0, microsecond=0)
    reset += dt.timedelta(days=(weekday - t.weekday()) % 7)
    if reset <= t:
        reset += dt.timedelta(days=7)
    return (reset - t).total_seconds() / 3600


def classify(cfg: Cfg, row: dict, folder: dict | None) -> tuple[object, str]:
    if row.get("fleet_sensitive"):
        return True, "dispatched with --sensitive"
    if row.get("restricted_mark"):
        return True, "its prompt marks it restricted"
    if folder and folder.get("cut_off"):
        return "unknown", "folder could not be read in time"
    # Names only: id, title, the folder's path below the home or Projects
    # folder, and markdown headings. Output bodies are never read.
    path = (folder or {}).get("path") or row.get("path") or row.get("path_hint") or ""
    path = re.sub(r"^(/Users/[^/]+|/home/[^/]+|[A-Za-z]:\\Users\\[^\\]+)", "", path)
    hay = " ".join([row.get("id", ""), row.get("title", ""), path,
                    (folder or {}).get("name", ""), " ".join((folder or {}).get("headings", []))]).lower()
    hay = re.sub(r"[_\-/\\.]", " ", hay)
    for w in cfg.sensitive_words():
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", hay):
            return True, f'topic word "{w}"'
    if folder is None and row.get("status") in ("not_found", "not_found_yet"):
        return False, ""
    return False, ""


def cmd_scan(cfg: Cfg, m: dict | None = None) -> dict:
    m = m or load_manifest(cfg)
    if not m.get("rows") and "plan" not in m.get("steps", {}):
        m = cmd_plan(cfg)
    rows = m["rows"]
    pats = _patterns(rows)
    hosts = [Host("pc", None, "el@elliotl.im", source="local")] + load_hosts(cfg)
    if cfg.only_host:
        hosts = [h for h in hosts if h.name == cfg.only_host]
    cfg.scan_dir.mkdir(parents=True, exist_ok=True)
    folders_by_host: dict[str, list[dict]] = {}
    sessions_by_host: dict[str, list[dict]] = {}
    other_by_host: dict[str, list[dict]] = {}
    for h in hosts:
        log(f"scan: {h.name} ...")
        if h.name == "pc":
            recs = scan_pc(cfg, pats)
            h.reachable = True
            h.info = recs[0]
        else:
            recs = scan_remote(cfg, h, pats)
        # Raw scan output stays on the PC (never synced to the Project).
        (cfg.scan_dir / f"{h.name}.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
        folders_by_host[h.name] = [r for r in recs if r.get("kind") == "folder"]
        sessions_by_host[h.name] = [r for r in recs if r.get("kind") == "session"]
        other_by_host[h.name] = [r for r in recs if r.get("kind") in ("prompts", "zip")]
        if h.name == "pc":
            m["pc_pages"] = [Path(r["path"]).name for r in recs if r.get("kind") == "page"]
        m["hosts"][h.name] = h.to_json()
        log(f"scan: {h.name}: reachable={h.reachable} partial={h.partial} folders={len(folders_by_host[h.name])} "
            f"sessions={len(sessions_by_host[h.name])} {h.error}")

    hosts_by_name = {h.name: h for h in hosts}
    used: set[tuple[str, str]] = set()

    def match(row: dict) -> tuple[str, dict, str] | None:
        order = [row.get("host_hint")] if row.get("host_hint") in folders_by_host else []
        order += [n for n in folders_by_host if n not in order]
        for hn in order:
            for f in folders_by_host[hn]:
                if f.get("cut_off"):
                    continue
                if f["name"] == row["id"] and not _is_returns_copy(f):
                    return hn, f, "folder name equals the sprint id"
            for f in folders_by_host[hn]:
                if row["id"] in f.get("matches", []) and not _is_returns_copy(f):
                    return hn, f, "state.json or PROGRESS.md names the sprint id"
            if row.get("prompt_file"):
                for f in folders_by_host[hn]:
                    if row["prompt_file"] in f.get("matches", []) and not _is_returns_copy(f):
                        return hn, f, "state.json or PROGRESS.md names the prompt file"
            for f in folders_by_host[hn]:
                if f["name"] == row["id"]:
                    return hn, f, "a returns copy carries the sprint id"
        return None

    for row in rows:
        res = match(row)
        if res:
            hn, f, why = res
            used.add((hn, f["path"]))
            sess = _sessions_for(f, sessions_by_host.get(hn, []))
            row.update({"status": "found", "host": hn, "path": f["path"], "match_reason": why,
                        "has_return": f.get("has_return"), "return_sha256": f.get("return_sha256", ""),
                        "run_status": f.get("run_status", ""), "mtime": f.get("mtime"),
                        "returns_copy": _is_returns_copy(f)})
            row["push_signals"] = push_signals(hosts_by_name[hn], f, sess, account=row.get("account", ""))
            row["push_score"] = len(row["push_signals"])
            row["sensitive"], row["sensitive_reason"] = classify(cfg, row, f)
            row["harvest"] = True
        else:
            hint = row.get("host_hint") or ""
            h = hosts_by_name.get(hint)
            row["status"] = "not_found_yet" if h is not None and h.reachable is False else "not_found"
            row["sensitive"], row["sensitive_reason"] = classify(cfg, row, None)
            row["harvest"] = False
            row["why_missing"] = _why_missing(row, hosts_by_name, other_by_host)

    # Fold ~/orchestrator/returns/<id> copies into their sprint folder.
    sprint_ids = {(r.get("host"), Path(r.get("path", "")).name) for r in rows if r.get("status") == "found" and not r.get("returns_copy")}
    for r in rows:
        if r.get("returns_copy") and (r.get("host"), r["id"]) in sprint_ids:
            r["harvest"] = False
            r["status"] = "found"
            r["note"] = "returns copy of a sprint folder that is harvested"

    # Unmatched candidates: discovered runs and pattern-recognized pushes.
    cands = []
    for hn, flist in folders_by_host.items():
        h = hosts_by_name[hn]
        sprint_names = {f["name"] for f in flist if not _is_returns_copy(f)}
        for f in flist:
            if (hn, f["path"]) in used:
                continue
            if _is_returns_copy(f) and f["name"] in sprint_names:
                continue
            in_window = (f.get("mtime") or 0) >= cfg.since
            sess = _sessions_for(f, sessions_by_host.get(hn, []))
            if not in_window and not any((parse_ts(s.get("last_ts", "")) or s.get("mtime", 0)) >= cfg.since for s in sess):
                continue
            sig = push_signals(h, f, sess)
            score = len(sig)
            known = _is_known_run_root(f["path"])
            row = {"id": f["name"], "title": f["name"], "host": hn, "path": f["path"],
                   "source_doc": f"discovered:{hn}" if known else f"pattern:{hn}",
                   "sources": [f"discovered:{hn}" if known else f"pattern:{hn}"],
                   "status": "found", "match_reason": "sprint folder not in any plan" if known else "pattern pass",
                   "has_return": f.get("has_return"), "return_sha256": f.get("return_sha256", ""),
                   "run_status": f.get("run_status", ""), "mtime": f.get("mtime"),
                   "push_signals": sig, "push_score": score, "returns_copy": _is_returns_copy(f),
                   "sessions": len(sess), "models_seen": sorted({x for s in sess for x in s.get("models", [])})[:4]}
            row["sensitive"], row["sensitive_reason"] = classify(cfg, row, f)
            if f.get("cut_off"):
                row["harvest"], row["decision"] = False, "cut off before it was read; re-run with --slow"
            elif known or score >= 4:
                row["harvest"], row["decision"] = True, "harvest"
            elif score >= 2:
                row["harvest"], row["decision"] = False, "confirm"
            else:
                row["harvest"], row["decision"] = False, "ignored (low signal)"
            cands.append(row)
    # Sessions whose folder is not a candidate (e.g. a cwd outside the allowed
    # roots) are counted, never copied.
    stray = []
    for hn, slist in sessions_by_host.items():
        for s in slist:
            if not s.get("root") and (parse_ts(s.get("last_ts", "")) or s.get("mtime", 0)) >= cfg.since:
                if any(m_ in (s.get("models") or []) for m_ in ("claude-fable-5-1", "claude-opus-5-5")):
                    stray.append({"host": hn, "cwd": s.get("cwd", ""), "models": s.get("models", [])[:2],
                                  "first_ts": s.get("first_ts", ""), "last_ts": s.get("last_ts", "")})
    m["candidates"] = cands
    m["stray_sessions"] = stray[:200]
    m["host_extras"] = {hn: [{"kind": o["kind"], "path": o.get("path") or o.get("dir"),
                               "files": len(o.get("files", [])) if o.get("files") else None} for o in lst]
                        for hn, lst in other_by_host.items()}
    m["steps"]["scan"] = now().isoformat(timespec="seconds")
    save_manifest(cfg, m)
    return m


def _is_returns_copy(f: dict) -> bool:
    return bool(re.search(r"/orchestrator/returns/[^/]+$", f.get("path", "")))


def _why_missing(row: dict, hosts: dict[str, Host], extras: dict[str, list[dict]]) -> str:
    hint = row.get("host_hint")
    h = hosts.get(hint or "")
    if h is not None and h.reachable is False:
        return f"{hint} was unreachable ({h.error or 'no answer'})"
    staged = any(row.get("prompt_file") and row["prompt_file"] in (o.get("files") or [])
                 for lst in extras.values() for o in lst if o.get("kind") == "prompts")
    if staged:
        return "prompt is staged on the host but no run folder exists: probably never launched"
    if row.get("already_returned"):
        return "its RETURN.md is in orchestrator-returns; the run folder is gone or elsewhere"
    if row.get("source_doc", "").startswith("weekly-blitz"):
        return "named in a blitz plan; no run folder on any reachable host (never launched, or renamed)"
    return "no run folder on any reachable host: never launched, deleted, or renamed"


# --------------------------------------------------------------------------
# Step: copy

def _bad_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p) for p in NEVER_COPY)


def member_allowed(rel: PurePosixPath, size: int, is_dir: bool = False) -> tuple[bool, str]:
    parts = rel.parts
    if not parts or rel.is_absolute() or ".." in parts:
        return False, "unsafe path"
    for i, p in enumerate(parts):
        if p in SKIP_DIRS:
            return False, "cache or dependency folder"
        if p == ".git":
            rest = parts[i + 1:]
            if is_dir and len(rest) == 0:
                return True, ""
            if len(rest) == 1 and rest[0] in GIT_KEEP:
                return True, ""
            return False, "git history"
    name = parts[-1]
    if any(fnmatch.fnmatch(name, j) for j in JUNK):
        return False, "macOS metadata"
    if not is_dir and _bad_name(name):
        return False, "credential-like name"
    if not is_dir and name.lower().endswith(".log") and size > BIG_LOG_BYTES:
        return False, "log over 50 MB"
    return True, ""


def _win_safe(part: str) -> str:
    if os.name != "nt":
        return part
    part = re.sub(r'[<>:"|?*\x00-\x1f]', "_", part).rstrip(" .") or "_"
    if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        part = "_" + part
    return part


def _long(p: Path) -> str:
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\") and len(s) > 240:
        return "\\\\?\\" + os.path.abspath(s)
    return s


def remote_tar_cmd(path: str, control_only: bool, extra_excludes: list[str]) -> str:
    parent, name = path.rsplit("/", 1)
    ex = []
    for pat in list(NEVER_COPY) + [p.upper() for p in ("*token*", "*secret*")] + ["*Token*", "*Secret*"]:
        ex.append("--exclude=" + shlex.quote(pat))
    for d in SKIP_DIRS:
        ex.append("--exclude=" + shlex.quote(d))
    for d in (".git/objects", ".git/lfs", ".git/modules", ".git/worktrees", ".git/logs", ".git/refs", ".DS_Store", "._*"):
        ex.append("--exclude=" + shlex.quote(d))
    for e in extra_excludes:
        ex.append("--exclude=" + shlex.quote(f"{name}/{e}"))
    if control_only:
        names = " -o ".join(f"-name {shlex.quote(c)}" for c in CONTROL_FILES)
        return (f"cd {shlex.quote(parent)} && find {shlex.quote(name)} -maxdepth 4 -type f \\( {names} \\) -size -4096k -print "
                f"| COPYFILE_DISABLE=1 tar -czf - {' '.join(ex)} -T -")
    return f"cd {shlex.quote(parent)} && COPYFILE_DISABLE=1 tar -czf - {' '.join(ex)} {shlex.quote(name)}"


def extract_stream(fileobj, dest: Path, strip: str) -> dict:
    """Write a tar stream into dest, member by member, applying the copy rules
    a second time on this side. Links and devices are skipped."""
    stats = {"files": 0, "bytes": 0, "skipped": {}, "errors": []}
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tf:
        for mbr in tf:
            rel = PurePosixPath(mbr.name)
            if rel.parts and rel.parts[0] == strip:
                rel = PurePosixPath(*rel.parts[1:]) if len(rel.parts) > 1 else PurePosixPath()
            if not rel.parts:
                continue
            ok, why = member_allowed(rel, mbr.size, mbr.isdir())
            if not ok or not (mbr.isfile() or mbr.isdir()):
                why = why or "link or special file"
                stats["skipped"][why] = stats["skipped"].get(why, 0) + 1
                continue
            target = dest.joinpath(*[_win_safe(p) for p in rel.parts])
            try:
                if mbr.isdir():
                    os.makedirs(_long(target), exist_ok=True)
                    continue
                os.makedirs(_long(target.parent), exist_ok=True)
                src = tf.extractfile(mbr)
                with open(_long(target), "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)  # type: ignore[arg-type]
                os.utime(_long(target), (mbr.mtime, mbr.mtime))
                stats["files"] += 1
                stats["bytes"] += mbr.size
            except OSError as e:
                stats["errors"].append(f"{rel}: {e}"[:200])
    return stats


def remote_size(host: Host, path: str, timeout: int = 180) -> dict:
    cmd = "sh -s -- size " + shlex.quote(path)
    lines, rc, err, to = run_stream(ssh_base(host.target) + [cmd], timeout=timeout, stdin_bytes=finder_bytes())
    for line in lines:
        try:
            o = json.loads(line)
            if o.get("kind") == "size":
                return o
        except json.JSONDecodeError:
            continue
    return {"error": err[:200] or ("timed out" if to else "no answer")}


def local_size(path: Path) -> dict:
    files = bytes_ = 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != ".git"]
        for f in filenames:
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            files += 1
            bytes_ += st.st_size
            newest = max(newest, st.st_mtime)
    rt = _pc_marker(path, "RETURN.md")
    return {"files": files, "bytes": bytes_, "newest": int(newest),
            "return_sha256": sha256_file(rt) if rt else "", "big_logs": [], "credential_like": []}


def _fingerprint(sz: dict) -> dict:
    return {k: sz.get(k) for k in ("files", "bytes", "newest", "return_sha256")}


def _versions(base: Path) -> list[Path]:
    out = [base] if base.exists() else []
    out += sorted((p for p in base.parent.glob(base.name + "-v*") if re.fullmatch(re.escape(base.name) + r"-v\d+", p.name)),
                  key=lambda p: int(p.name.rsplit("-v", 1)[1]))
    return out


def _sidecar(p: Path) -> dict:
    s = p / ".harvest.json"
    if s.exists():
        try:
            return json.loads(s.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def harvest_targets(m: dict) -> list[dict]:
    """Rows and candidates to copy, one per (host, path): two plan rows that
    name the same folder are copied once."""
    out, seen = [], set()
    for r in [*m.get("rows", []), *m.get("candidates", [])]:
        if not r.get("harvest"):
            continue
        key = (r.get("host"), r.get("path"))
        if key in seen:
            r["copy_status"] = r.get("copy_status") or "same folder as another row"
            continue
        seen.add(key)
        out.append(r)
    return out


def cmd_copy(cfg: Cfg, m: dict | None = None) -> dict:
    m = m or load_manifest(cfg)
    if "scan" not in m.get("steps", {}):
        m = cmd_scan(cfg, m)
    hosts = {h.name: h for h in load_hosts(cfg)}
    targets = harvest_targets(m)
    # Sizes first, so the disk check covers the whole harvest.
    total = 0
    for r in targets:
        if r["host"] == "pc":
            r["size"] = local_size(Path(r["path"]))
        else:
            h = hosts.get(r["host"])
            if h is None:
                r["copy_status"] = "error"
                r["copy_error"] = "host not in the host list"
                continue
            r["size"] = remote_size(h, r["path"])
        sz = r["size"]
        if sz.get("error"):
            r["copy_status"], r["copy_error"] = "error", sz["error"]
            continue
        r["size_bytes"] = sz.get("bytes", 0)
        r["credential_like_skipped"] = len(sz.get("credential_like") or [])
        if r["host"] != "pc":
            total += min(sz.get("bytes", 0), FOLDER_CAP_BYTES)
        if sz.get("return_sha256"):
            r["return_sha256"] = sz["return_sha256"]
    free = shutil.disk_usage(cfg.dest if cfg.dest.exists() else cfg.dest.parent).free
    m["disk"] = {"free_bytes": free, "needed_bytes": total}
    if not cfg.dry_run and free < 2 * total:
        m["steps"]["copy_refused"] = f"free space {free >> 20} MB is under twice the harvest ({total >> 20} MB)"
        for r in targets:
            r.setdefault("copy_status", "refused (disk)")
        save_manifest(cfg, m)
        log("copy: refused, not enough free disk")
        return m

    for r in targets:
        if r.get("copy_status") == "error":
            continue
        sz = r["size"]
        safe_id = _win_safe(Path(r["path"].replace("\\", "/")).name)
        base = cfg.dest / r["host"] / safe_id
        fp = _fingerprint(sz)
        versions = _versions(base)
        latest = versions[-1] if versions else None
        if latest is not None and _sidecar(latest).get("fingerprint") == fp:
            r["copy_status"], r["dest"] = "unchanged", str(latest)
            continue
        dest = base if latest is None else base.parent / f"{base.name}-v{len(versions) + 1}"
        r["dest"] = str(dest)
        control_only = sz.get("bytes", 0) > FOLDER_CAP_BYTES
        r["control_only"] = control_only
        if cfg.dry_run:
            r["copy_status"] = "dry-run"
            log(f"copy (dry run): {r['host']}:{r['path']} -> {dest} ({sz.get('bytes', 0) >> 20} MB"
                f"{', control files only' if control_only else ''})")
            continue
        if r["host"] == "pc":
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "RUNS-HERE.md").write_text(
                f"# {r['id']}\n\nThis run lives on the PC; the harvest records it by reference.\n\n"
                f"- Path: `{r['path']}`\n- Files: {sz.get('files')} ({sz.get('bytes', 0) >> 10} KB)\n"
                f"- RETURN.md: {'yes' if sz.get('return_sha256') else 'no'}\n- Harvested: {cfg.date}\n",
                encoding="utf-8")
            _write_sidecar(dest, r, fp, cfg, {"files": 0, "bytes": 0, "skipped": {}, "errors": []})
            r["copy_status"] = "by_reference"
            continue
        h = hosts[r["host"]]
        big = [b for b in (sz.get("big_logs") or [])]
        cmd = remote_tar_cmd(r["path"], control_only, big)
        tmp = dest.parent / (dest.name + ".partial")
        if tmp.exists():
            shutil.rmtree(_long(tmp), ignore_errors=True)
        tmp.mkdir(parents=True)
        log(f"copy: {r['host']}:{r['path']} -> {dest}")
        try:
            p = subprocess.Popen(ssh_base(h.target) + [cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            errbuf: list[bytes] = []
            te = threading.Thread(target=lambda: errbuf.append(p.stderr.read()), daemon=True)  # type: ignore[union-attr]
            te.start()
            stats = extract_stream(p.stdout, tmp, Path(r["path"]).name)
            p.wait(timeout=120)
            te.join(10)
            err = b"".join(errbuf).decode("utf-8", "replace").strip()
            if p.returncode not in (0, None) and stats["files"] == 0:
                raise RuntimeError(err.splitlines()[-1] if err else f"exit {p.returncode}")
            if err:
                stats["errors"].append(err.splitlines()[-1][:200])
        except Exception as e:
            shutil.rmtree(_long(tmp), ignore_errors=True)
            r["copy_status"], r["copy_error"] = "error", str(e)[:200]
            continue
        _write_sidecar(tmp, r, fp, cfg, stats)
        os.replace(_long(tmp), _long(dest))
        r["copy_status"] = "copied_control_only" if control_only else "copied"
        r["copied_files"], r["copied_bytes"] = stats["files"], stats["bytes"]
        r["skipped"] = stats["skipped"]
        if stats["errors"]:
            r["copy_warnings"] = stats["errors"][:5]
    # Defense in depth: nothing credential-like may exist under blitz-returns.
    m["credential_check"] = credential_check(cfg.dest)
    m["steps"]["copy"] = now().isoformat(timespec="seconds")
    save_manifest(cfg, m)
    return m


def _write_sidecar(d: Path, r: dict, fp: dict, cfg: Cfg, stats: dict) -> None:
    (d / ".harvest.json").write_text(json.dumps({
        "id": r["id"], "host": r["host"], "source_path": r["path"], "harvested": cfg.date,
        "fingerprint": fp, "sensitive": r.get("sensitive"), "files": stats["files"], "bytes": stats["bytes"],
        "skipped": stats["skipped"]}, indent=1), encoding="utf-8")


def credential_check(root: Path) -> list[str]:
    bad = []
    if not root.exists():
        return bad
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if _bad_name(f):
                bad.append(os.path.join(dirpath, f))
    return bad


# --------------------------------------------------------------------------
# Step: push

def git(cfg: Cfg, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cfg.returns), *args], capture_output=True, text=True, timeout=timeout)


def _find_return(p: Path) -> Path | None:
    return _pc_marker(p, "RETURN.md") if p.exists() else None


def cmd_push(cfg: Cfg, m: dict | None = None) -> dict:
    m = m or load_manifest(cfg)
    res: dict = {"files": [], "commit": "", "error": "", "fleet_pull": ""}
    if not (cfg.returns / ".git").exists():
        res["error"] = f"no git clone at {cfg.returns}"
        m["pushed"] = res
        save_manifest(cfg, m)
        return m
    if cfg.dry_run:
        res["error"] = "dry run: nothing pushed"
    else:
        # fleet.py pull already moves RETURN.md for the sprints it dispatched;
        # run it first and only add what it misses (Path B and pattern runs).
        fleet_py = cfg.tools / "fleet.py"
        if cfg.fleet_pull and fleet_py.exists():
            try:
                out = subprocess.run([sys.executable, str(fleet_py), "pull"], capture_output=True, text=True, timeout=900)
                res["fleet_pull"] = (out.stdout.strip().splitlines() or [""])[-1][:200] + (
                    f" (exit {out.returncode})" if out.returncode else "")
            except Exception as e:
                res["fleet_pull"] = f"failed: {e}"[:200]
        pull = git(cfg, "pull", "--ff-only")
        if pull.returncode:
            res["pull_warning"] = (pull.stderr.strip().splitlines() or [""])[-1][:200]
    idx = returns_index(cfg)
    for r in harvest_targets(m):
        src = None
        if r.get("dest") and r.get("copy_status") in ("copied", "copied_control_only", "unchanged"):
            src = _find_return(Path(r["dest"]))
        elif r["host"] == "pc":
            src = _find_return(Path(r["path"]))
        if not src:
            continue
        sha = sha256_file(src)
        have = idx.get(r["id"])
        if have and have["sha256"] == sha:
            r["return_push"] = "already in orchestrator-returns"
            continue
        target = Path(have["path"]) if have else cfg.returns / _win_safe(r["id"]) / "RETURN.md"
        r["return_push"] = "updated" if have else "new"
        if cfg.dry_run:
            r["return_push"] += " (dry run)"
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        res["files"].append(str(target.relative_to(cfg.returns)))
    if res["files"] and not cfg.dry_run:
        git(cfg, "add", "--", *res["files"])
        c = git(cfg, "commit", "-m", f"blitz-harvest {cfg.date}: {len(res['files'])} RETURN.md")
        if c.returncode:
            res["error"] = (c.stderr.strip() or c.stdout.strip()).splitlines()[-1][:200]
        else:
            res["commit"] = git(cfg, "rev-parse", "--short", "HEAD").stdout.strip()
            for i, wait in enumerate((0, 2, 4, 8, 16)):
                time.sleep(wait)
                p = git(cfg, "push", timeout=180)
                if p.returncode == 0:
                    res["pushed"] = True
                    break
                res["error"] = (p.stderr.strip().splitlines() or ["push failed"])[-1][:200]
            else:
                res["pushed"] = False
                res["error"] = "push failed 5 times; commit left local: " + res["error"]
            if res.get("pushed"):
                res["error"] = ""
    m["pushed"] = res
    m["steps"]["push"] = now().isoformat(timespec="seconds")
    save_manifest(cfg, m)
    return m


# --------------------------------------------------------------------------
# Step: report

def _report_path(cfg: Cfg) -> Path:
    base = cfg.dest / f"HARVEST-{cfg.date}.md"
    if not base.exists():
        return base
    n = 2
    while (cfg.dest / f"HARVEST-{cfg.date}-{n}.md").exists():
        n += 1
    return cfg.dest / f"HARVEST-{cfg.date}-{n}.md"


def _size(b: int | None) -> str:
    if not b:
        return "-"
    if b >= 1 << 30:
        return f"{b / (1 << 30):.1f} GB"
    if b >= 1 << 20:
        return f"{b / (1 << 20):.0f} MB"
    return f"{max(1, b >> 10)} KB"


def _status_word(r: dict) -> str:
    s = (r.get("run_status") or "").strip()
    if s:
        return s
    return "finished" if r.get("has_return") else "no RETURN"


def build_report(cfg: Cfg, m: dict, project_sync: str | None = None) -> str:
    rows = m.get("rows", [])
    cands = m.get("candidates", [])
    targets = harvest_targets(m)
    hosts = m.get("hosts", {})

    head = [f"# Blitz harvest, {cfg.date}", ""]
    hs = ", ".join(f"{n} {'ok' if h.get('reachable') else 'unreachable'}{' (partial)' if h.get('partial') else ''}"
                   for n, h in hosts.items())
    head.append(f"Window: trailing {m.get('window_days')} days (since {m.get('window_since')}). "
                f"Hosts: {hs or 'not scanned'}. Mode: {m.get('mode')}.")
    head.append(project_sync or m.get("project_sync_line") or
                "Project sync not run; run `/blitz-harvest` from the Project to sync.")

    need = []
    for n, h in hosts.items():
        if h.get("reachable") is False:
            need.append(f"- {n} was unreachable ({h.get('error') or 'no answer'}). Wake it, or add the key: "
                        f"`ssh-copy-id {h.get('target')}` from the PC.")
        elif h.get("partial"):
            need.append(f"- {n} was scanned partially. Re-run `harvest.py scan --host {n} --slow`.")
    for r in targets:
        if r.get("control_only"):
            need.append(f"- {r['id']} on {r['host']} is over 2 GB ({_size(r.get('size_bytes'))}); only its control "
                        f"files were copied. Say whether to copy the rest.")
        if r.get("copy_status") == "error":
            need.append(f"- {r['id']} on {r['host']} failed to copy: {r.get('copy_error')}")
    for r in rows + cands:
        if r.get("sensitive") == "unknown" and (r.get("harvest") or r.get("decision") == "confirm"):
            need.append(f"- Confirm whether {r['id']} ({r.get('host')}) is restricted: {r.get('sensitive_reason')}.")
    if m.get("steps", {}).get("copy_refused"):
        need.append(f"- Copy refused: {m['steps']['copy_refused']}.")
    if m.get("steps", {}).get("sweep_error"):
        need.append(f"- The local evidence sweep stopped: {m['steps']['sweep_error']}")
    if m.get("credential_check"):
        need.append(f"- {len(m['credential_check'])} credential-like files found under blitz-returns; review and delete them.")
    pe = m.get("pushed", {}).get("error")
    if pe and "dry run" not in pe:
        need.append(f"- orchestrator-returns: {pe}")
    sec1 = ["## 1. Needed from Elliot", ""] + (need or ["Nothing."])

    miss = [r for r in rows if r.get("status") in ("not_found", "not_found_yet")]
    sec3 = ["## 3. Not found anywhere", ""]
    sec3 += [f"- {r['id']} ({r.get('source_doc')}): {r.get('why_missing')}" for r in miss[:15]] or ["Nothing missing."]
    if len(miss) > 15:
        sec3.append(f"- and {len(miss) - 15} more in harvest-manifest.json")

    disc = [c for c in cands if c.get("decision") == "harvest"]
    conf = [c for c in cands if c.get("decision") == "confirm"]
    low = [c for c in cands if str(c.get("decision", "")).startswith("ignored")]
    sec4 = ["## 4. Discovered runs and pattern-recognized pushes", ""]
    if disc:
        sec4.append("Harvested without a plan:")
        sec4 += [f"- {c['id']} on {c['host']}: score {c['push_score']} ({', '.join(c['push_signals']) or 'none'})"
                 for c in disc[:12]]
        if len(disc) > 12:
            sec4.append(f"- and {len(disc) - 12} more")
    if conf:
        sec4 += ["", "Pattern-recognized pushes to confirm (not copied; say \"harvest <name>\" to copy one):"]
        sec4 += [f"- {c['id']} on {c['host']} (`{c['path']}`): score {c['push_score']} ({', '.join(c['push_signals'])})"
                 for c in conf[:12]]
        if len(conf) > 12:
            sec4.append(f"- and {len(conf) - 12} more")
    if not disc and not conf:
        sec4.append("None.")
    if low:
        sec4 += ["", f"{len(low)} low-signal folders ignored (listed in harvest-manifest.json)."]

    # Section 5 is never shortened: every restricted item is named.
    sec5 = ["## 5. Excluded for privacy", ""]
    excl = [r for r in rows + cands if r.get("sensitive") in (True, "unknown")]
    for r in excl:
        if r.get("harvest") and r.get("host") == "pc":
            what = "stays on the PC; never synced to the Project"
        elif r.get("harvest") and r.get("copy_status") not in (None, "error"):
            what = "copied to the PC only; not synced to the Project"
        elif r.get("harvest"):
            what = "PC only; not synced to the Project"
        else:
            what = "not copied; never synced to the Project"
        sec5.append(f"- {r['id']} ({r.get('host') or r.get('host_hint') or 'host unknown'}): "
                    f"{r.get('sensitive_reason') or 'restricted'}; {what}.")
    if not excl:
        sec5.append("Nothing excluded.")

    sec6 = ["## 6. Possible other hosts", ""]
    ph = m.get("possible_hosts", [])
    for p in ph[:10]:
        if p.get("never_scan"):
            sec6.append(f"- {p['peer']} ({p['ip']}): Jjess's Mac mini, not a Claude host; not scanned. "
                        f"Evidence `{Path(p['file']).name}:{p['line']}`.")
        else:
            sec6.append(f"- {p['peer']} ({p['ip']}): `{Path(p['file']).name}:{p['line']}` \"{p['evidence']}\". "
                        f"To include it, add a hosts.conf line with `harvest_only=yes`.")
    if not ph:
        sec6.append("No evidence of runs on other Tailscale peers.")
    if m.get("tailscale_peers"):
        off = [p for p in m["tailscale_peers"] if not p.get("online")]
        sec6.append(f"Tailscale: {len(m['tailscale_peers'])} peers, {len(off)} offline.")

    sec7 = ["## 7. Pushed to orchestrator-returns", ""]
    pu = m.get("pushed", {})
    if pu.get("commit"):
        sec7.append(f"- Commit {pu['commit']}{'' if pu.get('pushed') else ' (local only, not pushed)'}: "
                    + ", ".join(f"`{f}`" for f in pu.get("files", [])[:12]))
    elif pu.get("error"):
        sec7.append(f"- {pu['error']}")
    else:
        sec7.append("- No new or changed RETURN.md.")
    if pu.get("fleet_pull"):
        sec7.append(f"- fleet.py pull: {pu['fleet_pull']}")

    # Section 2 takes whatever room is left under 150 lines.
    fixed = len(head) + sum(len(x) + 1 for x in (sec1, sec3, sec4, sec5, sec6, sec7)) + 4
    room = max(8, 150 - fixed)
    sec2 = ["## 2. Found and copied", ""]
    if targets:
        sec2 += ["| Run | Host | Status | Size | Copy | Destination |", "|---|---|---|---|---|---|"]
        ordered = sorted(targets, key=lambda x: (x["host"], x["id"]))
        shown = ordered if len(ordered) <= room - 2 else ordered[:room - 3]
        for r in shown:
            dest = r.get("dest", "")
            dest = dest.replace(str(cfg.dest), "blitz-returns") if dest else "-"
            sec2.append(f"| {r['id']} | {r['host']} | {_status_word(r)} | {_size(r.get('size_bytes'))} | "
                        f"{r.get('copy_status', 'not copied yet')} | `{dest}` |")
        if len(shown) < len(ordered):
            sec2.append(f"| and {len(ordered) - len(shown)} more | | | | | see harvest-manifest.json |")
    else:
        sec2.append("Nothing found to copy.")
    pages = m.get("pc_pages") or []
    if pages:
        sec2.append(f"\nBlitz pages in the PC's Downloads (left in place): {len(pages)}.")

    out = head + [""]
    for sec in (sec1, sec2, sec3, sec4, sec5, sec6, sec7):
        out += sec + [""]
    return "\n".join(out).rstrip() + "\n"


def cmd_report(cfg: Cfg, m: dict | None = None, project_sync: str | None = None) -> Path:
    m = m or load_manifest(cfg)
    text = build_report(cfg, m, project_sync)
    p = _report_path(cfg)
    cfg.dest.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    readme = cfg.dest / "README.md"
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    hist = cfg.dest / "_history"
    hist.mkdir(exist_ok=True)
    shutil.copy2(cfg.manifest_path, hist / (p.stem.replace("HARVEST", "harvest-manifest") + ".json"))
    m["report"] = str(p)
    m["steps"]["report"] = now().isoformat(timespec="seconds")
    save_manifest(cfg, m)
    print(p)
    return p


README_TEXT = """# blitz-returns

Written by the blitz-harvest skill (`tools\\harvest\\harvest.py`).

- `HARVEST-<date>.md`: one report per harvest (same-day re-runs get `-2`, `-3`).
- `harvest-manifest.json`: every planned, found, missing and pattern-recognized run of the latest harvest; `_history\\` keeps each harvest's manifest.
- `<host>\\<run>\\`: the run folder as copied from that host. A `-v2`, `-v3` sibling is a later harvest of the same run after it changed; earlier copies are never overwritten.
- `pc\\<run>\\RUNS-HERE.md`: runs that live on this PC are recorded by reference, not duplicated.
- `.harvest.json` in each copy: source path, harvest date, fingerprint, sensitivity, what was skipped.
- `_scan\\`: raw finder output per host (PC only; never synced to the Project).

Never copied: credentials, tokens, keychains, `.env` files, git history, dependencies and caches.
Restricted runs stay here and in orchestrator-returns; they are never synced to the Project.
"""


# --------------------------------------------------------------------------
# Cloud path: the allowed subset for the Project

def cmd_sync_zip(cfg: Cfg, have: set[str]) -> Path:
    """Zip the report, the manifest and every harvested folder whose row is
    sensitive: false (unknown counts as restricted), up to 200 MB. Folders that
    do not fit go in with their control files only."""
    m = load_manifest(cfg)
    z = cfg.dest / f"sync-{cfg.date}.zip"
    allowed = [r for r in harvest_targets(m) if r.get("sensitive") is False and r.get("dest")
               and r["host"] != "pc" and r.get("copy_status") in ("copied", "copied_control_only", "unchanged")]
    allowed = [r for r in allowed if f"{r['host']}/{Path(r['dest']).name}" not in have]
    allowed.sort(key=lambda r: r.get("size_bytes") or 0)
    budget = PROJECT_SYNC_CAP_BYTES
    listing = []
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        rep = m.get("report")
        if rep and Path(rep).exists():
            zf.write(rep, Path(rep).name)
            budget -= Path(rep).stat().st_size
        zf.writestr("harvest-manifest.json", json.dumps(project_safe_manifest(m), indent=1))
        for r in allowed:
            d = Path(r["dest"])
            files = [p for p in d.rglob("*") if p.is_file()]
            size = sum(p.stat().st_size for p in files)
            mode = "full"
            if size > budget:
                files = [p for p in files if p.name in CONTROL_FILES or p.name == ".harvest.json"]
                size = sum(p.stat().st_size for p in files)
                mode = "control files only"
            if size > budget:
                listing.append({"run": f"{r['host']}/{d.name}", "mode": "pointer only"})
                continue
            for p in files:
                if _bad_name(p.name):
                    continue
                zf.write(p, f"{r['host']}/{d.name}/{p.relative_to(d).as_posix()}")
            budget -= size
            listing.append({"run": f"{r['host']}/{d.name}", "mode": mode, "bytes": size})
        zf.writestr("SYNC-INDEX.json", json.dumps(listing, indent=1))
    m["project_sync"] = {"zip": str(z), "runs": listing}
    save_manifest(cfg, m)
    print(z)
    return z


def project_safe_manifest(m: dict) -> dict:
    """The manifest as the Project may hold it: no RETURN excerpts, no raw
    session paths, and restricted rows reduced to name, host and reason."""
    def slim(r: dict) -> dict:
        if r.get("sensitive") in (True, "unknown"):
            return {k: r.get(k) for k in ("id", "host", "host_hint", "status", "sensitive", "sensitive_reason",
                                          "copy_status", "decision") if r.get(k) is not None}
        return {k: v for k, v in r.items() if k not in ("size",)}
    out = {k: v for k, v in m.items() if k not in ("rows", "candidates", "stray_sessions", "possible_hosts",
                                                    "returns_index", "host_extras")}
    out["rows"] = [slim(r) for r in m.get("rows", [])]
    out["candidates"] = [slim(c) for c in m.get("candidates", []) if c.get("decision") != "ignored (low signal)"]
    out["possible_hosts"] = [{"peer": p["peer"], "ip": p["ip"], "never_scan": p.get("never_scan")}
                             for p in m.get("possible_hosts", [])]
    return out


# --------------------------------------------------------------------------
# CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harvest.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="master-orchestrator folder (default: two levels above this script)")
    ap.add_argument("--dest", help="destination (default: <root>\\blitz-returns)")
    ap.add_argument("--returns", help="orchestrator-returns clone (default: <root>\\returns)")
    ap.add_argument("--projects", help="folder searched for PC runs (default: parent of <root>)")
    ap.add_argument("--days", type=int, default=8, help="trailing window in days (default 8; never under 7)")
    ap.add_argument("--date", help="harvest date label (default: today, UTC)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan", help="build the manifest from plans, prompts, fleet state and returns")
    p_scan = sub.add_parser("scan", help="find each run on every host (read-only)")
    p_copy = sub.add_parser("copy", help="copy found runs to the PC")
    p_push = sub.add_parser("push", help="push new RETURN.md files to orchestrator-returns")
    sub.add_parser("report", help="write HARVEST-<date>.md")
    p_all = sub.add_parser("all", help="plan, scan, copy, push, report")
    p_rows = sub.add_parser("rows", help="print plan rows from documents as JSON (for the cloud path)")
    p_zip = sub.add_parser("sync-zip", help="zip the non-restricted subset for the Project")
    sub.add_parser("selftest", help="run the built-in tests")
    for p in (p_plan, p_all, p_rows):
        p.add_argument("--docs", action="append", help="extra folder holding blitz/, intake/, prompts/ (repeatable)")
        p.add_argument("--extra-rows", action="append", help="JSON list of rows to add (from the cloud thread)")
    for p in (p_scan, p_all):
        p.add_argument("--host", choices=["pc", "agent2", "macbook", "agent1"])
        p.add_argument("--slow", action="store_true", help="300 s finder budget instead of 60 s")
    for p in (p_copy, p_push, p_all):
        p.add_argument("--dry-run", action="store_true")
    for p in (p_plan, p_all):
        p.add_argument("--from-project", action="store_true", help="the run was started from the Project (cloud path)")
    for p in (p_push, p_all):
        p.add_argument("--no-fleet-pull", action="store_true", help="skip `fleet.py pull` before pushing")
    p_zip.add_argument("--have", default="", help="comma list of host/run already in the Project")
    p_zip.add_argument("--have-file", help="file with one host/run per line already in the Project")
    p_rows.add_argument("--out", help="write rows here instead of stdout")
    args = ap.parse_args(argv)
    if args.days < 7:
        ap.error("--days must be at least 7 (the window always covers a full weekly cycle)")
    for k in ("docs", "extra_rows", "host", "slow", "dry_run", "no_fleet_pull", "from_project"):
        if not hasattr(args, k):
            setattr(args, k, None)
    cfg = Cfg(args)

    if args.cmd == "selftest":
        import unittest
        sys.path.insert(0, str(HERE))
        suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_harvest.py")
        return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1
    if args.cmd == "rows":
        rows: list[dict] = []
        for p in doc_files(cfg):
            if doc_in_window(p, cfg.since):
                rows = merge_rows(rows, parse_doc(p))
        text = json.dumps(rows, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    if args.cmd == "plan":
        cmd_plan(cfg)
    elif args.cmd == "scan":
        cmd_scan(cfg)
    elif args.cmd == "copy":
        cmd_copy(cfg)
    elif args.cmd == "push":
        cmd_push(cfg)
    elif args.cmd == "report":
        cmd_report(cfg)
    elif args.cmd == "sync-zip":
        have = {h.strip() for h in args.have.split(",") if h.strip()}
        if args.have_file:
            have |= {ln.strip() for ln in Path(args.have_file).read_text(encoding="utf-8").splitlines() if ln.strip()}
        cmd_sync_zip(cfg, have)
    elif args.cmd == "all":
        m = cmd_plan(cfg)
        m = cmd_scan(cfg, m)
        m = cmd_copy(cfg, m)
        m = cmd_push(cfg, m)
        cmd_report(cfg, m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
