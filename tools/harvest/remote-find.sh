#!/bin/sh
# remote-find.sh: find sprint and push run folders on one Mac for blitz-harvest.
#
# Read-only by construction: it writes, moves and deletes nothing, creates no temp files and runs no
# model. It never opens token, keychain, .env or credential files, and it never prints conversation
# text: from Claude Code session logs it takes only cwd, model, version, timestamps, slash-command names
# and the number of subagent logs.
#
# Run from the PC:  ssh <host> 'sh -s -- --days 8 [--find-seconds 25] [--path DIR]...' < remote-find.sh
# Output: one JSON object per line, ending with {"kind":"done",...}. Output without the "done" line was
# cut short (the PC's time limit), and harvest.py marks that host partial.
#
# Record kinds: host, prompt, log, session, folder, zip, warn, done.

VERSION=1
DAYS=8
FIND_SECONDS=25
MAX_SESSIONS=400
NL='
'
PATHS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --days) DAYS=$2; shift 2 ;;
    --find-seconds) FIND_SECONDS=$2; shift 2 ;;
    --max-sessions) MAX_SESSIONS=$2; shift 2 ;;
    --path) PATHS="$PATHS$2$NL"; shift 2 ;;
    *) shift ;;
  esac
done
case "$DAYS" in ''|*[!0-9]*) DAYS=8 ;; esac
case "$FIND_SECONDS" in ''|*[!0-9]*) FIND_SECONDS=25 ;; esac
case "$MAX_SESSIONS" in ''|*[!0-9]*) MAX_SESSIONS=400 ;; esac

START=$(date -u +%s)
H=${HOME%/}
set -f   # no globbing of found paths; globs below are expanded with set +f

# ------------------------------------------------------------------ helpers

# JSON string literal for $1 (control characters dropped, newlines kept as \n).
js() {
  printf '%s' "$1" | tr -d '\000-\010\013-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/\\t/g' |
    awk 'BEGIN { ORS = ""; printf "\"" } NR > 1 { printf "\\n" } { print } END { printf "\"" }'
}

# JSON array of strings from newline-separated stdin (blank lines skipped).
jarr() {
  first=1
  printf '['
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    [ $first = 1 ] || printf ','
    js "$line"
    first=0
  done
  printf ']'
}

# String values of JSON key $1 found in stdin, one per line (compact or spaced JSON).
jval() {
  grep -o -E "\"$1\" *: *\"[^\"]*\"" | sed -E "s/^\"$1\" *: *\"//; s/\"\$//"
}

# BSD stat (macOS) or GNU stat (Linux, used only by the tests).
case "$(stat -f %m / 2>/dev/null)" in
  ''|*[!0-9]*) BSD=0 ;;
  *) BSD=1 ;;
esac
mtime() { if [ $BSD = 1 ]; then stat -f %m "$1" 2>/dev/null; else stat -c %Y "$1" 2>/dev/null; fi; }
fsize() { if [ $BSD = 1 ]; then stat -f %z "$1" 2>/dev/null; else stat -c %s "$1" 2>/dev/null; fi; }

# Cache and dependency folders: never walked, never copied.
PRUNE_EXPR='\( -name node_modules -o -name .git -o -name .venv -o -name venv -o -name __pycache__ -o -name .cache -o -name .pnpm-store -o -name .next -o -name .turbo -o -name .npm \) -prune -o'

# "newest_mtime total_bytes file_count" for a tree, cache folders skipped.
tree_stats() {
  if [ $BSD = 1 ]; then
    eval "find \"\$1\" $PRUNE_EXPR -type f -exec stat -f '%m %z' {} +" 2>/dev/null
  else
    eval "find \"\$1\" $PRUNE_EXPR -type f -exec stat -c '%Y %s' {} +" 2>/dev/null
  fi | awk 'BEGIN { m = 0; b = 0; n = 0 } { n++; b += $2; if ($1 > m) m = $1 } END { printf "%d %d %d", m, b, n }'
}

# Run a command under a time limit when perl exists (macOS has no timeout(1)).
with_limit() {
  secs=$1; shift
  if command -v perl >/dev/null 2>&1; then
    perl -e '$t = shift @ARGV; alarm $t; exec @ARGV or exit 127' "$secs" "$@"
  else
    "$@"
  fi
}

# Credential-looking names are never opened.
is_secret_name() {
  case "$(basename "$1" | tr 'A-Z' 'a-z')" in
    *token*|*secret*|*.pem|id_*|.env|.env.*|*.keychain|*.keychain-db|.netrc|.npmrc|*credential*|*.p12|*.key|auth.json|.git-credentials) return 0 ;;
  esac
  return 1
}

# Print the run folder that directory $1 belongs to, or nothing when it is not a run folder.
run_root() {
  d=${1%/}
  case "$d" in
    "$H"|"") return ;;
    "$H/"*) ;;
    *) return ;;           # temp and system folders (/private/tmp, /private/var/folders, ...) are never runs
  esac
  case "$d" in
    # a Claude Desktop Code-tab session started without a folder works in its own scratch workspace
    "$H/Library/Application Support/Claude/scratch-workspaces/"*)
      r=${d#"$H/Library/Application Support/Claude/scratch-workspaces/"}
      echo "$H/Library/Application Support/Claude/scratch-workspaces/${r%%/*}"; return ;;
    "$H/orchestrator/sprints/_aborted/"*) r=${d#"$H/orchestrator/sprints/_aborted/"}; echo "$H/orchestrator/sprints/_aborted/${r%%/*}"; return ;;
    "$H/orchestrator/sprints/_aborted") return ;;
    "$H/orchestrator/sprints/"*) r=${d#"$H/orchestrator/sprints/"}; echo "$H/orchestrator/sprints/${r%%/*}"; return ;;
    "$H/orchestrator/returns/"*) r=${d#"$H/orchestrator/returns/"}; echo "$H/orchestrator/returns/${r%%/*}"; return ;;
    "$H/orchestrator"|"$H/orchestrator/"*) return ;;
    "$H/Library"|"$H/Library/"*|"$H/."*) return ;;
    # Gas Town (agent2's always-on agent office) is infrastructure, not a run: see SKIP_TREES
    "$H/gt"|"$H/gt/"*) return ;;
  esac
  case "$d" in
    */loops/*) d=${d%%/loops/*} ;;
    */loops) d=${d%/loops} ;;
    */loop/*) d=${d%%/loop/*} ;;
    */loop) d=${d%/loop} ;;
  esac
  for top in Downloads Desktop Documents; do
    case "$d" in
      "$H/$top") return ;;
      "$H/$top/"*) r=${d#"$H/$top/"}; echo "$H/$top/${r%%/*}"; return ;;
    esac
  done
  case "$d" in "$H/Pictures"*|"$H/Movies"*|"$H/Music"*|"$H/Public"*|"$H") return ;; esac
  echo "$d"
}

SEEN="$NL"
seen() { case "$SEEN" in *"$NL$1$NL"*) return 0 ;; esac; return 1; }
has() { printf '%s\n' "$REL" | grep -E "(^|/)$1\$" >/dev/null && echo true || echo false; }
rv() { [ -f "$D/$1" ] && ! is_secret_name "$1" && head -c 120 "$D/$1" | tr -d '\r' | head -n 1; }

# Emit one folder record for directory $1 (found because of $2).
emit_folder() {
  D=${1%/}; why=$2
  seen "$D" && return
  SEEN="$SEEN$D$NL"
  if [ ! -d "$D" ]; then
    printf '{"kind":"folder","path":%s,"why":%s,"missing":true}\n' "$(js "$D")" "$(js "$why")"
    return
  fi
  set -- $(tree_stats "$D")
  newest=${1:-0}; bytes=${2:-0}; nfiles=${3:-0}
  recent=false
  [ "$newest" -ge $((START - DAYS * 86400)) ] 2>/dev/null && recent=true
  CTL=$(eval "find \"\$D\" -maxdepth 4 $PRUNE_EXPR -type f \\( -name state.json -o -name PROGRESS.md -o -name RETURN.md \
        -o -name MANIFEST.md -o -name GOAL.txt -o -name SPRINT.md -o -name ORIGINAL-PROMPT.md -o -name prompt.md \
        -o -name REPORT.md -o -name BRIEF.md -o -name README.md -o -name 00-README.md \\) -print" 2>/dev/null | head -n 60)
  REL=$(printf '%s\n' "$CTL" | awk -v p="$D/" 'index($0, p) == 1 { print substr($0, length(p) + 1) }')
  # the main RETURN.md: the one at the root, else the shallowest
  ret=""
  [ -f "$D/RETURN.md" ] && ret="$D/RETURN.md"
  [ -n "$ret" ] || ret=$(printf '%s\n' "$CTL" | grep '/RETURN\.md$' | awk '{ print length($0) " " $0 }' | sort -n | head -n 1 | cut -d' ' -f2-)
  rjson=null
  if [ -n "$ret" ] && [ -f "$ret" ]; then
    rsum=$(cksum < "$ret" | awk '{ print $1 "-" $2 }')
    rjson=$(printf '{"path":%s,"cksum":%s,"mtime":%s,"head":%s,"headings":%s}' "$(js "${ret#"$D"/}")" "$(js "$rsum")" \
            "$(mtime "$ret")" "$(js "$(head -n 20 "$ret" | cut -c1-240)")" "$(grep -E '^#' "$ret" | head -n 40 | cut -c1-160 | jarr)")
  fi
  # README/MANIFEST: headings and sensitivity marks only, never bodies
  docheads=$(for f in "$D/README.md" "$D/MANIFEST.md" "$D/00-README.md"; do [ -f "$f" ] && grep -E '^#' "$f" | head -n 30; done | cut -c1-160)
  overall=$( [ -f "$D/MANIFEST.md" ] && grep -i -E '^ *overall *:' "$D/MANIFEST.md" | head -n 1 | cut -c1-80)
  conf=$(cat "$D/MANIFEST.md" "$D/README.md" "$D/00-README.md" 2>/dev/null | grep -c -E 'Confidential|Sensitive project|RESTRICTED|Restricted project')
  # runner files written by run-sprint.sh (short values only; config.env: key names and two flags)
  cfgkeys=$( [ -f "$D/config.env" ] && sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' "$D/config.env" | head -n 20)
  sens=false; [ -f "$D/config.env" ] && grep -q '^SENSITIVE=1' "$D/config.env" && sens=true
  maxh=$( [ -f "$D/config.env" ] && sed -n 's/^MAX_HOURS=\([0-9.]*\).*/\1/p' "$D/config.env" | head -n 1)
  # model and intensity hints in prompt-like files: the matched words only
  hints=$(printf '%s\n' "$CTL" | grep -E '/(GOAL\.txt|SPRINT\.md|ORIGINAL-PROMPT\.md|prompt\.md|BRIEF\.md)$' | head -n 8 |
          while IFS= read -r f; do head -n 200 "$f"; done |
          grep -o -i -E 'claude-(fable|opus|sonnet)-[0-9]-[0-9]|fable 5\.1|opus 5\.5|(effort|at) (high|xhigh|max)|(high|xhigh|max) effort|ultracode|max_hours=[0-9]+|/goal|/loop|workflow tool|agent tool' |
          tr 'A-Z' 'a-z' | sort -u | head -n 20)
  states=$(printf '%s\n' "$CTL" | grep '/state\.json$' | head -n 3 | while IFS= read -r f; do
             printf '%s: ' "${f#"$D"/}"; head -c 1200 "$f" | tr '\n\t\r' '   '; printf '\n'; done)
  pf=$(printf '%s\n' "$CTL" | grep '/PROGRESS\.md$' | awk '{ print length($0) " " $0 }' | sort -n | head -n 1 | cut -d' ' -f2-)
  prog=""; [ -n "$pf" ] && prog=$(head -n 12 "$pf" | cut -c1-200)
  printf '{"kind":"folder","path":%s,"why":%s,"mtime":%s,"newest":%s,"bytes":%s,"nfiles":%s,"recent":%s,' \
    "$(js "$D")" "$(js "$why")" "$(mtime "$D")" "$newest" "$bytes" "$nfiles" "$recent"
  printf '"has_state":%s,"has_progress":%s,"has_return":%s,"has_manifest":%s,"has_goal":%s,"has_sprint":%s,' \
    "$(has state.json)" "$(has PROGRESS.md)" "$(has RETURN.md)" "$(has MANIFEST.md)" "$(has GOAL.txt)" "$(has SPRINT.md)"
  printf '"control":%s,"return":%s,"doc_headings":%s,"manifest_overall":%s,"confidential_marks":%s,' \
    "$(printf '%s\n' "$REL" | jarr)" "$rjson" "$(printf '%s\n' "$docheads" | jarr)" "$(js "$overall")" "${conf:-0}"
  printf '"runner":{"status":%s,"account":%s,"started":%s,"ended":%s,"model":%s,"config_keys":%s,"sensitive":%s,"max_hours":%s},' \
    "$(js "$(rv status)")" "$(js "$(rv account)")" "$(js "$(rv started_utc)")" "$(js "$(rv ended_utc)")" \
    "$(js "$(rv model)")" "$(printf '%s\n' "$cfgkeys" | jarr)" "$sens" "$(js "$maxh")"
  printf '"hints":%s,"states":%s,"progress_head":%s}\n' \
    "$(printf '%s\n' "$hints" | jarr)" "$(printf '%s\n' "$states" | jarr)" "$(js "$prog")"
}

# Emit folder records for a newline-separated list of directories.
emit_list() {
  why=$1; list=$2
  oldifs=$IFS; IFS=$NL
  for p in $list; do
    IFS=$oldifs
    [ -n "$p" ] && emit_folder "$p" "$why"
    IFS=$NL
  done
  IFS=$oldifs
}

# ------------------------------------------------------------------ 1. host

acct=""
[ -f "$H/.config/orchestrator/account" ] && acct=$(head -c 120 "$H/.config/orchestrator/account" | head -n 1)
printf '{"kind":"host","version":%s,"hostname":%s,"home":%s,"now":%s,"days":%s,"bsd":%s,"account_hint":%s,"orchestrator":%s}\n' \
  "$VERSION" "$(js "$(hostname)")" "$(js "$H")" "$START" "$DAYS" "$BSD" "$(js "$acct")" \
  "$([ -d "$H/orchestrator" ] && echo true || echo false)"

# ------------------------------------------------------------------ 2. prompt and log listings (names only)

set +f
for dir in "$H/orchestrator-prompts" "$H/orchestrator/prompts"; do
  [ -d "$dir" ] || continue
  for f in "$dir"/*; do
    [ -e "$f" ] || continue
    is_secret_name "$f" && continue
    printf '{"kind":"prompt","dir":%s,"name":%s,"mtime":%s,"size":%s,"is_dir":%s}\n' "$(js "$dir")" "$(js "$(basename "$f")")" \
      "$(mtime "$f")" "$(fsize "$f")" "$([ -d "$f" ] && echo true || echo false)"
  done
done
if [ -d "$H/orchestrator/logs" ]; then
  for f in "$H/orchestrator/logs"/*; do
    [ -f "$f" ] || continue
    printf '{"kind":"log","name":%s,"mtime":%s,"size":%s}\n' "$(js "$(basename "$f")")" "$(mtime "$f")" "$(fsize "$f")"
  done
fi

# ------------------------------------------------------------------ 3. orchestrator run folders, then named paths

ORCH=""
for base in sprints returns; do
  for d in "$H/orchestrator/$base"/*/; do
    [ -d "$d" ] || continue
    [ "${d%/}" = "$H/orchestrator/sprints/_aborted" ] && continue
    ORCH="$ORCH${d%/}$NL"
  done
done
for d in "$H/orchestrator/sprints/_aborted"/*/; do
  [ -d "$d" ] && ORCH="$ORCH${d%/}$NL"
done
set -f
emit_list "orchestrator" "$ORCH"
NAMED=$(printf '%s' "$PATHS" | sed "s#^~/#$H/#")
emit_list "named" "$NAMED"

# ------------------------------------------------------------------ 4. Claude Code sessions (metadata only)

# "mtime path" for each session file in the window, Gas Town and home-dot-folder projects left out.
session_files() {
  if [ $BSD = 1 ]; then fmt='%m %N'; opt=-f; else fmt='%Y %n'; opt=-c; fi
  find "$H/.claude/projects" -mindepth 2 -maxdepth 2 -type f -name '*.jsonl' -mtime -"$DAYS" \
    ! -path "$H/.claude/projects/$HDASH-gt/*" ! -path "$H/.claude/projects/$HDASH-gt-*" \
    ! -path "$H/.claude/projects/$HDASH--*" -exec stat "$opt" "$fmt" {} + 2>/dev/null
}

SESS=""
if [ -d "$H/.claude/projects" ]; then
  # Claude names a project folder after its cwd with / and . turned into -; skip Gas Town and home dot folders,
  # then take the newest session files first so the cap never drops recent work
  HDASH=$(printf '%s' "$H" | tr '/.' '--')
  SESS=$(session_files | sort -rn | head -n "$MAX_SESSIONS" | sed 's/^[0-9]* //' |
  while IFS= read -r f; do
    h40=$(head -n 40 "$f")
    t20=$(tail -n 20 "$f")
    cwd=$(printf '%s\n' "$h40" | jval cwd | head -n 1)
    ver=$(printf '%s\n' "$h40" | jval version | head -n 1)
    t1=$(printf '%s\n' "$h40" | jval timestamp | head -n 1)
    t2=$(printf '%s\n' "$t20" | jval timestamp | tail -n 1)
    models=$(printf '%s\n%s\n' "$h40" "$t20" | jval model | grep '^claude-' | sort -u)
    slash=$(printf '%s\n' "$h40" | grep -o '<command-name>/[a-z-]*' | sed 's/<command-name>//' | sort -u)
    effort=$(printf '%s\n' "$h40" | grep -o -i -E '"effort[a-z_]*" *: *"(low|medium|high|xhigh|max)"' | tr 'A-Z' 'a-z' | sort -u | head -n 3)
    sdir=${f%.jsonl}
    nsub=0; wf=false
    if [ -d "$sdir" ]; then
      nsub=$(find "$sdir" -type f -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
      [ -d "$sdir/workflows" ] && wf=true
    fi
    printf '{"kind":"session","file":%s,"size":%s,"mtime":%s,"first_ts":%s,"last_ts":%s,"cwd":%s,"version":%s,"models":%s,"slash":%s,"effort":%s,"subagent_logs":%s,"workflows":%s}\n' \
      "$(js "$f")" "$(fsize "$f")" "$(mtime "$f")" "$(js "$t1")" "$(js "$t2")" "$(js "$cwd")" "$(js "$ver")" \
      "$(printf '%s\n' "$models" | jarr)" "$(printf '%s\n' "$slash" | jarr)" "$(printf '%s\n' "$effort" | jarr)" "$nsub" "$wf"
    lm=-
    printf '%s\n' "$models" | grep -E -q 'fable-5-1|opus-5-5' && lm=L
    [ -n "$cwd" ] && printf 'CWD\t%s\t%s\n' "$lm" "$cwd"
  done)
fi
printf '%s\n' "$SESS" | grep '^{'
CWDROOTS=""
oldifs=$IFS; IFS=$NL
for line in $(printf '%s\n' "$SESS" | sed -n 's/^CWD	//p' | sort -u); do
  IFS=$oldifs
  lm=${line%%	*}; c=${line#*	}
  if [ -d "$c" ]; then
    r=$(run_root "$c")
    # folders that look like work: a latest-model session ran there, or a run marker or repository within two levels
    if [ -n "$r" ] && { [ "$lm" = L ] || find "$r" -maxdepth 2 \( -name state.json -o -name PROGRESS.md -o -name RETURN.md \
         -o -name MANIFEST.md -o -name SPRINT.md -o -name GOAL.txt -o -name .git \) 2>/dev/null | head -n 1 | grep -q .; }; then
      CWDROOTS="$CWDROOTS$r$NL"
    fi
  fi
  IFS=$NL
done
IFS=$oldifs
emit_list "session-cwd" "$(printf '%s' "$CWDROOTS" | sort -u)"

# ------------------------------------------------------------------ 5. generic finder (time-limited)

GEN=$(with_limit "$FIND_SECONDS" find "$H" -maxdepth 4 \( -path "$H/Library" -o -path "$H/.*" -o -path "$H/gt" \
      -o -path "$H/.config" -o -path "$H/orchestrator" -o -path "$H/Pictures" -o -path "$H/Movies" -o -path "$H/Music" \
      -o -name node_modules -o -name .git -o -name .cache -o -name .npm -o -name .venv \) -prune -o -type f \
      \( -name state.json -o -name PROGRESS.md -o -name RETURN.md -o -name MANIFEST.md \) -mtime -"$DAYS" -print 2>/dev/null)
rc=$?
[ $rc -ge 128 ] && printf '{"kind":"warn","msg":%s}\n' "$(js "generic find stopped at its ${FIND_SECONDS}s limit; results are partial")"
GENROOTS=""
oldifs=$IFS; IFS=$NL
for f in $GEN; do
  IFS=$oldifs
  r=$(run_root "$(dirname "$f")")
  [ -n "$r" ] && GENROOTS="$GENROOTS$r$NL"
  IFS=$NL
done
IFS=$oldifs
emit_list "find" "$(printf '%s' "$GENROOTS" | sort -u)"

# ------------------------------------------------------------------ 6. package zips in Downloads (names only)

if [ -d "$H/Downloads" ]; then
  find "$H/Downloads" -maxdepth 1 -type f \( -name '*sprint*.zip' -o -name '*loop*.zip' -o -name '*-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]*.zip' \) \
    -mtime -"$DAYS" 2>/dev/null | head -n 40 | while IFS= read -r z; do
    printf '{"kind":"zip","path":%s,"mtime":%s,"size":%s}\n' "$(js "$z")" "$(mtime "$z")" "$(fsize "$z")"
  done
fi

printf '{"kind":"done","elapsed":%s}\n' "$(( $(date -u +%s) - START ))"
