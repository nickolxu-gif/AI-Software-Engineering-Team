#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/github-branch-protection.sh --owner <OWNER> --repo <REPO> [--branch <BRANCH>] [--required-reviews <N>] [--status-checks]

Apply/refresh basic branch protection settings for a repository branch.
Options:
  --owner <OWNER>          GitHub owner/organization (required)
  --repo <REPO>            Repository name (required)
  --branch <BRANCH>        Branch name (default: main)
  --required-reviews <N>   Required approving reviews (default: 1)
  --status-checks           Add strict=true status checks (requires workflows)
  --dry-run                 Print desired payload and exit without applying
USAGE
}

OWNER=""
REPO=""
BRANCH="main"
REQUIRED_REVIEWS=1
STRICT_STATUS_CHECKS="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)
      OWNER="$2"; shift 2; ;;
    --repo)
      REPO="$2"; shift 2; ;;
    --branch)
      BRANCH="$2"; shift 2; ;;
    --required-reviews)
      REQUIRED_REVIEWS="$2"; shift 2; ;;
    --status-checks)
      STRICT_STATUS_CHECKS="true"; shift; ;;
    --dry-run)
      DRY_RUN="true"; shift; ;;
    -h|--help)
      usage; exit 0; ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$OWNER" || -z "$REPO" ]]; then
  echo "--owner and --repo are required" >&2
  usage
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI not found" >&2
  exit 2
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated" >&2
  exit 2
fi

PAYLOAD=$(cat <<JSON
{
  "required_pull_request_reviews": {
    "required_approving_review_count": ${REQUIRED_REVIEWS},
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false
  },
  "required_status_checks": {
    "strict": ${STRICT_STATUS_CHECKS},
    "contexts": []
  },
  "enforce_admins": false,
  "restrictions": null,
  "required_conversation_resolution": true
}
JSON
)

echo "Target: https://github.com/${OWNER}/${REPO}.git#${BRANCH}"
echo "Desired payload:" >&2
echo "$PAYLOAD" | sed -n '1,120p'

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry-run completed."
  exit 0
fi

set +e
gh api -X PUT "/repos/${OWNER}/${REPO}/branches/${BRANCH}/protection" --input - <<<"$PAYLOAD"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  echo "Branch protection update failed, check API response above." >&2
  exit 1
fi

echo "Branch protection updated successfully."

gh api "/repos/${OWNER}/${REPO}/branches/${BRANCH}/protection"
