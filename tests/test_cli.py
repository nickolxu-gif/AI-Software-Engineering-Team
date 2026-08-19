import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from team_control.git_context import RepoContext
from team_control.intents import IntentService
from team_control.service import ControlPlane
from team_control.store import ControlStore
from team_control.errors import GitStateError
from tests.helpers import make_repo, run


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(repo, *args, cwd=None, check=True):
    return run(
        [
            sys.executable,
            "-m",
            "team_control",
            "--repo",
            str(repo),
            *args,
        ],
        cwd or PROJECT_ROOT,
        check=check,
    )


def assert_single_json_line(test, result, stream="stdout"):
    value = getattr(result, stream)
    test.assertTrue(value.endswith("\n"), value)
    test.assertEqual(value.count("\n"), 1, value)
    return json.loads(value)


def assert_json_help(test, result, command=None):
    test.assertEqual(result.returncode, 0)
    test.assertEqual(result.stderr, "")
    payload = assert_single_json_line(test, result)
    test.assertEqual(payload["status"], "help")
    if command is not None:
        test.assertEqual(payload["command"], command)
    return payload


def make_cli_repo(path):
    path.mkdir()
    shutil.copytree(PROJECT_ROOT / "team_control", path / "team_control")
    scripts = path / "scripts"
    scripts.mkdir()
    for name in ("team-control", "worktree-doctor"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, scripts / name)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    (path / ".gitignore").write_text("/.worktrees/\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], path)
    run(["git", "config", "user.name", "MVP0 Test"], path)
    run(["git", "config", "user.email", "mvp0@example.invalid"], path)
    run(["git", "add", "--", "."], path)
    run(["git", "commit", "-m", "test: initialize CLI fixture"], path)
    return path


class CliTests(unittest.TestCase):
    def test_root_and_every_subparser_disable_option_abbreviations(self):
        from team_control.cli import build_parser

        parser = build_parser()
        self.assertFalse(parser.allow_abbrev)
        subparser_action = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparser_action.choices),
            {
                "approvals", "doctor", "init", "intents", "process-intent",
                "process-pending-intents", "projects", "start", "status",
                "transition",
            },
        )
        for command, subparser in subparser_action.choices.items():
            with self.subTest(command=command):
                self.assertFalse(subparser.allow_abbrev)
        projects = subparser_action.choices["projects"]
        project_subparser_action = next(
            action
            for action in projects._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(project_subparser_action.choices), {"register", "retire", "list"}
        )
        for command, subparser in project_subparser_action.choices.items():
            with self.subTest(project_command=command):
                self.assertFalse(subparser.allow_abbrev)

    def test_abbreviated_help_is_single_json_error_not_argparse_help(self):
        result = run(
            [sys.executable, "-m", "team_control", "--hel"],
            PROJECT_ROOT,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        payload = assert_single_json_line(self, result, "stderr")
        self.assertEqual(payload["error"]["code"], "CONTRACT_ERROR")
        self.assertNotIn("usage:", result.stderr)

    def test_help_is_single_json_line_for_module_and_wrapper_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = make_cli_repo(tmp_path / "help repo")
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()

            module_help = run(
                [sys.executable, "-m", "team_control", "-h"], PROJECT_ROOT
            )
            root_payload = assert_json_help(self, module_help)
            self.assertIn("doctor", root_payload["commands"])

            module_doctor_help = run_cli(repo, "doctor", "--help")
            doctor_payload = assert_json_help(
                self, module_doctor_help, command="doctor"
            )
            self.assertEqual(doctor_payload["modes"], ["inspect", "repair"])

            module_projects_help = run_cli(repo, "projects", "--help")
            projects_payload = assert_json_help(
                self, module_projects_help, command="projects"
            )
            self.assertEqual(
                projects_payload["subcommands"], ["register", "retire", "list"]
            )
            self.assertEqual(
                projects_payload["usage"],
                "team-control --repo PATH projects {register,retire,list}",
            )

            project_help_cases = {
                "register": (
                    "team-control --repo PATH projects register "
                    "--display-name NAME --path ABSOLUTE_PATH"
                ),
                "retire": (
                    "team-control --repo PATH projects retire --project-id UUID"
                ),
                "list": "team-control --repo PATH projects list",
            }
            for project_command, usage in project_help_cases.items():
                with self.subTest(entrypoint="module", command=project_command):
                    result = run_cli(repo, "projects", project_command, "--help")
                    payload = assert_json_help(self, result, command="projects")
                    self.assertEqual(payload["project_command"], project_command)
                    self.assertEqual(payload["usage"], usage)
                    self.assertNotIn("subcommands", payload)

            wrapper_help = run(
                [str(repo / "scripts" / "team-control"), "--help"],
                elsewhere,
            )
            assert_json_help(self, wrapper_help)

            wrapper_doctor_help = run(
                [str(repo / "scripts" / "team-control"), "doctor", "-h"],
                elsewhere,
            )
            assert_json_help(self, wrapper_doctor_help, command="doctor")

            for project_command, usage in project_help_cases.items():
                with self.subTest(entrypoint="wrapper", command=project_command):
                    result = run(
                        [
                            str(repo / "scripts" / "team-control"),
                            "projects",
                            project_command,
                            "--help",
                        ],
                        elsewhere,
                    )
                    payload = assert_json_help(self, result, command="projects")
                    self.assertEqual(payload["project_command"], project_command)
                    self.assertEqual(payload["usage"], usage)

            doctor_wrapper_help = run(
                [str(repo / "scripts" / "worktree-doctor"), "--help"],
                elsewhere,
            )
            assert_json_help(self, doctor_wrapper_help, command="doctor")

    def test_wrappers_reject_repo_override_before_mutating_either_repo(self):
        cases = (
            ("team-control", "split"),
            ("team-control", "equals"),
            ("team-control", "abbreviated"),
            ("team-control", "abbreviated_equals"),
            ("worktree-doctor", "split"),
            ("worktree-doctor", "equals"),
            ("worktree-doctor", "abbreviated"),
            ("worktree-doctor", "abbreviated_equals"),
        )
        for wrapper_name, form in cases:
            with self.subTest(wrapper=wrapper_name, form=form):
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)
                    bound = make_cli_repo(tmp_path / "bound repo")
                    other = make_cli_repo(tmp_path / "other repo")
                    bound_db = (
                        RepoContext.discover(bound).common_dir
                        / "team"
                        / "runtime"
                        / "team.db"
                    )
                    other_db = (
                        RepoContext.discover(other).common_dir
                        / "team"
                        / "runtime"
                        / "team.db"
                    )
                    if form == "split":
                        override = ["--repo", str(other)]
                    elif form == "equals":
                        override = ["--repo=%s" % other]
                    elif form == "abbreviated":
                        override = ["--rep", str(other)]
                    else:
                        override = ["--rep=%s" % other]
                    result = run(
                        [
                            str(bound / "scripts" / wrapper_name),
                            *override,
                            "init",
                        ],
                        tmp_path,
                        check=False,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    payload = assert_single_json_line(self, result, "stderr")
                    self.assertEqual(
                        payload["error"]["code"], "WRAPPER_REPO_OVERRIDE"
                    )
                    self.assertFalse(bound_db.exists())
                    self.assertFalse(other_db.exists())

    def test_init_start_status_transition_and_approvals_emit_single_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo with spaces")

            initialized = run_cli(repo, "init")
            init_payload = assert_single_json_line(self, initialized)
            self.assertEqual(initialized.stderr, "")
            self.assertEqual(init_payload["status"], "initialized")

            start_args = (
                "start",
                "--dispatch-id",
                "20260808-008",
                "--title",
                "CLI",
                "--objective",
                "Emit JSON",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                "cli-smoke",
            )
            started = run_cli(repo, *start_args)
            started_payload = assert_single_json_line(self, started)
            self.assertEqual(started.stderr, "")
            self.assertEqual(started_payload["state"], "DISPATCHED")
            self.assertTrue(Path(started_payload["worktree_path"]).is_dir())

            repeated = run_cli(repo, *start_args)
            self.assertEqual(
                assert_single_json_line(self, repeated), started_payload
            )

            status = run_cli(
                repo, "status", "--dispatch-id", "20260808-008"
            )
            status_payload = assert_single_json_line(self, status)
            self.assertEqual(status_payload["task"]["state"], "DISPATCHED")

            transitioned = run_cli(
                repo,
                "transition",
                "--dispatch-id",
                "20260808-008",
                "--to",
                "IN_PROGRESS",
                "--reason",
                "implementation began",
            )
            transition_payload = assert_single_json_line(self, transitioned)
            self.assertEqual(transition_payload["state"], "IN_PROGRESS")

            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = store.get_task("20260808-008")
            nonce = "cli-approval-nonce-0001"
            approval = control.request_approval(
                "20260808-008",
                "integrate",
                task["current_head_sha"],
                {},
                nonce,
                10,
            )
            approvals = run_cli(
                repo, "approvals", "--dispatch-id", "20260808-008"
            )
            approvals_payload = assert_single_json_line(self, approvals)
            self.assertEqual(
                [item["approval_id"] for item in approvals_payload["approvals"]],
                [approval["approval_id"]],
            )
            self.assertEqual(approvals_payload["approvals"][0]["status"], "PENDING")
            self.assertIsNone(approvals_payload["approvals"][0]["consumed_at"])
            self.assertEqual(store.pending_approvals("20260808-008"), [approval])

            control.consume_approval(approval["approval_id"], nonce)
            consumed_approvals = run_cli(
                repo, "approvals", "--dispatch-id", "20260808-008"
            )
            consumed_payload = assert_single_json_line(self, consumed_approvals)
            self.assertEqual(
                consumed_payload["approvals"][0]["status"], "CONSUMED"
            )
            self.assertIsNotNone(
                consumed_payload["approvals"][0]["consumed_at"]
            )
            self.assertEqual(store.pending_approvals("20260808-008"), [])
            prepared = store.prepared_operations()
            self.assertEqual(len(prepared), 1)
            store.finish_operation(
                prepared[0]["operation_id"],
                "COMMITTED",
                {"verified": True, "callback_status": "PENDING"},
            )

            intents = IntentService(context, store, control)
            submitted = intents.submit({
                "dispatch_id": "20260808-008",
                "action": "PAUSE_REQUEST",
                "target_sha": task["current_head_sha"],
                "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
                "parameters": {},
            })
            listed = run_cli(repo, "intents", "--dispatch-id", "20260808-008")
            listed_payload = assert_single_json_line(self, listed)
            self.assertEqual(
                [item["intent_id"] for item in listed_payload["intents"]],
                [submitted["intent_id"]],
            )
            self.assertEqual(set(listed_payload["intents"][0]), {
                "intent_id", "dispatch_id", "action", "target_sha", "status",
                "result_code", "created_at", "updated_at",
            })
            self.assertNotIn("confirmation", listed.stdout)
            processed = run_cli(
                repo, "process-intent", "--intent-id", submitted["intent_id"]
            )
            processed_payload = assert_single_json_line(self, processed)
            self.assertEqual(
                (processed_payload["status"], processed_payload["result_code"]),
                ("APPLIED", "PAUSE_REQUESTED"),
            )

            all_approvals = run_cli(repo, "approvals")
            all_payload = assert_single_json_line(self, all_approvals)
            self.assertEqual(
                [item["approval_id"] for item in all_payload["approvals"]],
                [approval["approval_id"]],
            )
            self.assertEqual(all_payload["approvals"][0]["status"], "CONSUMED")

    def test_process_pending_intents_emits_safe_bounded_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_cli_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = control.create_task(
                "20260812-005", "Queue", "Process pending safely", "L2"
            )
            control.transition(task["dispatch_id"], "DISPATCHED", "start")
            control.transition(task["dispatch_id"], "IN_PROGRESS", "start")
            IntentService(context, store, control).submit({
                "dispatch_id": task["dispatch_id"],
                "action": "PAUSE_REQUEST",
                "target_sha": task["current_head_sha"],
                "idempotency_key": "123e4567-e89b-12d3-a456-426614174012",
                "parameters": {},
            })

            processed = run_cli(repo, "process-pending-intents", "--limit", "1")
            payload = assert_single_json_line(self, processed)

            self.assertEqual(payload["attempted"], 1)
            self.assertEqual(payload["results"][0]["status"], "APPLIED")
            self.assertEqual(set(payload["results"][0]), {
                "intent_id", "dispatch_id", "action", "target_sha", "status",
                "result_code", "created_at", "updated_at",
            })
            self.assertNotIn("confirmation", processed.stdout)

    def test_doctor_inspect_and_repair_emit_single_json_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            context = RepoContext.discover(repo)
            store = ControlStore.for_repo(context)
            control = ControlPlane(context, store)
            task = control.create_task(
                "20260808-009", "Doctor", "Repair safely", "L1"
            )
            branch = "agent/codex/20260808-009-doctor"
            run(["git", "branch", branch, task["task_base_sha"]], repo)
            common = (
                "--dispatch-id",
                "20260808-009",
                "--agent",
                "codex",
                "--slug",
                "doctor",
                "--base-sha",
                task["task_base_sha"],
            )

            inspected = run_cli(repo, "doctor", "inspect", *common)
            self.assertEqual(
                assert_single_json_line(self, inspected)["classification"],
                "REPAIRABLE_BRANCH_ONLY",
            )
            repaired = run_cli(repo, "doctor", "repair", *common)
            repaired_payload = assert_single_json_line(self, repaired)
            self.assertEqual(repaired_payload["classification"], "HEALTHY")
            self.assertTrue(Path(repaired_payload["path"]).is_dir())

    def test_projects_commands_register_list_and_retire_safe_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            central = make_cli_repo(tmp_path / "central")
            target = make_repo(tmp_path / "target project")
            target_database = (
                RepoContext.discover(target).common_dir / "team" / "runtime" / "team.db"
            )
            self.assertFalse(target_database.exists())
            run_cli(central, "init")

            registered = run_cli(
                central,
                "projects",
                "register",
                "--display-name",
                "Local Target",
                "--path",
                str(target),
            )
            registered_payload = assert_single_json_line(self, registered)
            self.assertEqual(set(registered_payload), {
                "project_id", "display_name", "status", "created_at", "updated_at",
            })
            self.assertEqual(registered_payload["display_name"], "Local Target")
            self.assertEqual(registered_payload["status"], "ACTIVE")
            self.assertNotIn(str(target), registered.stdout)
            self.assertFalse(target_database.exists())

            listed = run_cli(central, "projects", "list")
            listed_payload = assert_single_json_line(self, listed)
            self.assertEqual(list(listed_payload), ["projects"])
            self.assertEqual(listed_payload["projects"], [registered_payload])
            self.assertNotIn(str(target), listed.stdout)

            retired = run_cli(
                central,
                "projects",
                "retire",
                "--project-id",
                registered_payload["project_id"],
            )
            retired_payload = assert_single_json_line(self, retired)
            self.assertEqual(set(retired_payload), {
                "project_id", "display_name", "status", "created_at", "updated_at",
            })
            self.assertEqual(retired_payload["status"], "RETIRED")
            self.assertNotIn(str(target), retired.stdout)
            self.assertEqual(
                assert_single_json_line(self, run_cli(central, "projects", "list")),
                {"projects": []},
            )
            self.assertFalse(target_database.exists())

            # Existing central commands remain available after registry activity.
            self.assertEqual(
                assert_single_json_line(
                    self,
                    run_cli(central, "intents"),
                ),
                {"intents": []},
            )

    def test_projects_reject_unknown_subcommands_and_missing_required_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            central = make_cli_repo(tmp_path / "central")
            run_cli(central, "init")

            unknown = run_cli(central, "projects", "scan", check=False)
            self.assertNotEqual(unknown.returncode, 0)
            self.assertEqual(unknown.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, unknown, "stderr")["error"]["code"],
                "CONTRACT_ERROR",
            )

            missing_path = run_cli(
                central,
                "projects",
                "register",
                "--display-name",
                "Missing Path",
                check=False,
            )
            self.assertNotEqual(missing_path.returncode, 0)
            self.assertEqual(missing_path.stdout, "")
            self.assertEqual(
                assert_single_json_line(
                    self, missing_path, "stderr"
                )["error"]["code"],
                "CONTRACT_ERROR",
            )

            absent_target = tmp_path / "private target path"
            invalid_target = run_cli(
                central,
                "projects",
                "register",
                "--display-name",
                "Unavailable",
                "--path",
                str(absent_target),
                check=False,
            )
            self.assertNotEqual(invalid_target.returncode, 0)
            self.assertEqual(invalid_target.stdout, "")
            self.assertEqual(
                assert_single_json_line(
                    self, invalid_target, "stderr"
                )["error"]["code"],
                "BOUNDARY_ERROR",
            )
            self.assertNotIn(str(absent_target), invalid_target.stderr)

    def test_projects_register_redacts_target_git_errors_at_cli_boundary(self):
        from team_control import cli

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            central = make_cli_repo(tmp_path / "central")
            run_cli(central, "init")
            private_target = str(tmp_path / "private" / "target")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with mock.patch.object(
                cli.ProjectRegistryService,
                "register",
                side_effect=GitStateError(
                    "git target inspection failed: %s" % private_target
                ),
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main([
                    "--repo",
                    str(central),
                    "projects",
                    "register",
                    "--display-name",
                    "Private Target",
                    "--path",
                    private_target,
                ])

            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(stderr.getvalue().endswith("\n"))
            self.assertEqual(stderr.getvalue().count("\n"), 1)
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"]["code"], "BOUNDARY_ERROR")
            self.assertNotIn(private_target, stderr.getvalue())

    def test_projects_argparse_errors_never_echo_an_absolute_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            central = make_cli_repo(tmp_path / "central")
            private_argument = str(tmp_path / "very-private" / "target")

            result = run_cli(
                central, "projects", private_argument, check=False
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, result, "stderr")["error"]["code"],
                "CONTRACT_ERROR",
            )
            self.assertNotIn(private_argument, result.stderr)

    def test_any_argparse_error_never_echoes_an_absolute_command_or_stray_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            central = make_cli_repo(tmp_path / "central")
            private_command = str(tmp_path / "very-private" / "command")
            private_stray_argument = str(tmp_path / "very-private" / "stray")

            for arguments, private_value in (
                ((private_command,), private_command),
                (("status", "--dispatch-id", "safe", private_stray_argument), private_stray_argument),
            ):
                with self.subTest(arguments=arguments):
                    result = run_cli(central, *arguments, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(
                        assert_single_json_line(self, result, "stderr")["error"]["code"],
                        "CONTRACT_ERROR",
                    )
                    self.assertEqual(
                        assert_single_json_line(self, result, "stderr")["error"]["message"],
                        (
                            "invalid status command arguments"
                            if arguments[0] == "status"
                            else "invalid command arguments"
                        ),
                    )
                    self.assertNotIn(private_value, result.stderr)

    def test_projects_help_does_not_treat_an_option_value_as_a_subcommand(self):
        from team_control import cli

        payload = cli.help_payload([
            "--repo", "/private/central", "projects", "--path", "list", "--help",
        ])

        self.assertEqual(payload["command"], "projects")
        self.assertNotIn("project_command", payload)
        self.assertEqual(payload["subcommands"], ["register", "retire", "list"])

    def test_help_scope_does_not_treat_an_unknown_option_value_as_a_command(self):
        from team_control import cli

        payload = cli.help_payload([
            "--unknown-option", "projects", "--help",
        ])

        self.assertNotIn("command", payload)
        self.assertEqual(payload["commands"], list(cli.COMMANDS))

    def test_domain_and_argparse_errors_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            bad_component = run_cli(
                repo,
                "start",
                "--dispatch-id",
                "bad/id",
                "--title",
                "Bad",
                "--objective",
                "Reject",
                "--risk",
                "L1",
                "--agent",
                "codex",
                "--slug",
                "bad",
                check=False,
            )
            self.assertNotEqual(bad_component.returncode, 0)
            self.assertEqual(bad_component.stdout, "")
            error = assert_single_json_line(self, bad_component, "stderr")
            self.assertEqual(error["error"]["code"], "BOUNDARY_ERROR")
            self.assertNotIn("Traceback", bad_component.stderr)

            usage = run_cli(repo, "start", check=False)
            self.assertNotEqual(usage.returncode, 0)
            self.assertEqual(usage.stdout, "")
            usage_error = assert_single_json_line(self, usage, "stderr")
            self.assertEqual(usage_error["error"]["code"], "CONTRACT_ERROR")
            self.assertNotIn("usage:", usage.stderr)

    def test_missing_repo_and_missing_initialization_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing repo"
            no_repo = run_cli(missing, "init", check=False)
            self.assertNotEqual(no_repo.returncode, 0)
            self.assertEqual(no_repo.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, no_repo, "stderr")["error"]["code"],
                "BOUNDARY_ERROR",
            )

            repo = make_repo(Path(tmp) / "repo")
            not_initialized = run_cli(
                repo,
                "status",
                "--dispatch-id",
                "20260808-010",
                check=False,
            )
            self.assertNotEqual(not_initialized.returncode, 0)
            self.assertEqual(not_initialized.stdout, "")
            self.assertEqual(
                assert_single_json_line(
                    self, not_initialized, "stderr"
                )["error"]["code"],
                "CONTRACT_ERROR",
            )

    def test_missing_intents_table_requires_schema_migration_before_queue_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_cli_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            with store.mutation() as connection:
                connection.execute("DROP TABLE intents")

            blocked = run_cli(
                repo,
                "process-pending-intents",
                "--limit",
                "1",
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual(blocked.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, blocked, "stderr")["error"]["code"],
                "SCHEMA_MIGRATION_REQUIRED",
            )

            run_cli(repo, "init")
            recovered = run_cli(repo, "process-pending-intents", "--limit", "1")
            self.assertEqual(
                assert_single_json_line(self, recovered),
                {"attempted": 0, "results": []},
            )

    def test_missing_required_table_blocks_every_non_init_cli_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_cli_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            with store.mutation() as connection:
                connection.execute("DROP TABLE intents")

            blocked = run_cli(
                repo,
                "status",
                "--dispatch-id",
                "20260812-007",
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual(blocked.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, blocked, "stderr")["error"]["code"],
                "SCHEMA_MIGRATION_REQUIRED",
            )

    def test_required_table_with_missing_column_is_schema_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_cli_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            with store.mutation() as connection:
                connection.execute("DROP TABLE intents")
                connection.execute("CREATE TABLE intents (intent_id TEXT PRIMARY KEY)")

            blocked = run_cli(
                repo,
                "process-pending-intents",
                "--limit",
                "1",
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual(blocked.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, blocked, "stderr")["error"]["code"],
                "SCHEMA_UNSUPPORTED",
            )

    def test_required_table_name_cannot_be_satisfied_by_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_cli_repo(Path(tmp) / "repo")
            run_cli(repo, "init")
            store = ControlStore.for_repo(RepoContext.discover(repo))
            with store.mutation() as connection:
                connection.execute("DROP TABLE intents")
                connection.execute("CREATE VIEW intents AS SELECT 'fake' AS intent_id")

            blocked = run_cli(
                repo,
                "process-pending-intents",
                "--limit",
                "1",
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual(blocked.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, blocked, "stderr")["error"]["code"],
                "SCHEMA_UNSUPPORTED",
            )

    def test_unexpected_error_is_redacted_without_traceback(self):
        from team_control import cli

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(cli, "execute", side_effect=RuntimeError("secret")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = cli.main(["--repo", ".", "init"])

        self.assertNotEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        payload = json.loads(stderr.getvalue())
        self.assertEqual(payload["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("secret", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_wrappers_locate_repo_from_arbitrary_cwd_and_do_not_inject_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = make_cli_repo(tmp_path / "repo with spaces;safe")
            elsewhere = tmp_path / "elsewhere"
            elsewhere.mkdir()
            marker = tmp_path / "PWNED"
            wrapper = repo / "scripts" / "team-control"

            initialized = run([str(wrapper), "init"], elsewhere)
            self.assertEqual(
                assert_single_json_line(self, initialized)["status"],
                "initialized",
            )
            rejected = run(
                [
                    str(wrapper),
                    "start",
                    "--dispatch-id",
                    "bad;touch-PWNED",
                    "--title",
                    "No injection",
                    "--objective",
                    "Quote argv",
                    "--risk",
                    "L1",
                    "--agent",
                    "codex",
                    "--slug",
                    "safe",
                ],
                elsewhere,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(rejected.stdout, "")
            self.assertEqual(
                assert_single_json_line(self, rejected, "stderr")["error"]["code"],
                "BOUNDARY_ERROR",
            )

            started = run(
                [
                    str(wrapper),
                    "start",
                    "--dispatch-id",
                    "20260808-011",
                    "--title",
                    "Wrapper",
                    "--objective",
                    "Run from elsewhere",
                    "--risk",
                    "L1",
                    "--agent",
                    "codex",
                    "--slug",
                    "wrapper",
                ],
                elsewhere,
            )
            task = assert_single_json_line(self, started)
            doctor = run(
                [
                    str(repo / "scripts" / "worktree-doctor"),
                    "inspect",
                    "--dispatch-id",
                    "20260808-011",
                    "--agent",
                    "codex",
                    "--slug",
                    "wrapper",
                    "--base-sha",
                    task["task_base_sha"],
                ],
                elsewhere,
            )
            self.assertEqual(
                assert_single_json_line(self, doctor)["classification"],
                "HEALTHY",
            )


if __name__ == "__main__":
    unittest.main()
