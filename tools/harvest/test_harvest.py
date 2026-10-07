"""Tests for blitz-harvest. Run: python harvest.py selftest

Builds a throwaway PC folder tree, two fake Mac home folders and an
orchestrator-returns clone with a local bare remote, and drives harvest.py end
to end with SSH replaced by a local shim, so the real copy, credential and
idempotency rules are exercised without touching any real host.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harvest as H  # noqa: E402

# The end-to-end test simulates Mac hosts with a local POSIX shell, so it runs
# on Linux or macOS; on the Windows PC the rule tests run and the live
# acceptance run (SKILL.md) covers the rest.
HAS_SH = os.name != "nt" and shutil.which("sh") is not None and shutil.which("git") is not None

FAKE_SSH = textwrap.dedent("""
    import json, os, subprocess, sys
    target, cmd = sys.argv[1], sys.argv[-1]
    homes = json.loads(os.environ["FAKE_HOMES"])
    if target not in homes:
        sys.stderr.write("ssh: connect to host %s port 22: Operation timed out\\n" % target)
        sys.exit(255)
    env = dict(os.environ, HOME=homes[target])
    sys.exit(subprocess.call(["sh", "-c", cmd], env=env, cwd=homes[target]))
""")


def w(p: Path, text: str = "x\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Rules(unittest.TestCase):
    def test_never_copy_list_is_enforced(self):
        bad = [".env", ".env.local", "token", "claude-token.txt", "SECRET.md", "id_ed25519", "id_rsa.pub",
               "server.pem", "login.keychain-db", ".credentials.json", "auth.json", ".npmrc", "x.key"]
        for name in bad:
            ok, why = H.member_allowed(PurePosixPath("run/sub") / name, 10)
            self.assertFalse(ok, name)
        for path in ("a/node_modules/x/i.js", "a/.git/objects/ab/cd", "a/.git/logs/HEAD", "a/__pycache__/m.pyc",
                     "a/.ssh/config", "../escape", "a/._resource"):
            self.assertFalse(H.member_allowed(PurePosixPath(path), 10)[0], path)
        for path in ("RETURN.md", "loop/state.json", "repo/.git/HEAD", "repo/.git/config", "out/post-01.md"):
            self.assertTrue(H.member_allowed(PurePosixPath(path), 10)[0], path)
        self.assertFalse(H.member_allowed(PurePosixPath("logs/run.log"), H.BIG_LOG_BYTES + 1)[0])
        self.assertTrue(H.member_allowed(PurePosixPath("logs/run.log"), 1000)[0])

    def test_never_copy_list_matches_plan(self):
        for pat in ("*token*", "*secret*", "*.pem", "id_*", ".env"):
            self.assertIn(pat, H.NEVER_COPY)

    def test_hours_before_reset(self):
        # Tuesday 2026-10-06 12:00 UTC is 48 h before CyberNova's Thursday 12:00 reset.
        ts = H.dt.datetime(2026, 10, 6, 12, 0, tzinfo=H.UTC).timestamp()
        self.assertAlmostEqual(H._hours_before_reset(ts, *H.RESETS["cybernova"]), 48.0)

    def test_days_floor(self):
        with self.assertRaises(SystemExit):
            H.main(["--days", "6", "plan"])


DOC_TONIGHT = """# Tonight on agent2 with the Gmail account

| # | Run | Folder to open |
|---|---|---|
| 1 | L7 | `/Users/agent2/orchestrator/sprints/L7-nasarai-devpush-2026-09-30` |

## 1. L7, paste

```
ultracode /goal SPR is /Users/agent2/orchestrator/sprints/L7-nasarai-devpush-2026-09-30. Fable 5.1 at xhigh.
```

## 3. First drafts of all the posts, paste

1. TARGET. Account luchansky.elliot.a@gmail.com (Claude Max 20x), agent2 Mac mini, pasted by Elliot (Path B).
6. CONTEXT. Working folder: create /Users/agent2/orchestrator/sprints/writing-home-drafts-2026-10-05/ and RETURN.md at /Users/agent2/orchestrator/returns/writing-home-drafts-2026-10-05/RETURN.md

## 7. Outreach

Working folder: /Users/agent2/orchestrator/sprints/outreach-2clients-2026-10-05/
"""

DOC_MACBOOK = """/goal ROOT is /Users/luchanskyelliot/Downloads/colombia-project-playa-2026-10-01 (if that folder does not exist,
ROOT is the current working folder). Opus 5.5 high effort.
"""

DOC_RUNLIST = """# Loop intake: run list

- Sprint id `nasarai-q4-revenue-L1-2026-09-30`. Zip: `C:\\Users\\Owner\\Downloads\\nasarai-marketing-q4-revenue-sprint-2026-09-30.zip`.
- Run on the MacBook: ~/Downloads/nasarai-marketing-q4-revenue-sprint-2026-09-30
"""


@unittest.skipUnless(HAS_SH, "needs sh and git")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="harvest-test-"))
        t = self.tmp
        self.projects = t / "Projects"
        self.root = self.projects / "master-orchestrator"
        w(self.root / "blitz" / "agent2-gmail-tonight-2026-10-05.md", DOC_TONIGHT)
        w(self.root / "macbook-2026-10-01" / "RUN-2-COLOMBIA-PROMPT.txt", DOC_MACBOOK)
        w(self.root / "intake" / "run-list-2026-09-30.md", DOC_RUNLIST)
        w(self.root / "tools" / "hosts.conf", "# name target account\nagent2 agent2@agents-mac-mini-1 cybernova\n")
        w(self.root / "state" / "dispatch-log.jsonl",
          json.dumps({"id": "smoke-001", "host": "agent2", "prompt": "smoke-001.md",
                      "ts": H.now().isoformat()}) + "\n")
        # PC runs: one push, one restricted package, one personal folder.
        l6 = self.projects / "cureis-ai-strategy" / "work" / "claude-code" / "loops-L6"
        w(l6 / "state.json", '{"status": "done"}')
        w(l6 / "PROGRESS.md", "# L6 Jev\nLead Opus 5.5 at high effort. /goal\n")
        w(l6 / "RETURN.md", "# RETURN L6\n")
        w(self.projects / "apeira-lawsuit-loops" / "loops" / "L1" / "SPRINT.md", "# Apeira L1\nOpus 5.5 xhigh\n")
        w(self.projects / ".personal-finance" / "state.json", "{}")
        # The returns clone, with a bare remote.
        remote = t / "returns-remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.returns = self.root / "returns"
        subprocess.run(["git", "clone", "-q", str(remote), str(self.returns)], check=True, capture_output=True)
        git(self.returns, "config", "user.email", "t@example.com")
        git(self.returns, "config", "user.name", "t")
        w(self.returns / "README.md", "returns\n")
        w(self.returns / "2026-09-28" / "smoke-001" / "RETURN.md", "# smoke\n")
        git(self.returns, "add", "-A")
        git(self.returns, "commit", "-q", "-m", "init")
        git(self.returns, "push", "-q", "origin", "HEAD")
        # PC session metadata.
        self.pchome = t / "pchome"
        sess = self.pchome / ".claude" / "projects" / "l6"
        w(sess / "s.jsonl", "\n".join([
            json.dumps({"cwd": str(l6), "timestamp": "2026-09-30T20:00:00Z", "message": {"content": "/goal ..."}}),
            json.dumps({"message": {"model": "claude-opus-5-5", "content": [{"type": "tool_use", "name": "Agent"}]},
                        "timestamp": "2026-09-30T23:30:00Z"}),
        ]) + "\n")
        # Fake Macs.
        self.a2 = t / "mac-agent2"
        self._agent2(self.a2)
        self.mb = t / "mac-macbook"
        self._macbook(self.mb)
        self.a1 = t / "mac-agent1"
        self.a1.mkdir()
        shim = w(t / "fake_ssh.py", FAKE_SSH)
        self._orig_ssh = H.ssh_base
        H.ssh_base = lambda target, connect_timeout=8: [sys.executable, str(shim), target]
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.pchome)
        os.environ["USERPROFILE"] = str(self.pchome)
        self.set_homes(agent1=False)

    def tearDown(self):
        H.ssh_base = self._orig_ssh
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_homes(self, agent1: bool):
        homes = {"agent2@agents-mac-mini-1": str(self.a2), "luchanskyelliot@100.116.248.10": str(self.mb)}
        if agent1:
            homes["agent1@100.82.254.11"] = str(self.a1)
        os.environ["FAKE_HOMES"] = json.dumps(homes)

    def _agent2(self, h: Path):
        l7 = h / "orchestrator" / "sprints" / "L7-nasarai-devpush-2026-09-30"
        w(l7 / "loop" / "state.json", '{"status": "DONE"}')
        w(l7 / "loop" / "PROGRESS.md", "# Progress\nFable 5.1 xhigh ultracode\n")
        w(l7 / "RETURN.md", "# RETURN L7\n## Produced\n")
        w(l7 / "nasarai-platform" / ".env", "SECRET=1\n")
        w(l7 / "nasarai-platform" / ".git" / "HEAD", "ref: refs/heads/x\n")
        w(l7 / "nasarai-platform" / ".git" / "config", "[core]\n")
        w(l7 / "nasarai-platform" / ".git" / "objects" / "ab" / "cd", "obj")
        w(l7 / "nasarai-platform" / "node_modules" / "x" / "i.js", "mod")
        w(l7 / "nasarai-platform" / "src" / "index.ts", "export {}\n")
        d = h / "orchestrator" / "sprints" / "writing-home-drafts-2026-10-05"
        w(d / "state.json", '{"phase": "drafting"}')
        w(d / "PROGRESS.md", "# P\n")
        w(d / "out" / "INDEX.md", "# Index\n")
        w(h / "orchestrator" / "returns" / "writing-home-drafts-2026-10-05" / "RETURN.md", "# RETURN drafts\n")
        w(h / "orchestrator" / "sprints" / "smoke-001" / "RETURN.md", "# smoke\n")
        w(h / "orchestrator-prompts" / "agent2-outreach-2clients-2026-10-05.md", "prompt\n")
        w(h / ".config" / "orchestrator" / "token", "TOKENVALUE\n")
        w(h / ".config" / "orchestrator" / "account", "cybernova\n")
        fh = h / "fleet-harness"
        w(fh / "state.json", '{"status": "paused"}')
        w(fh / "PROGRESS.md", "# fleet harness v0.1\n")
        w(h / ".claude" / "projects" / "fh" / "s.jsonl", "\n".join([
            json.dumps({"cwd": str(fh), "timestamp": "2026-10-01T10:00:00Z"}),
            json.dumps({"message": {"model": "claude-opus-5-5"}, "timestamp": "2026-10-01T14:30:00Z"}),
        ]) + "\n")
        w(h / "Documents" / "personal" / "notes.txt", "private\n")

    def _macbook(self, h: Path):
        c = h / "Downloads" / "colombia-project-playa-2026-10-01"
        w(c / "MANIFEST.md", "# Colombia Project Playa\n")
        w(c / "loops" / "C1" / "state.json", '{"status": "done"}')
        w(c / "work" / "C1" / "README.md", "# C1\n")
        q = h / "Downloads" / "nasarai-marketing-q4-revenue-sprint-2026-09-30"
        w(q / "MANIFEST.md", "# Nasarai Q4 revenue sprint\n")
        w(q / "loops" / "L1" / "state.json", '{"status": "done"}')
        w(q / "work" / "L1" / "README.md", "# first three days\n")

    def run_all(self, *extra: str) -> dict:
        args = ["--root", str(self.root), "--projects", str(self.projects), "--days", "8", "all",
                "--no-fleet-pull", *extra]
        self.assertEqual(H.main(args), 0)
        return json.loads((self.root / "blitz-returns" / "harvest-manifest.json").read_text())

    def row(self, m: dict, rid: str) -> dict:
        for r in m["rows"] + m["candidates"]:
            if r["id"] == rid:
                return r
        self.fail(f"no row {rid}: {[r['id'] for r in m['rows'] + m['candidates']]}")

    def test_full_harvest_then_idempotent(self):
        m = self.run_all()
        dest = self.root / "blitz-returns"
        l7 = self.row(m, "L7-nasarai-devpush-2026-09-30")
        self.assertEqual((l7["status"], l7["host"], l7["copy_status"]), ("found", "agent2", "copied"))
        got = dest / "agent2" / "L7-nasarai-devpush-2026-09-30"
        self.assertTrue((got / "RETURN.md").exists())
        self.assertTrue((got / "nasarai-platform" / ".git" / "HEAD").exists())
        self.assertTrue((got / "nasarai-platform" / "src" / "index.ts").exists())
        self.assertFalse((got / "nasarai-platform" / ".env").exists())
        self.assertFalse((got / "nasarai-platform" / ".git" / "objects").exists())
        self.assertFalse((got / "nasarai-platform" / "node_modules").exists())
        self.assertEqual(H.credential_check(dest), [])
        for p in dest.rglob("*"):
            if p.is_file():
                self.assertNotIn("TOKENVALUE", p.read_text(errors="replace"), str(p))
        # Planned but never launched: staged prompt, no folder.
        out = self.row(m, "outreach-2clients-2026-10-05")
        self.assertEqual(out["status"], "not_found")
        # MacBook package found and restricted by topic.
        col = self.row(m, "colombia-project-playa-2026-10-01")
        self.assertEqual((col["host"], col["sensitive"]), ("macbook", True))
        self.assertTrue((dest / "macbook" / "colombia-project-playa-2026-10-01" / "MANIFEST.md").exists())
        q4 = self.row(m, "nasarai-marketing-q4-revenue-sprint-2026-09-30")
        self.assertEqual((q4["host"], q4["sensitive"]), ("macbook", False))
        # Pattern pass: the fleet harness was never in a plan.
        fh = self.row(m, "fleet-harness")
        self.assertEqual(fh["host"], "agent2")
        self.assertGreaterEqual(fh["push_score"], 4, fh["push_signals"])
        self.assertTrue(fh["harvest"])
        # PC push recorded by reference; restricted PC package listed, personal folder never seen.
        l6 = self.row(m, "loops-L6")
        self.assertEqual(l6["host"], "pc")
        self.assertIn("model", l6["push_signals"])
        ids = {r["id"] for r in m["rows"] + m["candidates"]}
        self.assertNotIn(".personal-finance", ids)
        self.assertNotIn("personal", ids)
        ap = self.row(m, "apeira-lawsuit-loops")
        self.assertEqual(ap["sensitive"], True)
        # Smoke run from fleet state: matched on agent2, return already in the repo.
        sm = self.row(m, "smoke-001")
        self.assertEqual(sm["status"], "found")
        self.assertEqual(sm.get("return_push"), "already in orchestrator-returns")
        # Returns copy folded into its sprint folder.
        self.assertFalse(any(c["path"].endswith("returns/writing-home-drafts-2026-10-05") and c.get("harvest")
                             for c in m["candidates"]))
        # Push: new RETURN.md files reached the bare remote.
        self.assertTrue(m["pushed"].get("pushed"), m["pushed"])
        log = subprocess.run(["git", "--git-dir", str(self.tmp / "returns-remote.git"), "ls-tree", "-r",
                              "--name-only", "HEAD"], capture_output=True, text=True).stdout
        self.assertIn("L7-nasarai-devpush-2026-09-30/RETURN.md", log)
        # Report: sections, privacy list, unreachable agent1, under 150 lines.
        rep = Path(m["report"]).read_text()
        self.assertLessEqual(len(rep.splitlines()), 150)
        sec5 = rep.split("## 5. Excluded for privacy")[1].split("## 6.")[0]
        self.assertIn("colombia-project-playa-2026-10-01", sec5)
        self.assertIn("apeira-lawsuit-loops", sec5)
        self.assertIn("agent1 was unreachable", rep)
        self.assertNotIn("TOKENVALUE", rep)

        # Second run, all hosts reachable: nothing copied, nothing needed.
        self.set_homes(agent1=True)
        commits = git(self.returns, "rev-list", "--count", "HEAD")
        m2 = self.run_all()
        copied = [r for r in H.harvest_targets(m2) if r.get("copy_status") not in ("unchanged",)]
        self.assertEqual(copied, [], [(r["id"], r.get("copy_status")) for r in copied])
        self.assertEqual(git(self.returns, "rev-list", "--count", "HEAD"), commits)
        rep2 = Path(m2["report"]).read_text()
        self.assertTrue(Path(m2["report"]).name.endswith("-2.md"))
        self.assertIn("## 1. Needed from Elliot\n\nNothing.", rep2)

        # A changed run becomes a -v2 sibling; the first copy survives.
        time.sleep(1.1)
        w(self.a2 / "orchestrator" / "sprints" / "L7-nasarai-devpush-2026-09-30" / "RETURN.md", "# RETURN L7 v2\n")
        m3 = self.run_all()
        l7b = self.row(m3, "L7-nasarai-devpush-2026-09-30")
        self.assertEqual(l7b["copy_status"], "copied")
        self.assertTrue(l7b["dest"].endswith("L7-nasarai-devpush-2026-09-30-v2"))
        self.assertTrue((got / "RETURN.md").read_text().startswith("# RETURN L7\n"))

        # The Project subset: no restricted run, no credential, manifest slimmed.
        z = H.main(["--root", str(self.root), "--projects", str(self.projects), "sync-zip"])
        self.assertEqual(z, 0)
        zp = next((self.root / "blitz-returns").glob("sync-*.zip"))
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            man = json.loads(zf.read("harvest-manifest.json"))
        self.assertTrue(any(n.startswith("agent2/L7-nasarai-devpush-2026-09-30") for n in names))
        self.assertFalse(any("colombia" in n for n in names), names)
        self.assertFalse(any(n.startswith("pc/") for n in names))
        self.assertFalse(any(H._bad_name(Path(n).name) for n in names))
        col_slim = [r for r in man["rows"] + man["candidates"] if r["id"].startswith("colombia")][0]
        self.assertNotIn("path", col_slim)

    def test_confirm_and_drop(self):
        m = self.run_all()
        self.assertEqual(self.row(m, "loops-L6")["decision"], "confirm")
        base = ["--root", str(self.root), "--projects", str(self.projects)]
        self.assertEqual(H.main(base + ["confirm", "pc/loops-L6"]), 0)
        self.assertEqual(H.main(base + ["drop", "apeira-lawsuit-loops"]), 0)
        m2 = self.run_all()
        l6 = self.row(m2, "loops-L6")
        self.assertEqual((l6["decision"], l6["copy_status"]), ("harvest (confirmed by Elliot)", "by_reference"))
        self.assertTrue((self.root / "blitz-returns" / "pc" / "loops-L6" / "RUNS-HERE.md").exists())
        ap = self.row(m2, "apeira-lawsuit-loops")
        self.assertEqual(ap["decision"], "dropped by Elliot")
        rep = Path(m2["report"]).read_text()
        self.assertIn("apeira-lawsuit-loops", rep.split("## 5. Excluded for privacy")[1])  # still named
        self.assertNotIn("apeira-lawsuit-loops on pc", rep.split("## 4.")[1].split("## 5.")[0])

    def test_dry_run_copies_nothing(self):
        m = self.run_all("--dry-run")
        self.assertTrue(all(r.get("copy_status") in ("dry-run", "error") or r["host"] == "pc"
                            for r in H.harvest_targets(m)))
        self.assertFalse((self.root / "blitz-returns" / "agent2").exists())
        self.assertEqual(git(self.returns, "rev-list", "--count", "HEAD"), "1")


if __name__ == "__main__":
    unittest.main()
