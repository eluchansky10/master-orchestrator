---
name: blitz-harvest
description: Find blitz runs and major token pushes across the PC, agent2, the MacBook and agent1, and bring them home to C:\Projects and orchestrator-returns. Use for "harvest", "blitz harvest", "pull the blitz returns", "where did that loop run", or after a weekly reset.
---

# Blitz harvest

`weekly-blitz` decides what to run before a reset. This skill runs afterwards. It learns what was launched from the blitz plans and run prompts, also recognizes big pushes that no plan named, finds each run on Elliot's machines over SSH (read-only), copies the run folders to `C:\Projects\master-orchestrator\blitz-returns\`, pushes every new `RETURN.md` to `eluchansky10/orchestrator-returns`, and writes a short report. Run from the Master Orchestrator Project, it also copies the non-restricted runs into the Project files and keeps an index in project memory.

The work is done by plain scripts in `C:\Projects\master-orchestrator\tools\harvest\` (no model calls). Run them with `C:\Python313\python.exe`. Reading the report and deciding what to do with it is the Claude's own work.

## What it looks for

**Planned runs.** Rows in `harvest-manifest.json`, from, in order:
1. Run prompts and plans: `blitz\*.md` (weekly-blitz reports and the paste files they produced), `*\RUN-*-PROMPT.txt`, `intake\run-*.md`, `prompts\*`. A run is named by its folder path (`/Users/<user>/orchestrator/sprints/<id>`, `~/Downloads/<package>`), a `Sprint <id>` line, a `run-sprint.sh` or `fleet.py dispatch` line, or a package zip name. Weekly-blitz reports name board items rather than folders, so their runs are matched through the paste files they point to.
2. `fleet.py` state (`state\dispatch-log.jsonl`): every sprint the balancer dispatched.
3. The orchestrator-returns clone: returns already there are marked so only new or changed ones are pushed.
4. From the Project only: rows the thread adds from its own threads (see below).

**Pattern-recognized pushes.** Any run folder or session no plan names is scored on five signals: **model** (the latest Fable or Opus in its control files or session metadata), **intensity** (xhigh or max effort, Ultracode, Workflow or several Agent calls, `/goal`, `/loop`, a run over 2 hours, a watchdog or `MAX_HOURS`), **location** (a Mac, or a sprint account), **timing** (started within 48 hours before that account's weekly reset), and **shape** (sprint control files, or a sprint folder). Score 4 or 5: harvested like a planned run. Score 2 or 3: listed for Elliot to confirm. Sprint folders under `~/orchestrator/sprints` are always harvested.

**Window.** The trailing 8 days by default (`--days 8`, never under 7), so a full weekly cycle of every account is covered. A planned run is never dropped for age. Raise `--days` for a catch-up run.

## Hosts

| Host | SSH target | What is searched |
|---|---|---|
| PC (DESKTOP-GJ0EK81) | local | `C:\Projects\*` (hidden and personal folders skipped), session metadata in `%USERPROFILE%\.claude\projects` and `.codex\sessions`, blitz pages in Downloads (names only). PC runs are recorded by reference (`RUNS-HERE.md`), not duplicated |
| agent2 | `agent2@agents-mac-mini-1` | `~/orchestrator/sprints/*`, `~/orchestrator/returns/*`, `~/orchestrator-prompts`, sprint-shaped folders five levels under home, session metadata |
| MacBook Air | `luchanskyelliot@100.116.248.10` | the same, plus loop packages unpacked in `~/Downloads` |
| agent1 | `agent1@100.82.254.11` | the same |
| Jjess's Mac mini | none | never scanned; named in the report when evidence points at it |

Targets come from `tools\hosts.conf` when it names the host. The local evidence sweep also checks `C:\Projects\master-orchestrator\{prompts,blitz,intake,state}`, `hosts.conf`, the PowerShell and bash histories, `~\.ssh\known_hosts` and `tailscale status` for any other Tailscale peer named next to sprint words. Those peers are reported, never scanned.

## Commands

All from the PC: `C:\Python313\python.exe C:\Projects\master-orchestrator\tools\harvest\harvest.py <command>`.

| Goal | Command |
|---|---|
| The whole harvest | `all` (add `--days 14` for a catch-up) |
| See what would be copied first | `all --dry-run` |
| Build the run list only | `plan` |
| Find runs on every host, read-only | `scan` (one host: `--host agent2`; slow Mac: `--slow`) |
| Copy found runs to the PC | `copy` |
| Push new `RETURN.md` files | `push` (runs `fleet.py pull` first; `--no-fleet-pull` skips it) |
| Write the report | `report` |
| Harvest a pattern push from now on | `confirm <name>` (or `host/name`, or the full path) |
| Stop listing a pattern push | `drop <name>` |
| Zip the non-restricted subset for the Project | `sync-zip --have host/run,host/run` |
| Run plan rows from documents (any machine) | `rows --docs <folder holding blitz\ intake\ prompts\> --out rows.json` |
| Run the tests | `selftest` |

## Running locally (the default)

1. Run `harvest.py all`. On a first run, or after adding a host, run `all --dry-run` first and check the copy list and sizes.
2. Open the newest `blitz-returns\HARVEST-<date>.md`. Act on section 1, "Needed from Elliot", first. Tell Elliot only what is in it, in one line each.
3. For each push in section 4 "to confirm", ask Elliot once in a short list: harvest or drop. Record each answer with `confirm` or `drop`, then run `all` again.
4. The report says "Project sync not run". To sync, run this skill from the Master Orchestrator Project.

## From the Master Orchestrator Project

1. **Rows from the Project.** The blitz plans and paste files live in `/mnt/project-files/master-orchestrator/`. In the thread's container run `python3 /mnt/project-files/master-orchestrator/skills/blitz-harvest/tools/harvest.py --root <scratch folder> --days 8 rows --docs /mnt/project-files/master-orchestrator --out rows.json`. Then read, once, the threads "Weekly blitz run", "/weekly-blitz", "Lawsuit loops on agent2", "Loop package intake", "MacBook lane and cap balancing" and "Blitz inventory and progress page" (titles and first replies only, with `fetch_thread`) and add a row for any run they name that is not in rows.json: `{"id": ..., "title": ..., "source_doc": "thread:<title>", "host_hint": ...}`.
2. **Run it on the PC.** Check `list_devices`, then `start_rc_session` on Elliot's message in the preapproved `C:\Projects` folder (environment id in project memory). Brief: save the rows JSON to `C:\Projects\master-orchestrator\blitz-returns\_inputs\project-rows-<date>.json`; run `harvest.py --days 8 all --from-project --extra-rows <that file>`; then `harvest.py sync-zip --have <runs already under /mnt/project-files/master-orchestrator/blitz-returns/, as host/run>`; send back the report text, the counts and the zip path and size. Never brief it to write on a Mac or to run scripted Claude.
3. **Bring the subset into the Project.** The PC session cannot write to `/mnt/project-files`. Read `sync-<date>.zip` through the folder tools when this thread has them; otherwise ask Elliot in one line to upload that zip to the thread. Unzip it into `/mnt/project-files/master-orchestrator/blitz-returns/` with Python's `zipfile`, after checking every top-level `host/run` in it is `sensitive: false` in the zip's `harvest-manifest.json`; never extract `pc/` or anything restricted. The zip holds at most 200 MB; larger runs come with their control files only, listed in its `SYNC-INDEX.json`.
4. **Memory index.** Update the one memory file `blitz-harvest-index` (type project) in place: one line per harvested run with id, host, PC path, Project path or "PC only (restricted)", status and date. Keep it under 4 KB by pointing at the report for detail.
5. **Reply.** Lead with "Needed from Elliot" (or "Nothing needed"), then the counts (found, copied, not found, excluded), then the report's section 5 "Excluded for privacy" word for word, and attach the report from the Project copy through `attached_outputs`. Under 80 words of prose besides the excluded list.
6. **Tracker.** Send the coordinator one message listing the harvested runs and their status, so the board's owning thread can move their cards to "Returned (awaiting ingestion)" or "Done this week". Do not edit the board here.

## Restricted material

Each run is screened by name only: its id, title, folder path, and the markdown headings of its README, MANIFEST and RETURN files. It is restricted when it was dispatched with `--sensitive`, when its prompt marks it restricted or Claude-only, or when a topic word matches: lawsuit, litigation, Apeira, Toptal, US Pave, CureIS, client, NDA, deposition, family, personal debt, WhatsApp, messages export, photos, Colombia, Project Playa. Add words in `tools\harvest\sensitive-keywords.txt`. A run that could not be read counts as restricted for the Project.

Restricted runs are copied to the PC and pushed to the private returns repo (where they already belong), never to the Project files or memory, never to another host. Section 5 of the report names every one of them, with the word that triggered it.

## Rules this skill never breaks

- Read-only on every Mac: nothing is written, moved or deleted there. Copies always go host to PC.
- Never copies credentials: names matching `*token*`, `*secret*`, `*.pem`, `id_*`, `.env*`, keychains, `credentials*.json`, `auth.json`, `.netrc`, `.npmrc`, `.pypirc`, `*.p12`, `*.pfx`, `*.key` are excluded on the Mac and dropped again on the PC; after each copy the harvest checks `blitz-returns` holds none. Git history, `node_modules`, virtualenvs and caches are left behind; `.git\HEAD` and `.git\config` are kept for provenance.
- Never overwrites a harvest. A run that changed lands in a `-v2` (`-v3`, ...) sibling; an unchanged run is skipped.
- Never reads conversation bodies: session logs give only their folder, model ids, timestamps, sizes and tool-call counts, and are never copied.
- Never runs scripted Claude, and never on el@elliotl.im. The scripts make no model calls.
- Never scans or writes to Jjess's Mac mini.
- A host that cannot be reached, a refused step or a failed push is a line in the report, never a stop.

## Files

- `tools\harvest\harvest.py`: the harvester. `remote-find.sh`: the read-only finder piped to each Mac. `manifest-schema.json`: the manifest's shape. `test_harvest.py`: tests (`selftest`).
- `blitz-returns\HARVEST-<date>.md` (same-day re-runs get `-2`, `-3`), `harvest-manifest.json`, `_history\`, `<host>\<run>\` copies, `_inputs\` (Project rows, `confirmed.txt`, `dropped.txt`), `_scan\` (raw finder output, PC only).
- Plan and design record: `/mnt/project-files/master-orchestrator/skills/blitz-harvest/PLAN.md`.
