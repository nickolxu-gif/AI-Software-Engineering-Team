#!/bin/sh
set -eu

failures=0

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    failures=$((failures + 1))
}

if script_dir=$(CDPATH= cd "$(dirname "$0")" 2>/dev/null && pwd -P); then
    :
else
    printf 'ERROR: cannot resolve the script directory from %s\n' "$0" >&2
    exit 1
fi

if repo_root=$(CDPATH= cd "$script_dir/.." 2>/dev/null && pwd -P); then
    :
else
    printf 'ERROR: cannot resolve the repository root from %s/..\n' "$script_dir" >&2
    exit 1
fi

is_repo_root=0
if git_toplevel=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null); then
    if resolved_toplevel=$(CDPATH= cd "$git_toplevel" 2>/dev/null && pwd -P); then
        if [ "$resolved_toplevel" = "$repo_root" ]; then
            is_repo_root=1
        else
            fail "script-derived root $repo_root is not the Git top-level $resolved_toplevel"
        fi
    else
        fail "cannot resolve Git top-level directory: $git_toplevel"
    fi
else
    fail "$repo_root is not a Git repository"
fi

if [ "$is_repo_root" -eq 1 ]; then
    if current_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD 2>/dev/null); then
        if [ "$current_branch" != main ]; then
            fail "root workspace must be on branch main (current: $current_branch)"
        fi
    else
        fail "cannot determine the current branch; the root workspace may have a detached HEAD"
    fi
else
    fail "cannot verify current root branch is main without a valid Git repository root"
fi

if [ "$is_repo_root" -eq 1 ]; then
    if ! git -C "$repo_root" show-ref --verify --quiet refs/heads/main; then
        fail "local branch main does not exist"
    fi
else
    fail "cannot verify local branch main exists without a valid Git repository root"
fi

for required_file in \
    AGENTS.md \
    handoff.md \
    GIT_WORKFLOW.md \
    CODEX_AGENT_DISPATCH_PROTOCOL.md \
    AGENT_ROLE_AND_MODEL_MATRIX.md \
    SOFTWARE_ENGINEERING_WORKFLOW.md \
    PROJECT_SPEC.md \
    REVIEW_ITERATION_2026-08-08.md \
    .gitignore \
    .gitattributes
do
    if [ ! -s "$repo_root/$required_file" ]; then
        fail "required file is missing or empty: $required_file"
    fi
done

if [ "$is_repo_root" -eq 1 ]; then
    if ! git -C "$repo_root" check-ignore -q -- .worktrees/; then
        fail "/.worktrees/ is not ignored by Git"
    fi
else
    fail "cannot verify /.worktrees/ ignore status without a valid Git repository root"
fi

if [ "$is_repo_root" -eq 1 ]; then
    if status_output=$(git -C "$repo_root" status --porcelain 2>/dev/null); then
        if [ -n "$status_output" ]; then
            fail "root workspace is not clean"
            printf '%s\n' "$status_output" >&2
        fi
    else
        fail "cannot inspect root workspace status"
    fi
else
    fail "cannot verify root workspace cleanliness without a valid Git repository root"
fi

printf '%s\n' 'Git worktree list:'
if [ "$is_repo_root" -eq 1 ]; then
    if ! git -C "$repo_root" worktree list; then
        fail "git worktree list failed"
    fi
else
    fail "cannot print git worktree list without a valid Git repository root"
fi

if [ "$failures" -ne 0 ]; then
    printf 'Repository health: FAIL (%s check(s) failed)\n' "$failures" >&2
    exit 1
fi

printf '%s\n' 'Repository health: PASS'
