# Local execution adapter

This directory is an installable runner for bounded specialist tasks. Its tests use fake executables. It is not a working live multi-model system, and installing it does not call a model or change an existing client configuration.

Generation bindings:

| Alias | Binding basis | Command |
|---|---|---|
| `codex` | Help text for 0.164.0 | `exec --json --color never --ephemeral -s read-only -C WORKSPACE -`, with stdin and `-c check_for_update_on_startup=false` |
| `codex2` | Same help text as `codex` | Same argv as `codex`. A separate `CODEX_HOME` comes only from private config. That does not prove a different model or account. |
| `kimi` | Help text for 2.5.2 | `-m` alias when authorized, `-p` prompt, `--output-format stream-json` |
| `hermes` | Gateway allow/deny checked against Hermes Agent v0.23.0 | `chat --query-file - --oneshot --format stream-json --in WORKSPACE --max-turns LIMIT` |

`cursor` stays outside this runner. `dsh` has no evidenced launch contract, so its argv is null. `openclaw` is paused, not globally unavailable. `claude` is disabled and is not probed.

The Hermes command above is the default route. It does not add `-m`, `--provider`, `-p`, `-z`, or `--usage-file`. Those first three selectors are added only when the approval explicitly authorizes them. `--oneshot` stays on `chat` and is not replaced by top-level `-z`. `chat --help` can still list `--run-budget` while the gateway client rejects it, so help text alone is not the executable contract. This package records v0.23.0 as the client version that binding was checked against. It does not probe the installed CLI, and a receipt leaves the observed client version `UNKNOWN`.

`--run-budget` is not sent. An approval that still contains `run_budget_seconds` is rejected with `hermes_run_budget_contract_retired` and does not start a process. The field is not ignored, and it is not written into the user's Hermes configuration.

Read-only mode limits model-generated shell writes. It is not full filesystem or network isolation. Ephemeral mode avoids session-file persistence and does not prove that logs or network activity are absent. `process_timeout_seconds` is only a client-process deadline. Killing or closing the Hermes CLI does not prove the gateway-side turn has stopped, and it does not prove that remote work or billing has stopped. Hermes receipts mark that termination semantic as `incomplete`. The runner does not use Hermes top-level single-query bypass mode or its usage-file option.

Missing model, usage, and price fields stay `UNKNOWN`. They are not inferred from an alias or a banner. A metering gap does not by itself forbid a run. Missing approved iteration values are not invented. Hermes generation stays unbound until an approval supplies `max_turns` and omits `run_budget_seconds`.

Package version: `0.1.4`. This package is not a live Hermes pathway result and not an independent acceptance.

## Changelog

### 0.1.4

- Hermes argv no longer includes `--run-budget`. The default route is `hermes chat --query-file - --oneshot --format stream-json --in WORKSPACE --max-turns LIMIT`.
- Approvals that still include `run_budget_seconds` are refused, so a package written for the previous Hermes contract cannot be replayed as unchanged. The adapter does not edit the user's Hermes configuration.
- The Hermes binding follows gateway allow/deny behavior checked against client v0.23.0. Help text that still lists `--run-budget` is not treated as the current contract.
- `process_timeout_seconds` remains a client-process deadline only. Closing or killing that process does not prove the gateway-side turn has stopped.

### 0.1.3

- Codex and Codex2 entries may set `skip_git_repo_check` to true. Only then is `--skip-git-repo-check` added. The default is false, so the flag stays off. The receipt records that boolean. Other entries cannot opt into this flag.

### 0.1.2

- Each enabled entry may declare an `env` map. The child receives `PATH` plus those keys only, never the full parent environment. Keys that look like secrets are rejected unless the value is explicitly marked `secret`. The receipt lists injected key names and not their values.
- `skills_dir` must be an existing directory after `realpath`, inside the configured `instance_root`. An empty directory is accepted. The receipt records `skills_dir_empty`. An empty skills directory is still not a sandbox.

### 0.1.1

- Timeout cleanup records the spawned pid, kills the process group, and blocks replay when the write outcome is unknown.
- Workspace, binary, and configuration-home paths are compared with `realpath`.

## Install without cloning this repository

Copy this directory, or the versioned zip, to the machine that is separately authorized to configure it. Python 3.14 does not need setuptools and this install does not download anything:

```bash
python3 -m venv .venv
site=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$PWD" > "$site/execution-adapter.pth"
install -m 0755 bin/execution-adapter .venv/bin/execution-adapter
.venv/bin/execution-adapter hash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/execution-adapter smoke
```

If that Python already has setuptools, `.venv/bin/python -m pip install .` remains optional. Do not modify an older installed copy; install this version beside it.

`hash` prints the source hash recorded in `SOURCE_HASH.txt`. `smoke` and the unit tests do not call a model.

Private enablement stays outside this package. Copy `config.template.json`, set binaries and homes only in that private file, and keep it out of the public repository. Do not commit account names, tokens, workspace paths, or numeric limits.

## Run

```bash
execution-adapter run --config /path/to/private-config.json --job job.json --approval approval.json
```

The job cannot change the binary, config path, or permissions. A recommendation is not an approval. The command runs one process and does not retry or fall back to another client.

## Rollback

1. Stop invoking `execution-adapter`.
2. Remove the virtualenv and the copied directory.
3. Leave or delete the private config outside this package. This package does not contain it and does not restore or overwrite client settings.
4. Do not treat a stopped local process as proof that a remote charge ended. `process_timeout_seconds` only stops the client process. Killing or closing the Hermes CLI does not prove the gateway-side turn has stopped.
5. This package does not change paused local inference configuration.

## Verify the archive

The hash covers every file in this directory except `SOURCE_HASH.txt` and bytecode caches, in sorted relative-path order. Each entry is the path, a NUL byte, the file bytes, and another NUL byte.
