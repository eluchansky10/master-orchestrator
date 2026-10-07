#!/usr/bin/env python3
"""Tests for blitz-harvest. Runs anywhere with Python 3.10+, sh and tar (Linux, macOS, or Git Bash on Windows).

    python test_harvest.py            (unittest; no network, no real hosts)

Fakes: a fleet.py with the pre-patch load_hosts() (so patch_fleet.py is tested too), and an ssh stand-in that
runs the remote command locally with HOME set to a fixture folder per host. A host missing from the map
behaves like an unreachable Mac.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harvest  # noqa: E402
import patch_fleet  # noqa: E402

FAKE_SSH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
while args and args[0] == "-o":
    args = args[2:]
target, remote = args[0], " ".join(args[1:])
homes = json.loads(os.environ["FAKE_HOMES"])
if target not in homes:
    sys.stderr.write("ssh: connect to host %s port 22: Connection timed out\n" % target)
    sys.exit(255)
env = dict(os.environ, HOME=homes[target])
if remote == "hostname":
    print(target.split("@")[-1]); sys.exit(0)
sys.exit(subprocess.call(["sh", "-c", remote], env=env, cwd=homes[target]))
'''

FAKE_FLEET = '''
import os, sys
from pathlib import Path
HOSTS_FILE = Path(__file__).with_name("hosts.conf")
SSH = os.environ["FAKE_SSH"]
NO_WINDOW = 0
PULLED = []
''' + patch_fleet.OLD + '''

def pull():
    PULLED.append(1)
    print("no new returns")
'''


def write(p, text="x\n", mtime=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def jsonl(*objs):
    return "\n".join(json.dumps(o) for o in objs) + "\n"


class Fixture:
    """A fake C:\\Projects\\master-orchestrator plus fake homes for agent2, agent1 and the MacBook."""

    def __init__(self, macbook_up=False):
        self.tmp = Path(tempfile.mkdtemp(prefix="harvest-test-"))
        self.projects = self.tmp / "Projects"
        self.root = self.projects / "master-orchestrator"
        self.tools = self.root / "tools"
        self.tools.mkdir(parents=True)
        ssh = write(self.tmp / "fake-ssh", FAKE_SSH)
        ssh.chmod(0o755)
        self.ssh = ssh
        os.environ["FAKE_SSH"] = str(ssh)
        write(self.tools / "fleet.py", FAKE_FLEET)
        write(self.tools / "hosts.conf", "\n".join([
            "# name | ssh | account | key | last day | sensitive | max",
            "agent2|agent2@agents-mac-mini-1|elliot@cybernovaequity.com|token-8b88e8c09b14|-|yes|2",
            "macbook|luchanskyelliot@100.116.248.10|elliot@nasarai.com|PENDING|2026-10-01|no|2", ""]))
        self.homes = {"agent2@agents-mac-mini-1": str(self.tmp / "agent2"), "agent1@100.82.254.11": str(self.tmp / "agent1")}
        if macbook_up:
            self.homes["luchanskyelliot@100.116.248.10"] = str(self.tmp / "macbook")
        self.activate()
        self.make_agent2()
        self.make_agent1()
        self.make_macbook()
        self.make_returns()
        self.make_seed()
        self.make_pc()

    def make_agent2(self):
        h = self.tmp / "agent2"
        s = h / "orchestrator" / "sprints"
        l7 = s / "L7-nasarai-devpush-2026-09-30"
        write(l7 / "status", "DONE\n")
        write(l7 / "config.env", "RESUMABLE=1\nMAX_HOURS=6\n")
        write(l7 / "started_utc", "2026-10-05T03:50:00Z\n")
        write(l7 / "ended_utc", "2026-10-05T06:10:00Z\n")
        write(l7 / "RETURN.md", "# RETURN: L7-nasarai-devpush-2026-09-30\n1. Sprint id: L7-nasarai-devpush-2026-09-30\n"
                                "10. Status: DONE\n## Produced\nsite build\n")
        write(l7 / "loop" / "state.json", '{"sprint": "L7-nasarai-devpush-2026-09-30"}')
        write(l7 / "loop" / "PROGRESS.md", "# L7 progress\n")
        write(l7 / "prompt.md", "ultracode /goal ... Fable 5.1 at xhigh\n")
        write(l7 / "nasarai-platform" / "src" / "index.ts", "export {}\n")
        write(l7 / "nasarai-platform" / "node_modules" / "react" / "index.js", "big\n")
        write(l7 / "nasarai-platform" / ".git" / "HEAD", "ref: refs/heads/claude/devpush\n")
        write(l7 / "nasarai-platform" / ".git" / "config", "[core]\n")
        write(l7 / "nasarai-platform" / ".git" / "objects" / "ab" / "cdef", "blob\n")
        write(l7 / "nasarai-platform" / "packages" / "ui" / "tokens.css", ":root{}\n")
        write(l7 / ".env", "SECRET=1\n")
        write(l7 / "claude-token", "sk-ant-oat01-FAKE\n")
        write(l7 / "design" / ".DS_Store", "junk\n")
        write(l7 / "design" / "README.md", "# Design variations\n")
        d = s / "writing-home-drafts-2026-10-05"
        write(d / "PROGRESS.md", "# writing-home-drafts-2026-10-05\n")
        write(d / "state.json", '{"posts": 66}')
        write(d / "out" / "INDEX.md", "# drafts\n")
        lw = s / "lawsuit-damages-L9-2026-10-01"
        write(lw / "config.env", "SENSITIVE=1\n")
        write(lw / "PROGRESS.md", "# Damages ledger\n")
        write(lw / "status", "NO_RETURN\n")
        old = s / "test-001"
        write(old / "RETURN.md", "# RETURN: test-001\n", mtime=time.time() - 40 * 86400)
        os.utime(old, (time.time() - 40 * 86400,) * 2)
        write(h / "orchestrator" / "returns" / "writing-home-drafts-2026-10-05" / "RETURN.md",
              "# RETURN: writing-home-drafts-2026-10-05\n1. Sprint id: writing-home-drafts-2026-10-05\n")
        write(h / "orchestrator-prompts" / "outreach-2clients-2026-10-05.md", "prompt\n")
        write(h / ".config" / "orchestrator" / "token", "NEVER-READ-ME\n")
        fh = h / "fleet-harness"
        write(fh / "PROGRESS.md", "# Fleet harness\n")
        write(fh / ".git" / "HEAD", "ref: refs/heads/main\n")
        sess = h / ".claude" / "projects" / "-Users-agent2-fleet-harness"
        write(sess / "abc.jsonl", jsonl(
            {"type": "user", "cwd": str(fh), "version": "2.1.284", "timestamp": "2026-10-01T12:05:00Z",
             "message": {"content": "<command-name>/goal</command-name> BODY-SHOULD-NOT-LEAK"}},
            {"type": "assistant", "cwd": str(fh), "timestamp": "2026-10-01T12:06:00Z",
             "message": {"model": "claude-opus-5-5", "content": "BODY-SHOULD-NOT-LEAK"}},
            {"type": "assistant", "timestamp": "2026-10-01T16:30:00Z", "message": {"model": "claude-opus-5-5"}}))
        write(sess / "abc" / "subagents" / "a1.jsonl", "{}\n")
        write(sess / "abc" / "subagents" / "a2.jsonl", "{}\n")
        write(h / "Documents" / "notes" / "PROGRESS.md", "# personal notes\n")

    def make_agent1(self):
        write(self.tmp / "agent1" / ".zshrc", "# nothing here\n")

    def make_macbook(self):
        h = self.tmp / "macbook"
        q4 = h / "Downloads" / "nasarai-marketing-q4-revenue-sprint-2026-09-30"
        write(q4 / "MANIFEST.md", "# MANIFEST\n## Sensitivity\nOverall: normal. No client-confidential material.\n")
        write(q4 / "loops" / "L1" / "state.json", '{"loop": "L1"}')
        write(q4 / "loops" / "L1" / "RETURN.md", "# RETURN: nasarai-q4-revenue-L1-2026-09-30\n"
                                                 "1. Sprint id: nasarai-q4-revenue-L1-2026-09-30\nStatus: DONE\n## Clients we target\n")
        write(q4 / "work" / "L1" / "README.md", "# kit\n")
        pl = h / "Downloads" / "colombia-project-playa-2026-10-01"
        write(pl / "README.md", "# Project Playa\n## RULES\n4. Sensitive project. Claude models only.\n")
        write(pl / "loops" / "C1" / "state.json", '{"phase": 6}')
        write(pl / "work" / "C1" / "model.py", "print(1)\n")

    def make_returns(self):
        origin = self.tmp / "returns-origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        rc = self.root / "returns"
        subprocess.run(["git", "clone", "-q", str(origin), str(rc)], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(rc), "config", k, v], check=True)
        write(rc / "README.md", "# orchestrator-returns\n")
        write(rc / "smoke-001" / "RETURN.md", "# RETURN: smoke-001\n")
        subprocess.run(["git", "-C", str(rc), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(rc), "commit", "-q", "-m", "init"], check=True)
        subprocess.run(["git", "-C", str(rc), "push", "-q", "-u", "origin", "main"], check=True, capture_output=True)
        self.origin = origin

    def make_seed(self):
        docs = self.tmp / "docs"
        write(docs / "agent2-gmail-tonight-2026-10-05.md", "\n".join([
            "# Tonight on agent2 with the Gmail account", "",
            "| # | Run | Time | Folder to open |", "|---|---|---|---|",
            "| 1 | Finish the nasarai.com dev push (L7) | 1 h | `/Users/agent2/orchestrator/sprints/L7-nasarai-devpush-2026-09-30` |",
            "1. TARGET. Account luchansky.elliot.a@gmail.com (Claude Max 20x), agent2 Mac mini, pasted by Elliot (Path B).",
            "6. CONTEXT. Working folder: create /Users/agent2/orchestrator/sprints/writing-home-drafts-2026-10-05/ with inputs/",
            "Sprint outreach-2clients-2026-10-05. Goal: two clients.", ""]))
        write(docs / "run-on-macbook-2026-10-01.md", "\n".join([
            "# Run two loops on the MacBook tonight (Claude Desktop, Nasarai account)", "",
            "| Run | Download | Unpacks to (ROOT) | Prompt file |", "|---|---|---|---|",
            "| 1. Nasarai Q4 revenue sprint L1 | `x.zip` | `~/Downloads/nasarai-marketing-q4-revenue-sprint-2026-09-30/` | `RUN-1.txt` |",
            "| 2. Colombia / Rigo push C1 | `y.zip` | `~/Downloads/colombia-project-playa-2026-10-01/` | `RUN-2.txt` |", ""]))
        extra = write(self.tmp / "extra.json", json.dumps([
            {"id": "lawsuit-loops-2026-10-01", "title": "Lawsuit loops", "source_doc": "thread:Lawsuit loops on agent2",
             "host_hint": "agent2", "sensitive": True, "match_words": ["lawsuit", "apeira", "pave", "toptal"]}]))
        harvest.main(["seed", str(docs), "--out", str(self.root / "blitz-returns" / "seeds" / "seed-test.json"),
                      "--extra", str(extra), "--root", str(self.root)])

    def make_pc(self):
        pkg = self.projects / "writing-home" / "work" / "claude-code" / "second-account-loops-2026-09-30"
        write(pkg / "MANIFEST.md", "# MANIFEST\nOverall: normal\n")
        write(pkg / "loops" / "L3" / "state.json", "{}")

    def run(self, *args):
        out = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out):
            harvest.main(list(args) + ["--root", str(self.root)])
        return out.getvalue()

    def activate(self):
        """Point the fake ssh at this fixture (another fixture may have been made since)."""
        os.environ["FAKE_SSH"] = str(self.ssh)
        os.environ["FAKE_HOMES"] = json.dumps(self.homes)

    def manifest(self):
        return json.loads((self.root / "blitz-returns" / "harvest-manifest.json").read_text(encoding="utf-8"))

    def row(self, rid):
        return next(r for r in self.manifest()["rows"] if r["id"] == rid)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class HarvestTest(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("fleet", None)
        self.fx.activate()
        os.environ["HARVEST_PC_HOME"] = str(self.fx.tmp / "pc-home")   # keep the real PC home out of tests

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.close()

    def test_1_patch_fleet(self):
        sys.argv = ["patch_fleet.py", "--tools", str(self.fx.tools)]
        out = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out):
            patch_fleet.main()
            patch_fleet.main()   # idempotent
        text = out.getvalue()
        self.assertIn("verification passed", text)
        self.assertIn("already patched", text)
        sys.modules.pop("fleet", None)
        sys.path.insert(0, str(self.fx.tools))
        import fleet
        self.assertEqual([h["name"] for h in fleet.load_hosts()], ["agent2", "macbook"])
        self.assertEqual([h["name"] for h in fleet.load_hosts(harvest=True)], ["agent2", "macbook", "agent1", "pc"])

    def test_2_check_blocks_credentials(self):
        out = self.fx.run("check")
        self.assertEqual(out.count("PASS"), 2, out)

    @unittest.skipIf(os.name == "nt", "end-to-end fixture needs a POSIX sh as the fake Mac")
    def test_3_first_harvest(self):
        out = self.fx.run("all", "--days", "14")
        m = self.fx.manifest()
        hosts = m["harvest"]["hosts"]
        self.assertTrue(hosts["agent2"]["reachable"])
        self.assertFalse(hosts["agent2"]["partial"], hosts["agent2"])
        self.assertFalse(hosts["macbook"]["reachable"])
        l7 = self.fx.row("L7-nasarai-devpush-2026-09-30")
        self.assertEqual(l7["status"], "found")
        self.assertEqual(l7["copy"], "copied")
        self.assertIs(l7["sensitive"], False)
        self.assertGreaterEqual(l7["push_score"], 4, l7["push_signals"])
        dest = self.fx.root / "blitz-returns" / l7["dest"]
        self.assertTrue((dest / "nasarai-platform" / "src" / "index.ts").exists())
        self.assertTrue((dest / "nasarai-platform" / ".git" / "HEAD").exists())
        for never in (".env", "claude-token", "nasarai-platform/node_modules", "nasarai-platform/.git/objects",
                      "design/.DS_Store", "nasarai-platform/packages/ui/tokens.css"):
            self.assertFalse((dest / never).exists(), never)
        self.assertEqual(self.fx.row("outreach-2clients-2026-10-05")["status"], "not_found")
        self.assertIn("never started", self.fx.row("outreach-2clients-2026-10-05")["note"])
        self.assertEqual(self.fx.row("colombia-project-playa-2026-10-01")["status"], "not_found_yet")
        law = self.fx.row("lawsuit-damages-L9-2026-10-01")
        self.assertIs(law["sensitive"], True)
        self.assertEqual(law["topic_row"], "lawsuit-loops-2026-10-01")
        # pattern pass: the fleet harness (no plan) is caught by its session metadata
        fh = next(r for r in m["rows"] if r.get("path", "").endswith("fleet-harness"))
        self.assertEqual(fh["kind"], "pattern")
        self.assertTrue(any(s.startswith("model") for s in fh["push_signals"]))
        self.assertTrue(any(s.startswith("intensity") for s in fh["push_signals"]))
        notes = next(r for r in m["rows"] if r.get("path", "").endswith("Documents/notes"))
        self.assertNotEqual(notes["status"], "found")
        # old sprint folders outside the window are not copied
        self.assertNotIn("test-001", " ".join(r.get("dest", "") for r in m["rows"]))
        # PC runs are pointers, not copies
        pc = [r for r in m["rows"] if r.get("host") == "pc"]
        self.assertTrue(pc and all(r.get("copy") in ("pointer", None) for r in pc))
        # the scan never leaked conversation text or the token file
        raw = "".join(p.read_text() for p in (self.fx.root / "blitz-returns" / ".scan").glob("*.jsonl"))
        self.assertNotIn("BODY-SHOULD-NOT-LEAK", raw)
        self.assertNotIn("NEVER-READ-ME", raw)
        # push: full return for L7, pointer for the restricted run, nothing for runs without a RETURN.md
        rc = self.fx.root / "returns"
        self.assertIn("site build", (rc / "L7-nasarai-devpush-2026-09-30" / "RETURN.md").read_text())
        self.assertTrue(m["harvest"]["push"]["pushed"], m["harvest"]["push"])
        log = subprocess.run(["git", "--git-dir", str(self.fx.origin), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertIn("Harvest blitz returns", log)
        # manifest-schema.json declares every key the code writes, and every status and kind it uses
        schema = json.loads((HERE / "manifest-schema.json").read_text(encoding="utf-8"))
        hprops = schema["properties"]["harvest"]["properties"]
        self.assertFalse(set(m["harvest"]) - set(hprops), "harvest keys missing from the schema")
        rprops = schema["$defs"]["row"]["properties"]
        for r in m["rows"]:
            self.assertFalse(set(r) - set(rprops), r["id"])
            self.assertIn(r["status"], rprops["status"]["enum"], r["id"])
            self.assertIn(r["kind"], rprops["kind"]["enum"], r["id"])
            for k in schema["$defs"]["row"]["required"]:
                self.assertIn(k, r, r["id"])
        # report
        rep = Path(m["harvest"]["report"]).read_text(encoding="utf-8")
        self.assertIn("macbook unreachable", rep)
        excluded = rep.split("## 5.")[1].split("## 6.")[0]
        for rid in ("lawsuit-damages-L9-2026-10-01", "lawsuit-loops-2026-10-01", "colombia-project-playa-2026-10-01"):
            self.assertIn(rid, excluded)
        self.assertLess(len(rep.splitlines()), 151)
        self.assertEqual(m["harvest"]["audit"], [])

    @unittest.skipIf(os.name == "nt", "end-to-end fixture needs a POSIX sh as the fake Mac")
    def test_4_second_harvest_is_unchanged_and_changes_make_v2(self):
        fx = Fixture(macbook_up=True)
        try:
            fx.run("all", "--days", "14")
            m1 = fx.manifest()
            q4 = next(r for r in m1["rows"] if r["id"] == "nasarai-marketing-q4-revenue-sprint-2026-09-30")
            self.assertEqual(q4["status"], "found")
            self.assertIs(q4["sensitive"], False, q4["sensitive_why"])   # "Overall: normal" beats the soft word
            playa = next(r for r in m1["rows"] if r["id"] == "colombia-project-playa-2026-10-01")
            self.assertIs(playa["sensitive"], True)
            fx.run("all", "--days", "14")
            m2 = fx.manifest()
            copied = [r for r in m2["rows"] if r.get("copy") and r["copy"] not in ("unchanged", "pointer")]
            self.assertEqual(copied, [], [(r["id"], r["copy"]) for r in copied])
            rep = Path(m2["harvest"]["report"]).read_text(encoding="utf-8")
            first = rep.split("## 1. Needed from Elliot")[1].split("## 2.")[0].strip()
            self.assertEqual(first, "Nothing.", rep)
            self.assertTrue(Path(m2["harvest"]["report"]).name.endswith("-2.md"))
            # a changed run becomes -v2; the first copy stays
            l7 = fx.tmp / "agent2" / "orchestrator" / "sprints" / "L7-nasarai-devpush-2026-09-30" / "RETURN.md"
            l7.write_text(l7.read_text() + "\nlate fix\n")
            os.utime(l7, (time.time() - 3600,) * 2)
            fx.run("all", "--days", "14")
            r = next(x for x in fx.manifest()["rows"] if x["id"] == "L7-nasarai-devpush-2026-09-30")
            self.assertTrue(r["dest"].endswith("-v2"), r["dest"])
            self.assertTrue((fx.root / "blitz-returns" / "agent2" / "L7-nasarai-devpush-2026-09-30" / "RETURN.md").exists())
            # sync zip: no restricted run inside
            fx.run("sync")
            import zipfile
            z = zipfile.ZipFile(fx.manifest()["harvest"]["sync"]["zip"])
            names = z.namelist()
            self.assertTrue(any(n.startswith("agent2/L7-nasarai-devpush-2026-09-30-v2/") for n in names), names)
            self.assertFalse(any("lawsuit" in n or "colombia" in n for n in names), names)
            man = json.loads(z.read("harvest-manifest.json"))
            law = next(x for x in man["rows"] if x["id"] == "lawsuit-damages-L9-2026-10-01")
            self.assertEqual(law.get("return", "[withheld: restricted]"), "[withheld: restricted]")
        finally:
            fx.close()

    @unittest.skipIf(os.name == "nt", "end-to-end fixture needs a POSIX sh as the fake Mac")
    def test_9_find_prints_each_host(self):
        out = self.fx.run("find", "--days", "14")
        self.assertIn("macbook (luchanskyelliot@100.116.248.10): unreachable", out)
        self.assertIn("folder ~/orchestrator/sprints/L7-nasarai-devpush-2026-09-30", out)
        self.assertIn("session cwd=~/fleet-harness models=claude-opus-5-5", out)
        self.assertIn("prompts (1): outreach-2clients-2026-10-05.md", out)
        self.assertNotIn("BODY-SHOULD-NOT-LEAK", out)

    def test_5_doc_parser(self):
        rows = {r["id"]: r for r in harvest.parse_doc("\n".join([
            "# Loop: AI maturity scales (agent2, Gmail Max)",
            "1. TARGET. Account luchansky.elliot.a@gmail.com (Claude Max 20x), agent2 Mac mini, pasted by Elliot (Path B).",
            "Sprint maturity-scales-2026-10-05. Build it.",
            "copy your kit from the MacBook (`~/Downloads/nasarai-marketing-q4-revenue-sprint-2026-09-30/`, about 1 MB)",
            "- Sprint id `nasarai-q4-revenue-L1-2026-09-30`. Zip: `C:\\x.zip`"]), "doc.md")}
        self.assertEqual(rows["maturity-scales-2026-10-05"]["host_hint"], "agent2")
        self.assertEqual(rows["maturity-scales-2026-10-05"]["launch_path"], "B")
        self.assertEqual(rows["nasarai-marketing-q4-revenue-sprint-2026-09-30"]["host_hint"], "macbook")
        self.assertIsNone(rows["nasarai-marketing-q4-revenue-sprint-2026-09-30"]["account"])
        self.assertIn("nasarai-q4-revenue-L1-2026-09-30", rows)

    def test_6_member_filter(self):
        for rel, ok in (("a/.env", False), ("a/.env.local", False), ("x/id_rsa", False), ("k.pem", False),
                        ("r/.git/objects/aa", False), ("r/.git/HEAD", True), ("r/.git/refs/heads/main", True),
                        ("n/node_modules/a.js", False), ("../evil", False), ("/etc/passwd", False),
                        ("work/out.md", True), (".ssh/config", False), ("._x", False)):
            self.assertEqual(harvest.member_allowed(rel, 10, False)[0], ok, rel)
        self.assertFalse(harvest.member_allowed("big.log", harvest.BIG_LOG + 1, False)[0])
        self.assertFalse(harvest.member_allowed("work/out.md", 10, True)[0])
        self.assertTrue(harvest.member_allowed("loop/state.json", 10, True)[0])

    def test_7_run_root_twin(self):
        H = "/Users/agent2"
        cases = {H + "/orchestrator/sprints/L7/loop": H + "/orchestrator/sprints/L7",
                 H + "/Downloads/pkg/loops/L1": H + "/Downloads/pkg", H + "/Downloads": None,
                 H + "/orchestrator/bin": None, H + "/fleet-harness": H + "/fleet-harness", H: None,
                 H + "/Library/x": None, H + "/proj/loop": H + "/proj"}
        for path, want in cases.items():
            self.assertEqual(harvest.run_root(path, H), want, path)

    def test_8_next_reset(self):
        t = harvest.parse_time("2026-09-30T20:00:00Z")
        self.assertEqual(harvest.iso(harvest.next_reset("elliot@cybernovaequity.com", t)), "2026-10-01 12:00 UTC")
        self.assertEqual(harvest.iso(harvest.next_reset("elliot@nasarai.com", t)), "2026-10-01 04:15 UTC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
