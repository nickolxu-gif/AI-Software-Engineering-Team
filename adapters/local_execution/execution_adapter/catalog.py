"""Resource catalog. Generation argv is null unless a help contract evidences it."""

SCHEMA_VERSION = "2026-11-08"

# Help-verified coding-client template, version 0.164.0.
# Optional selectors are added only when an approval explicitly authorizes them.
CODEX_ARGV_TEMPLATE = [
    "{executable}",
    "-c",
    "check_for_update_on_startup=false",
    "exec",
    "--json",
    "--color",
    "never",
    "--ephemeral",
    "-s",
    "read-only",
    "-C",
    "{approved_workspace}",
    "-",
]
CODEX_VERSION = "0.164.0"
CODEX_OPTIONAL_FLAGS = ("-m", "-p", "-o", "--output-schema")
CODEX_FORBIDDEN_FLAGS = (
    "--yolo",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--ignore-rules",
    "--approve-for-me",
)

# Help-verified multiprovider chat contract, version 0.22.7.
# Numeric limits stay placeholders until an approved package supplies them.
HERMES_ARGV_TEMPLATE = [
    "{executable}",
    "chat",
    "--query-file",
    "-",
    "--oneshot",
    "--format",
    "stream-json",
    "--in",
    "{approved_workspace}",
    "--max-turns",
    "{approved_iteration_limit}",
    "--run-budget",
    "{approved_seconds}",
]
HERMES_VERSION = "0.22.7"
HERMES_OPTIONAL_FLAGS = ("-p", "--profile", "-m", "--provider")
HERMES_FORBIDDEN_FLAGS = ("-z", "--usage-file", "--yolo")

# User-supplied help for the long-context client, version 2.5.2.
KIMI_ARGV_TEMPLATE = [
    "{executable}",
    "-m",
    "{model_alias}",
    "-p",
    "{prompt}",
    "--output-format",
    "stream-json",
]
KIMI_VERSION = "2.5.2"
KIMI_OPTIONAL_FLAGS = ("--skills-dir", "--agent-file", "--model", "--prompt")
KIMI_FORBIDDEN_FLAGS = ("-z", "--usage-file")

RESOURCES = {
    "codex": {
        "kind": "codex_exec",
        "supported_version_declared": CODEX_VERSION,
        "generation_binding": "help_verified",
        "argv_template": CODEX_ARGV_TEMPLATE,
        "stdin": "approved_prompt",
        "configuration_home_env": None,
        "local_runner": True,
    },
    "codex2": {
        "kind": "codex_exec",
        "supported_version_declared": CODEX_VERSION,
        "generation_binding": "help_verified",
        "argv_template": CODEX_ARGV_TEMPLATE,
        "stdin": "approved_prompt",
        "configuration_home_env": "CODEX_HOME",
        "same_argv_contract_as": "codex",
        "distinct_model_or_account": False,
        "local_runner": True,
    },
    "kimi": {
        "kind": "kimi_cli",
        "supported_version_declared": KIMI_VERSION,
        "generation_binding": "help_verified",
        "argv_template": KIMI_ARGV_TEMPLATE,
        "stdin": "unused",
        "configuration_home_env": None,
        "local_runner": True,
    },
    "hermes": {
        "kind": "hermes_chat",
        "supported_version_declared": HERMES_VERSION,
        "generation_binding": "help_verified",
        "argv_template": HERMES_ARGV_TEMPLATE,
        "stdin": "approved_prompt",
        "configuration_home_env": None,
        "local_runner": True,
    },
    "cursor": {
        "kind": "external_orchestrator",
        "supported_version_declared": None,
        "generation_binding": None,
        "argv_template": None,
        "local_runner": False,
    },
    "dsh": {
        "kind": "unsupported_config_needed",
        "supported_version_declared": None,
        "generation_binding": None,
        "argv_template": None,
        "launch_contract": "not_evidenced",
        "local_runner": False,
    },
    "openclaw": {
        "kind": "paused",
        "supported_version_declared": None,
        "generation_binding": None,
        "argv_template": None,
        "globally_unavailable": False,
        "local_runner": False,
    },
    "claude": {
        "kind": "disabled",
        "supported_version_declared": None,
        "generation_binding": None,
        "argv_template": None,
        "probe": False,
        "local_runner": False,
    },
}
