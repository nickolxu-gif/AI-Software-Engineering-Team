#!/bin/sh

set -eu

die() {
    printf '%s\n' "BLOCKED: $1" >&2
    exit 2
}

original_path=${PATH-}
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin
export PATH
project_root=$(pwd -P)
# Project adapter: the packet and stream utilities are the approved global
# claude-emergency-verifier V4.10.6 core. Only this binding is project-local.
global_skill_root=/Users/qinxu/.codex/skills/claude-emergency-verifier
global_core_version=4.10.6
review_packet_sha256=7a970e656df08ea67b87e0c6b501d2258ee592759e0415995f42dbf2a4dcdcab
stream_runner_sha256=3cfd6f2f9eee50a6e392ac56bd68bb3adc0bd9789816575d09a2aa252fe7934b
normalizer_sha256=8663b02839e591260ba30cd1e00612eb718a14db3fd2c004fcaf0177711b509e
evidence_dir=$project_root/.review-evidence

[ -d "$global_skill_root" ] || die "global core Git identity is unavailable"
git -C "$global_skill_root" rev-parse --verify --quiet HEAD^{commit} >/dev/null 2>&1 || die "global core Git identity is unavailable"
[ "$(git -C "$global_skill_root" show HEAD:VERSION)" = "$global_core_version" ] || die "global core version mismatch"

work_dir=$(mktemp -d /tmp/codebuddy-verify.XXXXXX) || die "temporary directory creation failed"
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM
isolated_core=$work_dir/global-core
mkdir "$isolated_core" || die "isolated core directory creation failed"
git -C "$global_skill_root" show HEAD:scripts/review_packet.py > "$isolated_core/review_packet.py" || die "isolated packet copy failed"
git -C "$global_skill_root" show HEAD:scripts/codebuddy_stream_runner.py > "$isolated_core/codebuddy_stream_runner.py" || die "isolated runner copy failed"
git -C "$global_skill_root" show HEAD:scripts/normalize_review_result.py > "$isolated_core/normalize_review_result.py" || die "isolated normalizer copy failed"
review_packet=$isolated_core/review_packet.py
stream_runner=$isolated_core/codebuddy_stream_runner.py
normalizer=$isolated_core/normalize_review_result.py
[ "$(shasum -a 256 "$review_packet" | awk '{print $1}')" = "$review_packet_sha256" ] || die "review packet core hash mismatch"
[ "$(shasum -a 256 "$stream_runner" | awk '{print $1}')" = "$stream_runner_sha256" ] || die "stream runner core hash mismatch"
[ "$(shasum -a 256 "$normalizer" | awk '{print $1}')" = "$normalizer_sha256" ] || die "result normalizer core hash mismatch"
file_manifest=$work_dir/files
: > "$file_manifest"
file_candidates=$work_dir/candidates
: > "$file_candidates"

validate_utf8() {
    if ! printf '%s' "$1" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1; then
        die "argument is not UTF-8"
    fi
}

validate_relative_path() {
    value=$1
    validate_utf8 "$value"
    if printf '%s' "$value" | LC_ALL=C grep -q '[[:cntrl:]]'; then
        die "path must not contain control characters"
    fi
    case "$value" in ''|/*|*/) die "path must be a non-empty relative project path" ;; esac
    lowered=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "/$lowered/" in */runtime/*|*/vault/*|*/rebo-vault/*|*/home/*|*/.obsidian/*) die "runtime, vault, and home paths are not allowed" ;; esac
    remainder=$value
    current=$project_root
    while :; do
        component=${remainder%%/*}
        if [ "$component" = "$remainder" ]; then remainder=''; else remainder=${remainder#*/}; fi
        case "$component" in ''|.|..) die "path traversal is not allowed" ;; esac
        current=$current/$component
        [ ! -L "$current" ] || die "symlinks are not allowed"
        [ -n "$remainder" ] || break
    done
}

validate_source_file() {
    validate_relative_path "$1"
    lowered=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case "$lowered" in
        .env|.env.*|reports/*|.review-evidence/*|.git|.git/*|runtime/*|vault/*|rebo-vault/*|home/*|.obsidian/*)
            die "source path is not reviewable"
            ;;
    esac
    path=$project_root/$1
    [ -f "$path" ] || die "source must be an existing regular file"
    git -C "$project_root" ls-files --error-unmatch -- "$1" >/dev/null 2>&1 || die "source must be tracked"
    git -C "$project_root" diff --quiet "$base_ref..$head_ref" -- "$1" && die "source must be changed in selected range"
    /usr/bin/python3 -c 'import sys; sys.stdin.buffer.read().decode("utf-8")' < "$path" >/dev/null 2>&1 || die "source must be UTF-8 text"
    if LC_ALL=C grep -Eiq -- '-----BEGIN [^-]*PRIVATE KEY[^-]*-----' "$path"; then
        die "source contains private key material"
    fi
}

validate_report_path() {
    validate_relative_path "$1"
    case "$1" in reports/*/*|reports/|reports/*) ;; *) die "report path must be under reports" ;; esac
    case "$1" in reports/*/*|reports/) die "report path must be directly under reports" ;; esac
    reports_dir=$project_root/reports
    if [ -e "$reports_dir" ] && { [ ! -d "$reports_dir" ] || [ -L "$reports_dir" ]; }; then
        die "reports directory is unsafe"
    fi
    [ -d "$reports_dir" ] || mkdir "$reports_dir" || die "reports directory creation failed"
    [ ! -L "$reports_dir" ] || die "reports directory is unsafe"
    path=$reports_dir/${1#reports/}
    [ ! -e "$path" ] || die "report path already exists"
}

validate_safe_text() {
    if printf '%s' "$1" | LC_ALL=C grep -Eiq 'token|key|password|ticket_capability'; then
        die "$2 contains sensitive material"
    fi
}

check_selected_diff() {
    while IFS= read -r source_file; do
        case "$source_file" in
            *.patch)
                # Unified-diff blank context is represented by one required
                # leading space, which git diff --check misreports here.
                ;;
            *)
                git -C "$project_root" diff --check "$base_ref..$head_ref" -- "$source_file" >/dev/null 2>&1 || return 1
                ;;
        esac
    done < "$file_manifest"
}

prompt=''
report=''
base_ref='HEAD~1'
head_ref='HEAD'
prompt_seen=0
report_seen=0
file_count=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prompt) [ "$#" -ge 2 ] || die "--prompt requires a value"; [ "$prompt_seen" -eq 0 ] || die "--prompt may only be supplied once"; prompt=$2; prompt_seen=1; shift 2 ;;
        --report) [ "$#" -ge 2 ] || die "--report requires a value"; [ "$report_seen" -eq 0 ] || die "--report may only be supplied once"; report=$2; report_seen=1; shift 2 ;;
        --file) [ "$#" -ge 2 ] || die "--file requires a value"; printf '%s\n' "$2" >> "$file_candidates"; file_count=$((file_count + 1)); shift 2 ;;
        --base-ref) [ "$#" -ge 2 ] || die "--base-ref requires a value"; base_ref=$2; shift 2 ;;
        --head-ref) [ "$#" -ge 2 ] || die "--head-ref requires a value"; head_ref=$2; shift 2 ;;
        *) die "unknown argument" ;;
    esac
done
[ "$prompt_seen" -eq 1 ] && [ -n "$prompt" ] || die "--prompt is required and must be non-empty"
[ "$report_seen" -eq 1 ] || die "--report is required"
[ "$file_count" -gt 0 ] || die "at least one --file is required"
validate_utf8 "$prompt"
validate_safe_text "$prompt" "prompt"
validate_ref() {
    value=$1
    validate_utf8 "$value"
    case "$value" in ''|-*) die "Git ref is invalid" ;; esac
    if printf '%s' "$value" | LC_ALL=C grep -q '[[:cntrl:]]'; then
        die "Git ref must not contain control characters"
    fi
    git -C "$project_root" rev-parse --verify --quiet "$value^{commit}" >/dev/null 2>&1 || die "Git ref is not a commit"
}
validate_ref "$base_ref"
validate_ref "$head_ref"
while IFS= read -r source_file; do
    validate_source_file "$source_file"
    printf '%s\n' "$source_file" >> "$file_manifest"
done < "$file_candidates"
validate_report_path "$report"
report_path=$project_root/$report
report_parent=${report_path%/*}

write_report() {
    verdict=$1
    reason_code=$2
    fingerprint=${3-}
    report_tmp=$(mktemp "$reports_dir/.codebuddy-verify.XXXXXX") || die "report_publication_failed"
    if ! {
        printf '%s\n' '# CodeBuddy verification report'
        printf '%s\n' 'Verifier: CodeBuddy / GLM 5.2 / V4.10.6'
        printf '%s\n' 'Model: glm-5.2'
        printf '%s\n' "Verdict: $verdict"
        printf '%s\n' "Reason code: $reason_code"
        [ -z "$fingerprint" ] || printf '%s\n' "Packet ID: review-packets/$fingerprint"
        printf '%s\n' 'Scope:'
        while IFS= read -r source_file; do printf '%s\n' "- $source_file"; done < "$file_manifest"
    } > "$report_tmp"; then rm -f "$report_tmp"; die "report_publication_failed"; fi
    ln "$report_tmp" "$report_path" || { rm -f "$report_tmp"; die "report_already_exists"; }
    rm -f "$report_tmp"
}

set -- build --root "$project_root" --report-dir "$evidence_dir" --provider codebuddy --model glm-5.2 --runner-version "$stream_runner_sha256" --prompt-hash "$(printf '%s' "$prompt" | shasum -a 256 | awk '{print $1}')" --tier review-lite --focus "$prompt" --invariant 'Review only the immutable packet and emit one schema-valid verdict.' --scope-mode filter --base-ref "$base_ref" --head-ref "$head_ref" --max-packet-bytes 32768
while IFS= read -r source_file; do set -- "$@" --allow-file "$source_file"; done < "$file_manifest"
packet_output=$(/usr/bin/python3 "$review_packet" "$@" 2>"$work_dir/packet-error") || {
    reason_code=$(/usr/bin/python3 - "$work_dir/packet-error" <<'PY'
import json, sys
try: print(json.loads(open(sys.argv[1], encoding='utf-8').read())['reason_code'])
except Exception: print('packet_preflight_failed')
PY
)
    write_report BLOCKED "$reason_code" ''
    die "$reason_code"
}
fingerprint=$(printf '%s' "$packet_output" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])') || die "packet_protocol_failure"
packet_dir=$evidence_dir/review-packets/$fingerprint
packet_bytes=$(printf '%s' "$packet_output" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["packet_bytes"])') || die "packet_protocol_failure"
receipt_file=$evidence_dir/receipts/$fingerprint.json

check_selected_diff || { write_report BLOCKED local_diff_check_failed "$fingerprint"; die "local_diff_check_failed"; }
/usr/bin/python3 "$review_packet" claim --report-dir "$evidence_dir" --fingerprint "$fingerprint" >/dev/null 2>&1 || { write_report BLOCKED already_claimed "$fingerprint"; die "already_claimed"; }

node_executable=$(PATH=$original_path command -v node 2>/dev/null || true)
case "$node_executable" in /opt/homebrew/bin/node|/usr/local/bin/node|/usr/bin/node) node_runtime_dir=${node_executable%/node} ;; *) write_report BLOCKED approved_node_unavailable "$fingerprint"; die "approved_node_unavailable" ;; esac
codebuddy_executable=/Users/qinxu/.local/bin/codebuddy
[ -x "$codebuddy_executable" ] || { write_report BLOCKED codebuddy_unavailable "$fingerprint"; die "codebuddy_unavailable"; }

printf '%s\n' '{"mcpServers":{}}' > "$work_dir/empty-mcp.json"
request_file=$work_dir/review-request.md
{
    printf '%s\n\n' '# V4 immutable review packet'
    printf '%s\n' '## review-brief.md'; cat "$packet_dir/review-brief.md"
    printf '%s\n' '## review-manifest.json'; cat "$packet_dir/review-manifest.json"
    printf '%s\n' '## review.diff'; cat "$packet_dir/review.diff"
} > "$request_file"
result_file=$work_dir/result.json
events_file=$work_dir/events.jsonl
state_file=$work_dir/state.json
stderr_file=$work_dir/stderr.txt
verifier_timeout_seconds=300
if env -i PATH="$node_runtime_dir:$PATH" LC_ALL=C /usr/bin/python3 "$stream_runner" \
    --request-file "$request_file" --result-file "$result_file" --events-file "$events_file" --state-file "$state_file" --stderr-file "$stderr_file" --timeout-seconds "$verifier_timeout_seconds" --model glm-5.2 -- \
    "$codebuddy_executable" -p --model glm-5.2 --effort medium --append-system-prompt 'Read only the immutable packet. Do not use tools or explain reasoning. Return exactly one V4 JSON verdict.' --tools '' --no-session-persistence --strict-mcp-config --mcp-config "$work_dir/empty-mcp.json" --permission-mode dontAsk --max-turns 1 --setting-sources '' --settings '{"disableAllHooks":true}' --output-format stream-json --json-schema "$packet_dir/review-schema.json"
then
    runner_status=0
else
    runner_status=$?
fi
state_values=$(/usr/bin/python3 - "$state_file" <<'PY'
import json, sys
try:
 data=json.load(open(sys.argv[1], encoding='utf-8')); print(f"{int(data.get('event_count', 0))} {'yes' if data.get('final_result_seen', False) else 'no'} {data.get('reason_code') or 'transport_failure'}")
except Exception: print('0 no transport_failure')
PY
)
set -- $state_values
event_count=$1
final_seen=$2
transport_reason=$3
if [ "$runner_status" -ne 0 ]; then
    case "$transport_reason" in result_not_json|result_error|protocol_error) failure_class=P ;; *) failure_class=T ;; esac
    /usr/bin/python3 "$review_packet" receipt --receipt-file "$receipt_file" --fingerprint "$fingerprint" --provider codebuddy --model glm-5.2 --packet-dir "$packet_dir" --packet-bytes "$packet_bytes" --preflight pass --provider-started yes --event-count "$event_count" --final-result-seen "$final_seen" --verdict-parse not_attempted --failure-class "$failure_class" --model-verdict '' --reason-code "$transport_reason" >/dev/null 2>&1 || true
    write_report BLOCKED "$transport_reason" "$fingerprint"
    die "$transport_reason"
fi
set -- validate-verdict --result-file "$result_file" --manifest-file "$packet_dir/review-manifest.json"
validated=$(/usr/bin/python3 "$review_packet" "$@" 2>"$work_dir/validate-error") || {
    cat "$work_dir/validate-error" >&2
    validation_reason=$(/usr/bin/python3 - "$work_dir/validate-error" <<'PY'
import json, sys
try:
    value = json.loads(open(sys.argv[1], encoding="utf-8").read())
    reason = value.get("reason_code")
    print(reason if isinstance(reason, str) and reason else "protocol_failure")
except Exception:
    print("protocol_failure")
PY
)
    /usr/bin/python3 "$review_packet" receipt --receipt-file "$receipt_file" --fingerprint "$fingerprint" --provider codebuddy --model glm-5.2 --packet-dir "$packet_dir" --packet-bytes "$packet_bytes" --preflight pass --provider-started yes --event-count "$event_count" --final-result-seen "$final_seen" --verdict-parse fail --failure-class P --model-verdict '' --reason-code "$validation_reason" >/dev/null 2>&1 || true
    write_report BLOCKED "$validation_reason" "$fingerprint"
    die "$validation_reason"
}
verdict=$(printf '%s' "$validated" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')
if [ "$verdict" != "PASS" ]; then
    /usr/bin/python3 "$review_packet" receipt --receipt-file "$receipt_file" --fingerprint "$fingerprint" --provider codebuddy --model glm-5.2 --packet-dir "$packet_dir" --packet-bytes "$packet_bytes" --preflight pass --provider-started yes --event-count "$event_count" --final-result-seen "$final_seen" --verdict-parse pass --failure-class M --model-verdict "$verdict" --reason-code verdict_not_pass >/dev/null 2>&1 || { write_report BLOCKED receipt_failure "$fingerprint"; die "receipt_failure"; }
    write_report "$verdict" verdict_not_pass "$fingerprint"
    die "verdict_not_pass"
fi
/usr/bin/python3 "$review_packet" receipt --receipt-file "$receipt_file" --fingerprint "$fingerprint" --provider codebuddy --model glm-5.2 --packet-dir "$packet_dir" --packet-bytes "$packet_bytes" --preflight pass --provider-started yes --event-count "$event_count" --final-result-seen "$final_seen" --verdict-parse pass --failure-class none --model-verdict "$verdict" --reason-code none >/dev/null 2>&1 || { write_report BLOCKED receipt_failure "$fingerprint"; die "receipt_failure"; }
write_report "$verdict" none "$fingerprint"
