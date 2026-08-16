from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__
from .exit_codes import ExitCode
from .paths import ConfigurationError, RuntimePaths, resolve_runtime_paths
from .result import cli_failure, failure, render, success


COMMANDS = ("doctor", "plan", "apply", "verify", "status", "version")


class UsageError(ValueError):
    """Raised instead of letting argparse echo untrusted input."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _add_json_flag(parser: argparse.ArgumentParser, *, suppress_default: bool = False) -> None:
    default: Any = argparse.SUPPRESS if suppress_default else False
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=default,
        help="emit one deterministic JSON object",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        prog="arrctl",
        description="Safe, deterministic control plane for an existing *arr stack.",
    )
    _add_json_flag(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    descriptions = {
        "doctor": "inspect configuration readiness without contacting services",
        "plan": "produce an empty local skeleton plan without mutations",
        "apply": "fail closed until mutation support is implemented",
        "verify": "report local verification readiness without contacting services",
        "status": "show resolved external runtime directories",
        "version": "show the arrctl package version",
    }
    for command in COMMANDS:
        command_parser = subparsers.add_parser(command, help=descriptions[command])
        _add_json_flag(command_parser, suppress_default=True)
    return parser


def _base_data() -> dict[str, Any]:
    return {"mode": "skeleton", "remote_side_effects": False}


def _runtime_data(paths: RuntimePaths) -> dict[str, Any]:
    return {
        **_base_data(),
        "config_dir": str(paths.config_dir),
        "data_dir": str(paths.data_dir),
    }


def execute(command: str) -> dict[str, Any]:
    if command == "version":
        return success(command, {**_base_data(), "version": __version__})

    paths = resolve_runtime_paths()
    if command == "doctor":
        return success(
            command,
            {**_runtime_data(paths), "assessment": "not_run", "checks": []},
        )
    if command == "plan":
        return success(
            command,
            {**_runtime_data(paths), "plan_state": "not_generated", "operations": {}},
        )
    if command == "apply":
        return failure(
            command,
            ExitCode.OPERATION,
            code="CAPABILITY_MISSING",
            category="capability",
            message="Mutation support is not available in the CLI skeleton.",
            reason="mutation_not_implemented",
            data=_runtime_data(paths),
            status="blocked",
        )
    if command == "verify":
        return success(
            command,
            {**_runtime_data(paths), "assessment": "not_run", "checks": {}},
        )
    if command == "status":
        return success(command, {**_runtime_data(paths), "runtime_initialized": False})
    raise ValueError("unknown command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_args
    try:
        args = parser.parse_args(raw_args)
    except UsageError:
        payload = cli_failure(
            "cli",
            ExitCode.USAGE,
            code="USAGE_INVALID",
            message="Command-line arguments are invalid.",
        )
        if json_requested:
            print(render(payload, True))
        else:
            parser.print_usage(sys.stderr)
            print(render(payload, False), file=sys.stderr)
        return int(ExitCode.USAGE)
    if args.command is None:
        parser.print_help()
        return int(ExitCode.SUCCESS)

    json_output = bool(getattr(args, "json_output", False))
    try:
        payload = execute(args.command)
    except ConfigurationError:
        payload = failure(
            args.command,
            ExitCode.CONFIGURATION,
            code="CONFIG_INVALID",
            category="configuration",
            message="Runtime directory configuration is invalid or unsafe.",
            reason="runtime_directory_invalid",
        )
    except Exception:
        payload = cli_failure(
            args.command,
            ExitCode.INTERNAL,
            code="INTERNAL_ERROR",
            message="The command failed without exposing private details.",
        )

    output = render(payload, json_output)
    stream = sys.stdout if json_output or payload["status"] == "ok" else sys.stderr
    print(output, file=stream)
    return int(payload["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
