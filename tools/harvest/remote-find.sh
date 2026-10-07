#!/bin/sh
# remote-find.sh: read-only finder for blitz-harvest. harvest.py pipes this
# script over SSH (`ssh <host> sh -s -- <mode> ...`) and reads one JSON object
# per line from stdout. It never writes, moves or deletes anything on the host,
# and it never prints file bodies: only names, sizes, times, hashes, markdown
# headings and short metadata tokens (model ids, cwd paths, marker words).
#
# Modes
#   find DAYS BUDGET [PATTERN ...]  candidate run folders and session metadata.
#                                    PATTERNs are sprint ids or prompt file
#                                    names; a folder reports which ones its
#                                    state.json / PROGRESS.md mention.
#   size PATH                        bytes, file count, newest mtime, RETURN.md
#                                    hash, big logs and credential-like names
#                                    for one folder (same exclusions as copy).
#
# Output lines carry "kind": host, folder, prompts, zip, session, size, done.
# A run that is cut off has no "done" line, and harvest.py marks it partial.

LC_ALL=C
export LC_ALL
START=$(date +%s)
MODE=${1:-find}
[ $# -gt 0 ] && shift

# ---------------------------------------------------------------- helpers

# JSON string body: drop control characters, escape backslash, quote and tab,
# join lines with \n.
jstr() {
  printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' |
    sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g' |
    awk 'BEGIN { ORS = "" } { if (NR > 1) printf "\\n"; print }'
}
q() { printf '"%s"' "$(jstr "$1")"; }

# JSON array of strings from newline-separated stdin.
jarr() {
  first=1
  printf '['
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if [ $first -eq 1 ]; then first=0; else printf ','; fi
    q "$line"
  done
  printf ']'
}

tf() { if [ -e "$1" ]; then printf true; else printf false; fi; }

if stat -f %m / >/dev/null 2>&1; then
  mtime_of() { stat -f %m "$1" 2>/dev/null || echo 0; }
  STAT_MS='stat -f %m:%z'
else
  mtime_of() { stat -c %Y "$1" 2>/dev/null || echo 0; }
  STAT_MS='stat -c %Y:%s'
fi

sha_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | awk '{print $1}'
  else
    openssl dgst -sha256 "$1" 2>/dev/null | awk '{print $NF}'
  fi
}

elapsed() { echo $(( $(date +%s) - START )); }

# Map any path under $HOME to the run folder that owns it:
#   ~/orchestrator/{sprints,returns}/<id>   ~/<container>/<name>   ~/<name>
# Prints nothing for the home folder itself or for personal and hidden folders.
run_root() {
  p=$1
  case "$p" in "$HOME"/*) ;; *) return ;; esac
  rel=${p#"$HOME"/}
  first=${rel%%/*}
  case "$rel" in */*) rest=${rel#*/} ;; *) rest= ;; esac
  case "$first" in
    .*|Library|Pictures|Movies|Music|Photos|Public|Applications) return ;;
  esac
  case "$first" in
    orchestrator)
      second=${rest%%/*}
      case "$rest" in */*) rest2=${rest#*/} ;; *) rest2= ;; esac
      case "$second" in
        sprints|returns)
          third=${rest2%%/*}
          [ -n "$third" ] || return
          root="$HOME/orchestrator/$second/$third" ;;
        *) return ;;
      esac ;;
    orchestrator-prompts) return ;;
    Downloads|Documents|Desktop|Projects|projects|code|src|dev|work|repos|GitHub|Developer|Sites)
      second=${rest%%/*}
      [ -n "$second" ] || return
      root="$HOME/$first/$second" ;;
    *) root="$HOME/$first" ;;
  esac
  if [ ! -d "$root" ]; then
    # A marker file sitting directly in a container: its own folder is the run.
    root=$(dirname "$p")
  fi
  case "$root" in "$HOME"|"$HOME/orchestrator"|"$HOME/orchestrator/sprints"|"$HOME/orchestrator/returns") return ;; esac
  printf '%s\n' "$root"
}

# Marker file search inside one run folder (shallow; sprints keep their
# control files in the root, in loop/, or in loops/<ID>/).
marker() {
  # $1 folder, $2 file name
  for c in "$1/$2" "$1/loop/$2" "$1"/loops/*/"$2" "$1"/*/"$2" "$1"/*/loops/*/"$2"; do
    [ -f "$c" ] && { printf '%s\n' "$c"; return; }
  done
}

# ---------------------------------------------------------------- size mode

if [ "$MODE" = size ]; then
  P=${1:-}
  if [ -z "$P" ] || [ ! -d "$P" ]; then
    printf '{"kind":"size","path":%s,"error":"missing"}\n' "$(q "$P")"
    exit 0
  fi
  # Same exclusions as the copy step, so the size is what will travel.
  stats=$(find "$P" \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pnpm-store -o -name .turbo -o -name .cache -o -path '*/.git/objects' -o -path '*/.git/lfs' -o -path '*/.git/modules' -o -path '*/.git/worktrees' \) -prune -o -type f -exec $STAT_MS {} + 2>/dev/null |
    awk -F: '{ n++; b += $2; if ($1 > m) m = $1 } END { printf "%d %d %d", n, b, m }')
  set -- $stats
  files=${1:-0}; bytes=${2:-0}; newest=${3:-0}
  ret=$(marker "$P" RETURN.md)
  rsha=""; [ -n "$ret" ] && rsha=$(sha_of "$ret")
  big=$(find "$P" -name node_modules -prune -o -type f -name '*.log' -size +51200k -print 2>/dev/null | sed "s|^$P/||" | head -n 50 | jarr)
  cred=$(find "$P" -name node_modules -prune -o \( -iname '*token*' -o -iname '*secret*' -o -name '*.pem' -o -name 'id_*' -o -name '.env' -o -name '.env.*' -o -name '*.keychain*' -o -name 'credentials*.json' -o -name '.credentials.json' -o -name 'auth.json' -o -name '.netrc' -o -name '.npmrc' -o -name '.pypirc' -o -name '*.p12' -o -name '*.pfx' -o -name '*.key' \) -print 2>/dev/null | sed "s|^$P/||" | head -n 50 | jarr)
  printf '{"kind":"size","path":%s,"files":%s,"bytes":%s,"newest":%s,"return_sha256":%s,"big_logs":%s,"credential_like":%s}\n' \
    "$(q "$P")" "$files" "$bytes" "$newest" "$(q "$rsha")" "$big" "$cred"
  exit 0
fi

# ---------------------------------------------------------------- find mode

DAYS=${1:-8}
BUDGET=${2:-60}
[ $# -ge 2 ] && shift 2 || shift $#
# Remaining arguments are match patterns.
PATTERN_ARGS=""
for pat in "$@"; do
  [ -n "$pat" ] && PATTERN_ARGS="$PATTERN_ARGS -e $(printf '%s' "$pat" | sed "s/'/'\\\\''/g; s/^/'/; s/\$/'/")"
done
PARTIAL=false
over_budget() { [ "$(elapsed)" -ge $(( BUDGET - 5 )) ]; }

# Host line. The account hint is a plain value file written by the sprint kit;
# token files are never opened.
acct=""
[ -f "$HOME/.config/orchestrator/account" ] && acct=$(head -c 200 "$HOME/.config/orchestrator/account" | head -n 1)
printf '{"kind":"host","hostname":%s,"user":%s,"home":%s,"os":%s,"account_hint":%s,"has_tar":%s,"has_rsync":%s,"has_cap_status":%s,"days":%s}\n' \
  "$(q "$(hostname 2>/dev/null)")" "$(q "$(id -un 2>/dev/null)")" "$(q "$HOME")" "$(q "$(uname -s 2>/dev/null)")" \
  "$(q "$acct")" "$(command -v tar >/dev/null 2>&1 && echo true || echo false)" \
  "$(command -v rsync >/dev/null 2>&1 && echo true || echo false)" \
  "$(tf "$HOME/orchestrator/bin/cap-status.sh")" "$DAYS"

ROOTS_FILE_LIST=""
add_root() {
  r=$1
  [ -z "$r" ] && return
  case "
$ROOTS_FILE_LIST
" in *"
$r
"*) return ;; esac
  ROOTS_FILE_LIST="$ROOTS_FILE_LIST
$r"
}

# 1. Known run roots, every age (a planned run is never dropped for age).
for base in "$HOME/orchestrator/sprints" "$HOME/orchestrator/returns"; do
  [ -d "$base" ] || continue
  for d in "$base"/*; do
    [ -d "$d" ] && add_root "$d"
  done
done

# Loop packages unpacked in Downloads (a MANIFEST.md or a loops/ folder).
if [ -d "$HOME/Downloads" ]; then
  for d in "$HOME/Downloads"/*; do
    [ -d "$d" ] || continue
    if [ -f "$d/MANIFEST.md" ] || [ -d "$d/loops" ] || [ -f "$d/SPRINT.md" ]; then add_root "$d"; fi
  done
  # Package zips: names and sizes only.
  find "$HOME/Downloads" -maxdepth 1 -type f -name '*.zip' -mtime -"$DAYS" 2>/dev/null | while IFS= read -r z; do
    printf '{"kind":"zip","path":%s,"mtime":%s,"bytes":%s}\n' "$(q "$z")" "$(mtime_of "$z")" "$(wc -c < "$z" | tr -d ' ')"
  done
fi

# Prompt staging folders: file names only.
for pdir in "$HOME/orchestrator-prompts" "$HOME/orchestrator/prompts"; do
  [ -d "$pdir" ] || continue
  names=$(ls -1 "$pdir" 2>/dev/null | head -n 400 | jarr)
  printf '{"kind":"prompts","dir":%s,"files":%s}\n' "$(q "$pdir")" "$names"
done

# 2. Generic finder inside the window: any folder under the home folder that
# holds sprint control files, five levels deep. Hidden, personal and cache
# folders are pruned.
if ! over_budget; then
  GENERIC=$(find "$HOME" -maxdepth 5 \( -path "$HOME/.*" -o -name node_modules -o -name Library -o -name .Trash -o -name .git -o -name .venv -o -name __pycache__ -o -name Pictures -o -name Movies -o -name Music \) -prune -o \
      -type f \( -name state.json -o -name PROGRESS.md -o -name RETURN.md -o -name SPRINT.md -o -name GOAL.txt \) -mtime -"$DAYS" -print 2>/dev/null |
    while IFS= read -r m; do run_root "$m"; done | sort -u)
  while IFS= read -r r; do add_root "$r"; done <<EOF
$GENERIC
EOF
else
  PARTIAL=true
fi

# 3. Session metadata inside the window. Claude Code keeps one JSONL per
# session under ~/.claude/projects/<slug>/; Codex keeps rollouts under
# ~/.codex/sessions/. Only metadata tokens are extracted: cwd, model ids,
# timestamps, sizes, counts of Agent/Workflow tool calls, and marker words.
emit_session() {
  tool=$1; f=$2
  head_part=$(head -n 80 "$f" 2>/dev/null)
  cwd=$(printf '%s' "$head_part" | grep -o '"cwd" *: *"[^"]*"' | head -n 1 | sed 's/.*: *"//; s/"$//')
  models=$(grep -o '"model" *: *"[^"]*"' "$f" 2>/dev/null | sed 's/.*: *"//; s/"$//' | sort | uniq -c | sort -rn | head -n 5 | awk '{print $2}' | jarr)
  first_ts=$(printf '%s' "$head_part" | grep -o '"timestamp" *: *"[^"]*"' | head -n 1 | sed 's/.*: *"//; s/"$//')
  last_ts=$(tail -n 5 "$f" 2>/dev/null | grep -o '"timestamp" *: *"[^"]*"' | tail -n 1 | sed 's/.*: *"//; s/"$//')
  bytes=$(wc -c < "$f" | tr -d ' ')
  agents=$(grep -o '"name" *: *"\(Agent\|Task\)"' "$f" 2>/dev/null | wc -l | tr -d ' ')
  workflows=$(grep -o '"name" *: *"Workflow"' "$f" 2>/dev/null | wc -l | tr -d ' ')
  markers=$(printf '%s' "$head_part" | grep -o -i -E '/goal|/loop|ultracode|ultrathink|MAX_HOURS|xhigh|max effort|high effort|"effort" *: *"[a-z]*"|run-sprint' | tr 'A-Z' 'a-z' | sed 's/"effort" *: *"/effort:/; s/"$//' | sort -u | jarr)
  printf '{"kind":"session","tool":%s,"file":%s,"cwd":%s,"root":%s,"models":%s,"first_ts":%s,"last_ts":%s,"mtime":%s,"bytes":%s,"agent_calls":%s,"workflow_calls":%s,"markers":%s}\n' \
    "$(q "$tool")" "$(q "$f")" "$(q "$cwd")" "$(q "$(run_root "$cwd")")" "$models" "$(q "$first_ts")" "$(q "$last_ts")" \
    "$(mtime_of "$f")" "$bytes" "$agents" "$workflows" "$markers"
}

SESSION_ROOTS=""
for sdir in "$HOME/.claude/projects" "$HOME/.codex/sessions"; do
  [ -d "$sdir" ] || continue
  tool=claude; case "$sdir" in *codex*) tool=codex ;; esac
  SESSFILES=$(find "$sdir" -type f -name '*.jsonl' -mtime -"$DAYS" 2>/dev/null)
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if over_budget; then PARTIAL=true; break; fi
    line=$(emit_session "$tool" "$f")
    printf '%s\n' "$line"
    r=$(printf '%s' "$line" | sed -n 's/.*"root":"\([^"]*\)".*/\1/p')
    [ -n "$r" ] && SESSION_ROOTS="$SESSION_ROOTS
$r"
  done <<EOF
$SESSFILES
EOF
done
SESSION_ROOTS=$(printf '%s\n' "$SESSION_ROOTS" | sort -u)
while IFS= read -r r; do add_root "$r"; done <<EOF
$SESSION_ROOTS
EOF

# The desktop app's own session records, where they exist as files: cwd and
# model tokens only.
DESK="$HOME/Library/Application Support/Claude"
if [ -d "$DESK" ] && ! over_budget; then
  find "$DESK" -maxdepth 4 -type f -name '*.json' -path '*ession*' -mtime -"$DAYS" 2>/dev/null | head -n 200 |
  while IFS= read -r f; do
    cwd=$(grep -o '"cwd" *: *"[^"]*"' "$f" 2>/dev/null | head -n 1 | sed 's/.*: *"//; s/"$//')
    [ -z "$cwd" ] && continue
    model=$(grep -o '"model" *: *"[^"]*"' "$f" 2>/dev/null | head -n 1 | sed 's/.*: *"//; s/"$//')
    printf '{"kind":"session","tool":"desktop","file":%s,"cwd":%s,"root":%s,"models":%s,"first_ts":"","last_ts":"","mtime":%s,"bytes":%s,"agent_calls":0,"workflow_calls":0,"markers":[]}\n' \
      "$(q "$f")" "$(q "$cwd")" "$(q "$(run_root "$cwd")")" "$(printf '%s\n' "$model" | jarr)" "$(mtime_of "$f")" "$(wc -c < "$f" | tr -d ' ')"
  done
fi

# 4. One line per candidate folder.
printf '%s\n' "$ROOTS_FILE_LIST" | while IFS= read -r d; do
  [ -z "$d" ] && continue
  [ -d "$d" ] || continue
  if over_budget; then
    printf '{"kind":"folder","path":%s,"cut_off":true}\n' "$(q "$d")"
    continue
  fi
  st=$(marker "$d" state.json); pr=$(marker "$d" PROGRESS.md); rt=$(marker "$d" RETURN.md)
  gl=$(marker "$d" GOAL.txt); sp=$(marker "$d" SPRINT.md); mf=$(marker "$d" MANIFEST.md)
  newest=$(mtime_of "$d")
  for c in "$st" "$pr" "$rt"; do
    [ -n "$c" ] || continue
    t=$(mtime_of "$c"); [ "$t" -gt "$newest" ] && newest=$t
  done
  rsha=""; [ -n "$rt" ] && rsha=$(sha_of "$rt")
  # Headings only: folder README, MANIFEST and RETURN titles feed the
  # sensitivity screen. No body text.
  heads=$(for h in "$d/README.md" "$mf" "$rt"; do [ -n "$h" ] && [ -f "$h" ] && grep -E '^#{1,3} ' "$h" 2>/dev/null | head -n 15; done | cut -c1-120 | jarr)
  # Status word from state.json, and model/effort tokens from control files.
  status=""
  [ -n "$st" ] && status=$(grep -o -E '"(status|state|phase)" *: *"[A-Za-z_ -]{1,30}"' "$st" 2>/dev/null | head -n 1 | sed 's/.*: *"//; s/"$//')
  [ -z "$status" ] && [ -f "$d/STATUS" ] && status=$(head -c 40 "$d/STATUS" | head -n 1)
  ctl=""
  for c in "$st" "$pr" "$gl" "$sp" "$d/ORIGINAL-PROMPT.md" "$d/prompt.md" "$d/PROMPT.md"; do
    [ -n "$c" ] && [ -f "$c" ] && ctl="$ctl
$c"
  done
  models="[]"; markers="[]"; matches="[]"; maxh=""
  if [ -n "$ctl" ]; then
    files=$(printf '%s\n' "$ctl" | sed '/^$/d')
    models=$(printf '%s\n' "$files" | while IFS= read -r c; do head -n 200 "$c"; done |
      grep -o -i -E 'claude-(fable|opus|sonnet|haiku)-[0-9]+(-[0-9]+)?|(fable|opus|sonnet|haiku) [0-9]+(\.[0-9]+)?' | tr 'A-Z' 'a-z' | sort -u | head -n 8 | jarr)
    markers=$(printf '%s\n' "$files" | while IFS= read -r c; do head -n 200 "$c"; done |
      grep -o -i -E '/goal|/loop|ultracode|workflow tool|MAX_HOURS=[0-9]+|xhigh|max effort|high effort|watchdog|run-sprint' | tr 'A-Z' 'a-z' | sort -u | jarr)
    if [ -n "$PATTERN_ARGS" ]; then
      matches=$(printf '%s\n' "$files" | while IFS= read -r c; do cat "$c"; done | eval "grep -o -F $PATTERN_ARGS" 2>/dev/null | sort -u | jarr)
    fi
  fi
  printf '{"kind":"folder","path":%s,"name":%s,"mtime":%s,"has_state":%s,"has_progress":%s,"has_return":%s,"has_goal":%s,"has_sprint":%s,"has_manifest":%s,"has_loops":%s,"return_sha256":%s,"run_status":%s,"headings":%s,"models":%s,"markers":%s,"matches":%s}\n' \
    "$(q "$d")" "$(q "$(basename "$d")")" "$newest" \
    "$( [ -n "$st" ] && echo true || echo false)" "$( [ -n "$pr" ] && echo true || echo false)" "$( [ -n "$rt" ] && echo true || echo false)" \
    "$( [ -n "$gl" ] && echo true || echo false)" "$( [ -n "$sp" ] && echo true || echo false)" "$( [ -n "$mf" ] && echo true || echo false)" \
    "$(tf "$d/loops")" "$(q "$rsha")" "$(q "$status")" "$heads" "$models" "$markers" "$matches"
done

over_budget && PARTIAL=true
printf '{"kind":"done","elapsed":%s,"partial":%s}\n' "$(elapsed)" "$PARTIAL"
