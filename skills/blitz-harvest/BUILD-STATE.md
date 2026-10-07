# blitz-harvest build state

Builder: Opus 5.5 thread "Blitz harvest build loop" (Master Orchestrator Project), started 2026-10-07 22:39Z.
Brief: BUILD-PROMPT.md + PLAN.md (unchanged). This file is the only thing a resumed builder trusts.
Mirror: C:\Projects\master-orchestrator\skills\blitz-harvest\BUILD-STATE.md (written when the PC session is up).

## Resume here
BLOCKED at stage 1, step 1 (PC). The PC session's auto-mode classifier refused cloning branch claude/project-thread-9s605g (head 80abb56) into C:\Projects\master-orchestrator\.harvest-src: "[Untrusted Code Integration]". Nothing changed on the PC or any Mac. Asked Elliot once (23:3xZ) to approve in this thread with the exact sentence below. When he answers: re-send the same stage-1 brief (clone, install, seed, patch_fleet, tests, check, `find --days 14`) and continue. Meanwhile: draft SKILL.md (stage 5 text) in the cloud; nothing else can run without the PC.

## Stages (PLAN.md section 11)
- [x] 0. Setup: state file, fold in the earlier build thread's notes, PC session, PC inventory (fleet.py, hosts.conf, returns clone, reachability)
- [ ] 1. remote-find.sh written and run over SSH on agent2 and the MacBook; returns the 2026-10-05 night-run folders (L7, L8, post drafts, coach channel bet, resumed-on-CyberNova copies) and the MacBook Q4 kit and Project Playa folders
- [ ] 2. harvest.py plan (with the 3.7 pattern pass); manifest lists: 3 pattern examples (agent2 Desktop fleet harness 2026-10-01, MacBook night run 2026-10-01, L6 Jev loop), the four 2026-10-05 agent2 runs, runs 7 and 8 (not_found), MacBook RUN-1 and RUN-2, smoke-001, L6 Jev loop
- [ ] 3. scan, copy (dry run first), push, report; credential-exclusion test passes before the first real copy
- [ ] 4. Real `harvest.py all --days 14`: Excluded section names the lawsuit loops and Colombia/CureIS material; no token or .env in blitz-returns; orchestrator-returns received every new RETURN.md
- [ ] 5. SKILL.md written and proposed (propose_skills, plus weekly-blitz Step 2 improvement); cloud path run once (Project copy + memory index blitz-harvest-index)
- [ ] 6. First HARVEST report delivered to Elliot; harvest date and host facts in reference/fleet-inventory.md; coordinator told
- [ ] Acceptance: a second `all` copies nothing (unchanged everywhere) and the report's first section reads "Nothing." with all hosts reachable

## Current stage
1

## Verified (evidence)
### Stage 0 (PC session cse_01Kyi7ChrUHr4EKsxzv7ojP2, 2026-10-07 ~22:45Z, read-only)
- C:\Projects\master-orchestrator is not its own git repo (inside C:\Projects control-plane, ignored by .gitignore `/*`). No tools\harvest\, blitz-returns\, blitz\, intake\, skills\blitz-harvest\ on the PC. skills\ holds sprint-cap-balancer only. prompts\ holds L6, L7, L8 folders and openclaw-cleanup-2026-09-28.md.
- hosts.conf: 7 fields `name|ssh|account|account_key|last_day|sensitive|max_running`; rows agent2 (agent2@agents-mac-mini-1, cybernova, <account key label>, sensitive yes, 2) and macbook (luchanskyelliot@100.116.248.10, nasarai, PENDING, 2026-10-01, no, 2).
- fleet.py 640 lines: `run()`, `ssh(host, remote, data, timeout)` -> (code, str, str), `ssh_bytes()`, SSH = C:\Windows\System32\OpenSSH\ssh.exe, BatchMode, ConnectTimeout=15. `load_hosts()` reads fields 0-6 only. `pull()` tars ~/orchestrator/returns per host, skips SENSITIVE=1 sprints and ingested ones (status DONE/MOVED or .ingested), git add/commit/push in returns\. Runner files per sprint: status, account, started_utc, ended_utc, capped_until, model, config.env (SENSITIVE=1).
- returns clone: origin eluchansky10/orchestrator-returns, main, clean; top level 2026-10-05\, L6-jev-conformed-2026-09-30\, smoke-001\, test-001..004\, README (contract: `<sprint-id>/RETURN.md`, 10 fields; "Restricted material stays pointer-only and never comes here").
- Python 3.13.7; ssh/scp/tar (System32 and Git); no rsync; git and gh present. 159 GB free on C:.
- tailscale: agents-mac-mini-1 online, agent-1-1 online, luchanskys-macbook-air OFFLINE, jjesss-mac-mini online (not touched), muse 100.103.168.33 online (unknown peer), ipad offline, iphone online.
- SSH BatchMode: agent2 -> Agents-Mac-mini.local (0); agent1 -> agent-1.local (0); MacBook -> connection timed out (255).
- PC can read github.com/eluchansky10/master-orchestrator (ls-remote ok): code ships to the PC through this branch.
- Earlier build thread left nothing on the PC (no harvest files anywhere under C:\Projects\master-orchestrator).

## Earlier build thread ("Build blitz harvest skill", 2026-10-07 22:33Z to 23:05Z)
Its code is on branch claude/project-thread-ddudol (head faaf91b) and in from-stopped-build-thread/; notes in BUILD-NOTES.md.
Decision (23:1xZ): keep this loop's code (built after the PC inventory, uses fleet.load_hosts/fleet.SSH, pointer stubs for restricted returns, harvest_only patch). Borrowed from it: `colombia`/`playa` as strong restricted words; .gitattributes LF rule; its SKILL.md may be adapted at stage 5. Its gaps 1 and 2 (column guessing, restricted returns pushed in full) are why it was not adopted.

## Cloud build log
- 23:0xZ tests: 3 failures fixed (session lines with spaced JSON, TARGET-line account bleeding into other hosts' rows, sync test path). Added `find` command, manifest-schema.json, schema-conformance test, Windows skips for the POSIX-only end-to-end tests. `python3 test_harvest.py`: `Ran 9 tests ... OK`.
- `harvest.py check` (cloud, GNU tar): both packers PASS; "remote excludes alone stopped 18 of 18 never-copy files" after adding .aws, Keychains, .config/orchestrator to the tar excludes (before: 17 of 18; the local filter caught the 18th).
- Seed `harvest-seeds/seed-cloud-2026-10-07.json`: 15 rows from 15 Project docs + 3 thread rows (lawsuit-loops topic, cureis-ai-strategy topic, nasarai-claude-design). Thread facts recorded: L7/L8 first ran Path A on CyberNova 10-01 02:27Z, resumed 10-05 in agent2's Desktop on Gmail Max; Q4 kit and Colombia ran on the MacBook from ~/Downloads/<package>; lawsuit loops and CureIS L1-L4 never launched; fleet harness (agent2 Desktop, 10-01 morning) left to the pattern pass on purpose.
- Status rename to the plan's term: rows on an unreachable host are `not_found_yet`.

## Open questions for Elliot
- 22:5xZ asked once: wake the MacBook Air and confirm Tailscale is connected (it is offline; blocks the MacBook half of stage 1 and acceptance "all hosts reachable").
- 23:3xZ asked once: approve the PC running the harvest code. Sentence for him to send in the thread: "I approve cloning branch claude/project-thread-9s605g of eluchansky10/master-orchestrator into C:\Projects\master-orchestrator\.harvest-src and running its harvest scripts on the PC". Alternative: a Claude Code allow rule on the PC for that clone and for `C:\Python313\python.exe C:\Projects\master-orchestrator\tools\harvest\*`.

## Refusals and narrowed steps
- 2026-10-07 ~23:30Z, PC session, stage 1 step 1: `git clone --depth 1 --branch claude/project-thread-9s605g https://github.com/eluchansky10/master-orchestrator.git C:\Projects\master-orchestrator\.harvest-src` refused by the auto-mode classifier, reason "[Untrusted Code Integration]". The step already stayed inside C:\Projects, so it cannot be narrowed further; any other way of landing the same code (pasting it, writing it file by file) would be routing around the refusal, so none was tried. Waiting on Elliot's approval. `git ls-remote` showed 80abb567e1169862c9c5a6b6a92d1dee9a1dcf8e (exit 0).

## Proposed plan corrections
1. Section 9 (hosts.conf rows for agent1 and the PC): fleet.py's load_hosts() reads fields 0-6 only, so an 8th `harvest_only` column alone would let the 15-minute balance routine SSH to (and possibly dispatch to) agent1 and a `local` PC row. Fix being applied: add an optional 8th column and make load_hosts() drop harvest-only rows unless called with harvest=True. (Found by the PC session too.)
2. Section 6.2 vs the returns repo README: the README says restricted material "stays pointer-only and never comes here" and fleet.py pull skips sensitive sprints, while 6.2 says sensitive returns are pushed in full. Default built: restricted RETURN.md goes to the repo as a pointer stub (id, host, PC path, status), full text on the PC only; `--push-restricted full` exists if Elliot wants the full text there. Reason: a push to GitHub cannot be taken back, a pointer can be upgraded later.
3. Section 3.1 mirror: the PC cannot read Project files, so instead of mirroring the planning docs themselves the cloud procedure ships a seed file (rows extracted from the Project's blitz/intake/macbook docs and thread titles) to blitz-returns\seeds\ on the PC; `harvest.py plan` reads every seed plus the PC's own prompts\, state\ and returns\.
4. Section 4 PC roots: `C:\Users\Owner\Downloads\*.html` is not scanned (personal folder; the build prompt says never search personal folders). The report says so.
5. Section 6.1 transport: tar streamed over SSH (remote `tar czf -` with excludes, unpacked by Python tarfile with a second credential filter) for every folder, because scp -r cannot exclude node_modules or credentials and rsync is absent on the PC. fleet.py already uses tar over SSH.
6. Section 5.3 output: JSON lines with a final `done` line instead of one JSON array, so a 60-second cut still leaves parseable partial results.
7. Section 3.7 placement: the pattern pass needs host data, so it runs inside `scan` (plan builds rows from documents; scan adds pattern rows). Stage 2's check is made on the manifest after `plan` + `scan`.
8. Section 5.4 keyword list: added `colombia` and `playa` as strong words so Project Playa material is restricted by name even without its README marks (acceptance step 4 needs it).
