#!/usr/bin/env python3
r"""patch_fleet.py: one-time, idempotent change that lets hosts.conf carry harvest-only rows.

PLAN.md section 9 adds agent1 and the PC to hosts.conf "with harvest_only: yes so the balancer still never
dispatches to them". fleet.py's load_hosts() reads fields 0 to 6 only, so this patch:
  1. adds an optional 8th hosts.conf field, harvest_only, and makes load_hosts() skip "yes" rows unless it is
     called with harvest=True (only tools\harvest\harvest.py does), so caps, balance, dispatch and pull never
     touch them;
  2. appends the agent1 and pc rows to hosts.conf;
  3. makes pull() skip anything in a host's returns folder that is not a folder (macOS AppleDouble `._<id>` files
     crashed it on Windows with WinError 267), and asks the Mac's tar not to add such files;
  4. makes pull() leave a returns folder alone when it holds a `.harvested` marker: the harvest wrote that
     RETURN.md from the run folder itself, and without the marker pull put the runner's stub back on every run.
Both files are backed up first, once per run that changes them (fleet.py.bak-<date>, hosts.conf.bak-<date>; an
existing backup is never overwritten) and keep LF line endings. Running it again changes nothing, except that it restores LF if an earlier
run left CRLF behind.

Usage: C:\Python313\python.exe C:\Projects\master-orchestrator\tools\harvest\patch_fleet.py [--tools DIR]
"""
import datetime as dt
import importlib
import shutil
import sys
from pathlib import Path

OLD = '''def load_hosts():
    hosts = []
    for line in HOSTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        hosts.append({"name": f[0], "ssh": f[1], "account": f[2], "account_key": f[3],
                      "last_day": None if f[4] in ("", "-") else f[4],
                      "sensitive_ok": f[5].lower() == "yes", "max_running": int(f[6])})
    return hosts
'''

NEW = '''def load_hosts(harvest=False):
    """hosts.conf rows. A row whose optional 8th field (harvest_only) is "yes" is for blitz-harvest only:
    it is skipped unless harvest=True, so caps, balance, dispatch, move and pull never touch it."""
    hosts = []
    for line in HOSTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        harvest_only = len(f) > 7 and f[7].lower() == "yes"
        if harvest_only and not harvest:
            continue
        hosts.append({"name": f[0], "ssh": f[1], "account": f[2], "account_key": f[3],
                      "last_day": None if f[4] in ("", "-") else f[4],
                      "sensitive_ok": f[5].lower() == "yes", "max_running": int(f[6]),
                      "harvest_only": harvest_only})
    return hosts
'''

# pull(): a stray file in a host's ~/orchestrator/returns is not a return
PULL_LOOP_OLD = '''            for d in (src_root.iterdir() if src_root.exists() else []):
                dest = RETURNS / d.name
'''
PULL_LOOP_NEW = '''            for d in (src_root.iterdir() if src_root.exists() else []):
                if not d.is_dir() or d.name.startswith("._"):
                    continue  # macOS AppleDouble files and stray files are not returns
                dest = RETURNS / d.name
'''
PULL_TAR_OLD = '''ssh_bytes(h, "cd ~/orchestrator && tar -czf - returns")'''
PULL_TAR_NEW = '''ssh_bytes(h, "cd ~/orchestrator && COPYFILE_DISABLE=1 tar -czf - returns")'''

PULL_SKIP_OLD = '''                if dest.exists() and (done in ("DONE", "MOVED") or (dest / ".ingested").exists()):
                    continue  # already ingested; ingestion may have edited it
'''
PULL_SKIP_NEW = PULL_SKIP_OLD + '''                if (dest / ".harvested").exists():
                    continue  # blitz-harvest owns this return (the run folder's own RETURN.md); never overwrite it
'''

HOST_LINES = [
    "# Optional 8th field: harvest_only. Rows with \"yes\" are read only by tools\\harvest\\harvest.py (blitz-harvest);",
    "# fleet.py skips them, so they never get caps reads, sprints or pulls.",
    "agent1|agent1@100.82.254.11|unknown (Claude token expired)|-|-|no|0|yes",
    "pc|local|el@elliotl.im|-|-|yes|0|yes",
]


def read(path):
    """Text with its original line endings normalised to LF (the files are LF; Windows must not turn them CRLF)."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def write(path, text):
    path.write_bytes(text.encode("utf-8"))      # bytes, so Windows keeps LF


def repair_eol(path):
    """An earlier version of this script wrote CRLF on Windows. Put LF back when the backup it made was LF."""
    data = path.read_bytes()
    baks = sorted(path.parent.glob(path.name + ".bak-*"))
    if b"\r\n" in data and baks and b"\r\n" not in baks[0].read_bytes():
        path.write_bytes(data.replace(b"\r\n", b"\n"))
        print("%s: line endings restored to LF (as in %s)" % (path.name, baks[0].name))


def backup(path, stamp):
    """path.bak-<stamp>, or -2, -3 ... when that name is taken: the first backup of the day is never overwritten."""
    b, n = path.with_name("%s.bak-%s" % (path.name, stamp)), 1
    while b.exists():
        n += 1
        b = path.with_name("%s.bak-%s-%d" % (path.name, stamp, n))
    shutil.copyfile(path, b)
    return b.name


def main():
    tools = Path(sys.argv[sys.argv.index("--tools") + 1]) if "--tools" in sys.argv else Path(__file__).resolve().parents[1]
    fleet_py, hosts = tools / "fleet.py", tools / "hosts.conf"
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    for f in (fleet_py, hosts):
        repair_eol(f)
    src = read(fleet_py)
    baks = []

    def fleet_backup():
        if not baks:
            baks.append(backup(fleet_py, stamp))
        return baks[0]

    if NEW in src:
        print("fleet.py: already patched")
    elif OLD in src:
        name = fleet_backup()
        src = src.replace(OLD, NEW)
        write(fleet_py, src)
        print("fleet.py: load_hosts() now skips harvest-only rows (backup %s)" % name)
    else:
        sys.exit("fleet.py: load_hosts() is not the expected text; not changed. Patch by hand.")
    for what, pairs, fail in (
            ("skips stray files such as ._<id>", ((PULL_LOOP_OLD, PULL_LOOP_NEW), (PULL_TAR_OLD, PULL_TAR_NEW)),
             "its ._ crash stays until patched by hand"),
            ("leaves returns the harvest owns (.harvested) alone", ((PULL_SKIP_OLD, PULL_SKIP_NEW),),
             "pull and the harvest keep rewriting each other's RETURN.md")):
        todo = [(o, n) for o, n in pairs if n not in src]
        if not todo:
            print("fleet.py: pull() already %s" % what)
        elif all(o in src for o, n in todo):
            name = fleet_backup()
            new_src = src
            for o, n in todo:
                new_src = new_src.replace(o, n)
            compile(new_src, str(fleet_py), "exec")
            write(fleet_py, new_src)
            src = new_src
            print("fleet.py: pull() now %s (backup %s)" % (what, name))
        else:
            print("fleet.py: pull() is not the expected text; left as is (%s)" % fail)
    conf = read(hosts)
    missing = [ln for ln in HOST_LINES if ln not in conf]
    if missing:
        name = backup(hosts, stamp)
        write(hosts, conf.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
        print("hosts.conf: added %d lines (backup %s)" % (len(missing), name))
    else:
        print("hosts.conf: already has the harvest rows")
    sys.path.insert(0, str(tools))
    fleet = importlib.import_module("fleet")
    plain = [h["name"] for h in fleet.load_hosts()]
    every = [h["name"] for h in fleet.load_hosts(harvest=True)]
    print("fleet.load_hosts():             %s" % plain)
    print("fleet.load_hosts(harvest=True): %s" % every)
    if any(n in plain for n in ("agent1", "pc")) or not {"agent1", "pc"} <= set(every):
        sys.exit("verification FAILED")
    print("verification passed")


if __name__ == "__main__":
    main()
