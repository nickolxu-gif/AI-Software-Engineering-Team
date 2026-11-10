# Local execution adapter

This directory is an installable runner for bounded specialist tasks. Its tests use fake executables. It is not a working live multi-model system, and installing it does not call a model or change an existing client configuration.

Help-verified generation bindings:

| Alias | Declared help version | Binding |
|---|---|---|
| `codex` | 0.164.0 | `exec --json --color never --ephemeral -s read-only -C WORKSPACE -`, with stdin and `-c check_for_update_on_startup=false` |
| `codex2` | 0.164.0 | Same argv as `codex`. A separate `CODEX_HOME` comes only from private config. That does not prove a different model or account. |
| `kimi` | 2.5.2 | `-m` alias when authorized, `-p` prompt, `--output-format stream-json` |
| `hermes` | 0.22.7 | `chat --query-file - --oneshot --format stream-json --in WORKSPACE`, plus approved `--max-turns` and `--run-budget` |

`cursor` stays outside this runner. `dsh` has no evidenced launch contract, so its argv is null. `openclaw` is paused, not globally unavailable. `claude` is disabled and is not probed.

Read-only mode limits model-generated shell writes. It is not full filesystem or network isolation. Ephemeral mode avoids session-file persistence and does not prove that logs or network activity are absent. A killed client does not prove server-side billing stopped. Hermes `--run-budget` is wall-clock handling, not a cost ceiling. The runner does not use Hermes top-level single-query bypass mode or its usage-file option.

Missing model, usage, and price fields stay `UNKNOWN`. They are not inferred from an alias or a banner. A metering gap does not by itself forbid a run. Missing approved iteration or time values are not invented; Hermes generation stays unbound until an approval package supplies them.

Package version: `0.1.2`.

## Changelog

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
4. Do not treat a stopped local process as proof that a remote charge ended. A Hermes chat process can detach from a gateway turn.
5. This package does not change paused local inference configuration.

## Verify the archive

The hash covers every file in this directory except `SOURCE_HASH.txt` and bytecode caches, in sorted relative-path order. Each entry is the path, a NUL byte, the file bytes, and another NUL byte.
