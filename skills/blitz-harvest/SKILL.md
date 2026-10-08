---
name: blitz-harvest
description: Find every blitz run and major token push on the PC, agent2, the MacBook and agent1, copy it home to C:\Projects, push new returns, and report. Use for "harvest", "blitz harvest", "collect the blitz runs", "pull the blitz returns", "where did that loop run".
---

# Blitz harvest

`weekly-blitz` decides what to run before a reset; this skill brings the results home afterwards. It learns what was launched (planning docs, run prompts, fleet state, the returns repo, Project threads), also recognizes big pushes nobody planned, finds each run over SSH without writing anything on the Macs, copies the run folders to `C:\Projects\master-orchestrator\blitz-returns\`, pushes every new `RETURN.md` to `eluchansky10/orchestrator-returns`, and writes `HARVEST-<date>.md`. From the Master Orchestrator Project it also brings the non-restricted runs into the Project files and keeps a memory index.

The work is done by plain Python and POSIX shell in `C:\Projects\master-orchestrator\tools\harvest\` (no model calls, never `claude -p`). Run them with `C:\Python313\python.exe`. Reading the report and asking Elliot about it is the Claude's part.

Design record: `/mnt/project-files/master-orchestrator/skills/blitz-harvest/PLAN.md` (Project) and `BUILD-STATE.md` beside it (what was built, tested, and corrected).

## Commands

`C:\Python313\python.exe C:\Projects\master-orchestrator\tools\harvest\harvest.py <command> [--days N]`

| Goal | Command |
|---|---|
| The whole harvest | `all` (default window 8 days, never under 7; `--days 14` for a catch-up) |
| See what would be copied first | `plan`, `scan`, then `copy --dry-run`, then `report` |
| Build the run list from documents and seeds | `plan` |
| Find runs on every host (read-only), match and score them | `scan` (one host: `--host agent2`; slow Mac: `--slow`) |
| See raw finder output for a host | `find --host agent2` |
| Copy found runs to the PC | `copy` (runs `check` first) |
| Push returns | `push` (calls `fleet.py pull` first; restricted returns go as pointer stubs unless `--push-restricted full`) |
| Write the report | `report` |
| Credential self-test | `check` |
| Harvest a pattern push from now on / stop listing it | `confirm <folder>` / `drop <folder>` |
| Zip the non-restricted subset for the Project | `sync` |
| Rows from Project docs and threads (cloud side) | `seed <docs...> --extra rows.json --out seed-cloud-<date>.json` |
| Tests | `python tools\harvest\test_harvest.py` |

## What it looks for

**Planned runs** (`plan`): every `blitz-returns\seeds\*.json` (rows the Project sends), the PC's `blitz\`, `intake\`, `prompts\`, `macbook-*` docs, `state\dispatch-log.jsonl`, and the returns repo clone. A run is named by its folder path, a `Sprint <id>` line, a `run-sprint.sh` or `fleet.py dispatch` line, or a package name. A seed row with `match_words` is a topic (for example the lawsuit loops): every folder whose name holds one of the words becomes its own row.

**Pattern-recognized pushes** (`scan`): any run folder or session no plan names is scored on five signals: model (latest Fable or Opus), intensity (high/xhigh/max effort, Ultracode, Workflow or several subagents, `/goal`, `/loop`, over 2 hours, `MAX_HOURS`), location (a Mac or a sprint account), timing (started within 48 h before that account's reset), shape (state.json, PROGRESS.md, RETURN.md, MANIFEST.md, GOAL.txt, SPRINT.md). Score 4 or 5 is harvested; 2 or 3 is listed to confirm.

**Where a run can be.** On a Mac, only inside the home folder: `~/orchestrator/sprints/<id>` (and `sprints/_aborted/<id>`), `~/Downloads/<package>`, Claude Desktop scratch workspaces, and any home folder holding run control files. Never `~/gt` (Gas Town, agent2's always-on agent office), home dot folders, `~/Library`, temp or system folders. On the PC, only under `C:\Projects`: `<project>\work\<lane>\<child>`, `...\loops\<child>`, or a dated run folder; `_control`, dot folders and `master-orchestrator` itself are never runs, and `.claude\worktrees` counts as its project. Latest-model sessions scoring 4+ whose folder is no run (home, temp) are listed in section 4 as loose sessions, with nothing to copy.

**Window**: folders and sessions newer than `--days`; a planned run is never dropped for age.

## Hosts

From `tools\hosts.conf` (rows with an 8th field `yes` are harvest-only; `fleet.py` skips them): PC (local), agent2, MacBook Air, agent1. Jjess's Mac mini is never scanned. Before scanning, each Mac gets `ssh -o BatchMode=yes hostname`; an unreachable Mac is a line in the report with the one step for Elliot, and the rest continues. On the PC only `C:\Projects` (no hidden folders), session metadata, shell histories, `known_hosts` and `tailscale status` are read; personal folders are never searched. PC runs are recorded as `pc\<name>\RUNS-HERE.md` pointers, not copied.

## Running on the PC (the default)

1. `harvest.py all`. On a first run, or after adding a host, do `plan`, `scan`, `copy --dry-run`, `report` first and check the copy list and sizes.
2. Open the newest `blitz-returns\HARVEST-<date>.md` (at most 150 lines: 1 Needed from Elliot, 2 Found and copied, 3 Not found anywhere, 4 Discovered runs and pushes to confirm, 5 Excluded for privacy, 6 Possible other hosts, 7 Pushed). Section 1 first: tell him each line, one line each. Pushes to confirm are never in section 1; they are a question, not a blocker.
3. Section 4 pushes to confirm: ask Elliot once, in a short list, harvest or drop; record with `confirm` / `drop`; run `all` again.
4. The report's sync line says the Project copy was not made; run this skill from the Project for that.

## From the Master Orchestrator Project

1. **Rows.** In the thread's container: `python3 /mnt/project-files/master-orchestrator/skills/blitz-harvest/tools/harvest.py seed /mnt/project-files/master-orchestrator/{blitz,intake,prompts} /mnt/project-files/master-orchestrator/macbook-* /mnt/project-files/SPRINT.md --extra rows.json --out seed-cloud-<date>.json`. `rows.json` holds runs named only in the threads "Weekly blitz run", "/weekly-blitz", "Lawsuit loops on agent2", "Loop package intake", "MacBook lane and cap balancing", "Blitz inventory and progress page" (read once with `fetch_thread`, first pages only): `{"id", "title", "source_doc": "thread:<title>", "host_hint", "account", "sensitive", "note", "match_words"?}`.
2. **Run it on the PC.** `start_rc_session` on Elliot's message, `environment_id` from project memory (preapproved `C:\Projects`). Brief: write the seed JSON (in the brief) to `blitz-returns\seeds\`, run `harvest.py all` (`--days` as needed), then `harvest.py sync`, and send back the report text, the counts, and the sync zip path and size. Never brief it to write on a Mac or to run scripted Claude. If its permission check refuses a step, it says so; ask Elliot once, never route around it.
3. **Bring the subset into the Project.** <!-- route filled in at build stage 5 --> Check every top-level `host/run` in the zip is `sensitive: false` in its `harvest-manifest.json` before extracting into `/mnt/project-files/master-orchestrator/blitz-returns/`; never extract `pc/` or a restricted run. The zip holds at most 200 MB; larger runs come as control files only, listed in `SYNC-NOTES.md`.
4. **Memory index.** Update the memory file `blitz-harvest-index` (type project) in place: one line per harvested run (id, host, PC path, Project path or "PC only (restricted)", status, date), under 4 KB, pointing at the report for detail.
5. **Reply.** Lead with what is needed from Elliot (or "Nothing needed"), then counts (found, copied, not found, excluded), then the report's section 5 "Excluded for privacy" word for word, with the report attached through `attached_outputs`. Under 80 words besides the excluded list.
6. **Tracker.** One message to the coordinator listing the harvested runs and their status, so the board's owning thread moves the cards. Do not edit the board here.

## Restricted material

Screened by name only, in order: runner `SENSITIVE=1`; the plan or prompt marks it restricted; on the PC, the project's `.project.json` `sensitivity` is anything but normal; strong words in the folder or control-file names (lawsuit, litigation, Apeira, Toptal, Pave, CureIS, deposition, Colombia, Playa, tax, taxes); a package README/MANIFEST marked Confidential or "Sensitive project"; strong words in headings; "Overall: normal" in a MANIFEST clears soft words; soft words (client, NDA, family, personal debt, WhatsApp, messages export, photos). Output bodies are never read. A run that could not be read counts as restricted for the Project. One run has one answer: when any part of it is restricted (its returns copy, a `-try1` or `-v2` sibling, a PC copy), all of it is.

Restricted runs are copied to the PC only and go to the returns repo as pointer stubs; never into Project files or memory, never to another host. Section 5 of the report names each one with the reason; everywhere else in the report a restricted row is anonymous ("a restricted run on agent2, named in section 5").

## Rules this skill never breaks

- Read-only on every Mac: the only remote commands are `hostname`, `remote-find.sh` on stdin, and `tar -czf -` of a run folder.
- Never copies credentials: tokens, secrets, keys, `.env*`, keychains, `.ssh`, `.aws`, `.config/orchestrator`, `auth.json`, `.netrc`, `.npmrc` and the like are excluded on the Mac and dropped again on the PC; `check` proves both layers before any copy, and an audit of `blitz-returns` follows every copy. `node_modules`, caches and git objects stay behind; `.git\HEAD`, `config` and refs are kept.
- Never overwrites a harvest: a changed run lands in a `-v2` sibling; unchanged runs are skipped; a running run keeps its last copy.
- Never reads conversation bodies: session logs give only folder, model ids, timestamps, sizes and counts.
- Never runs scripted Claude, never scans Jjess's Mac mini, never searches personal folders.
- An unreachable host, a refused step or a failed push is a report line, never a stop.

## Files

`tools\harvest\`: `harvest.py`, `remote-find.sh`, `patch_fleet.py` (one-time `hosts.conf`/`fleet.py` change), `manifest-schema.json`, `test_harvest.py`. `blitz-returns\`: `HARVEST-<date>.md`, `harvest-manifest.json`, `harvest-ledger.json`, `choices.json`, `seeds\`, `<host>\<run>\`, `pc\<name>\RUNS-HERE.md`, `.scan\` (raw finder output), `README.md`.
