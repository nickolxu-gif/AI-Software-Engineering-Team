#!/bin/sh
set -eu

usage() {
    printf 'Usage: %s <dispatch-id> <agent> <slug>\n' "${0##*/}" >&2
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

validate_component() {
    arg_value=$1
    arg_label=$2

    [ -n "$arg_value" ] || die "$arg_label must not be empty"

    case "$arg_value" in
        */*)
            die "$arg_label must not contain '/'"
            ;;
    esac

    case "$arg_value" in
        *..*)
            die "$arg_label must not contain '..' (path traversal is forbidden)"
            ;;
    esac

    case "$arg_value" in
        [A-Za-z0-9]*)
            ;;
        *)
            die "$arg_label must start with an ASCII letter or digit"
            ;;
    esac

    case "$arg_value" in
        *[!A-Za-z0-9._-]*)
            die "$arg_label must match [A-Za-z0-9][A-Za-z0-9._-]*"
            ;;
    esac
}

if [ "$#" -ne 3 ]; then
    printf 'ERROR: expected exactly 3 arguments; received %s\n' "$#" >&2
    usage
    exit 2
fi

dispatch_id=$1
agent=$2
slug=$3

validate_component "$dispatch_id" 'dispatch-id'
validate_component "$agent" 'agent'
validate_component "$slug" 'slug'

if script_dir=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd -P); then
    :
else
    die "cannot resolve the script directory from $0"
fi

if repo_root=$(CDPATH= cd "$script_dir/.." 2>/dev/null && pwd -P); then
    :
else
    die "cannot resolve the repository root from $script_dir/.."
fi

if git_toplevel=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null); then
    :
else
    die "$repo_root is not a Git repository"
fi

if git_toplevel=$(CDPATH= cd "$git_toplevel" 2>/dev/null && pwd -P); then
    :
else
    die "cannot resolve Git top-level directory"
fi

[ "$git_toplevel" = "$repo_root" ] || \
    die "script-derived root $repo_root is not the Git top-level $git_toplevel"

if git_common_raw=$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null); then
    :
else
    die "cannot determine the Git common directory"
fi

case "$git_common_raw" in
    /*)
        git_common_candidate=$git_common_raw
        ;;
    *)
        git_common_candidate=$repo_root/$git_common_raw
        ;;
esac

if git_common_dir=$(CDPATH= cd "$git_common_candidate" 2>/dev/null && pwd -P); then
    :
else
    die "cannot resolve Git common directory: $git_common_candidate"
fi

# team/control.db is the stable control-plane marker.  Task 8 stores the
# current SQLite database at team/runtime/team.db, so recognize both paths
# during the staged MVP 0 rollout.  This guard must remain before mutations.
control_db=$git_common_dir/team/control.db
runtime_db=$git_common_dir/team/runtime/team.db
if [ -e "$control_db" ] || [ -e "$runtime_db" ]; then
    die "MVP 0 control plane is initialized; legacy Worktree creation is prohibited; use scripts/team-control start"
fi

if current_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD 2>/dev/null); then
    :
else
    die "cannot determine the current branch; the root workspace may have a detached HEAD"
fi

[ "$current_branch" = main ] || \
    die "root workspace must be on branch main (current: $current_branch)"

git -C "$repo_root" show-ref --verify --quiet refs/heads/main || \
    die "local branch main does not exist"

if status_output=$(git -C "$repo_root" status --porcelain 2>/dev/null); then
    :
else
    die "cannot inspect root workspace status"
fi

if [ -n "$status_output" ]; then
    printf '%s\n' 'ERROR: root workspace is not clean:' >&2
    printf '%s\n' "$status_output" >&2
    exit 1
fi

git -C "$repo_root" check-ignore -q -- .worktrees/ || \
    die "/.worktrees/ is not ignored by Git"

branch="agent/$agent/$dispatch_id-$slug"
worktree_root="$repo_root/.worktrees"
path="$worktree_root/$dispatch_id-$agent-$slug"

git check-ref-format --branch "$branch" >/dev/null 2>&1 || \
    die "constructed branch name is not a valid Git branch: $branch"

case "$path" in
    "$worktree_root"/*)
        ;;
    *)
        die "constructed worktree path escapes $worktree_root: $path"
        ;;
esac

[ "$path" != "$worktree_root" ] || \
    die "worktree path must be strictly below $worktree_root"

if [ -L "$worktree_root" ]; then
    die "worktree root must not be a symbolic link: $worktree_root"
fi

if [ -e "$worktree_root" ] && [ ! -d "$worktree_root" ]; then
    die "worktree root exists but is not a directory: $worktree_root"
fi

if git -C "$repo_root" show-ref --verify --quiet "refs/heads/$branch"; then
    die "branch already exists: $branch"
else
    show_ref_status=$?
    [ "$show_ref_status" -eq 1 ] || \
        die "failed to check whether branch exists: $branch"
fi

if [ -e "$path" ] || [ -L "$path" ]; then
    die "worktree path already exists: $path"
fi

if base_sha=$(git -C "$repo_root" rev-parse main 2>/dev/null); then
    :
else
    die "cannot resolve main to a base commit"
fi

mkdir -p "$worktree_root" || die "cannot create worktree root: $worktree_root"

if [ -L "$worktree_root" ]; then
    die "worktree root became a symbolic link: $worktree_root"
fi

if resolved_worktree_root=$(CDPATH= cd "$worktree_root" 2>/dev/null && pwd -P); then
    :
else
    die "cannot resolve worktree root after creation: $worktree_root"
fi

[ "$resolved_worktree_root" = "$worktree_root" ] || \
    die "resolved worktree root is outside the repository: $resolved_worktree_root"

git -C "$repo_root" worktree add -b "$branch" "$path" "$base_sha" || \
    die "git worktree add failed for $path"

printf 'WORKTREE_PATH=%s\n' "$path"
printf 'BRANCH=%s\n' "$branch"
printf 'BASE_SHA=%s\n' "$base_sha"
