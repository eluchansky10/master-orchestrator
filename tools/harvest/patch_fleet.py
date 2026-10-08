#!/usr/bin/env python3
r"""patch_fleet.py: one-time, idempotent change that lets hosts.conf carry harvest-only rows.

PLAN.md section 9 adds agent1 and the PC to hosts.conf "with harvest_only: yes so the balancer still never
dispatches to them". fleet.py's load_hosts() reads fields 0 to 6 only, so this patch:
  1. adds an optional 8th hosts.conf field, harvest_only, and makes load_hosts() skip "yes" rows unless it is
     called with harvest=True (only tools\harvest\harvest.py does), so caps, balance, dispatch and pull never
     touch them;
  2. appends the agent1 and pc rows to hosts.conf.
Both files are backed up first (fleet.py.bak-<date>, hosts.conf.bak-<date>) and keep LF line endings. Running it
again changes nothing, except that it restores LF if an earlier run left CRLF behind.

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


def main():
    tools = Path(sys.argv[sys.argv.index("--tools") + 1]) if "--tools" in sys.argv else Path(__file__).resolve().parents[1]
    fleet_py, hosts = tools / "fleet.py", tools / "hosts.conf"
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    for f in (fleet_py, hosts):
        repair_eol(f)
    src = read(fleet_py)
    if NEW in src:
        print("fleet.py: already patched")
    elif OLD in src:
        shutil.copyfile(fleet_py, fleet_py.with_name("fleet.py.bak-" + stamp))
        write(fleet_py, src.replace(OLD, NEW))
        print("fleet.py: load_hosts() now skips harvest-only rows (backup fleet.py.bak-%s)" % stamp)
    else:
        sys.exit("fleet.py: load_hosts() is not the expected text; not changed. Patch by hand.")
    conf = read(hosts)
    missing = [ln for ln in HOST_LINES if ln not in conf]
    if missing:
        shutil.copyfile(hosts, hosts.with_name("hosts.conf.bak-" + stamp))
        write(hosts, conf.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
        print("hosts.conf: added %d lines (backup hosts.conf.bak-%s)" % (len(missing), stamp))
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
