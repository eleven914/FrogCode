from __future__ import annotations

import argparse
import sys

from frog_code.cli.commands.chat import frog_cmd_chat
from frog_code.cli.commands.core import frog_cmd_core_start, frog_cmd_core_status, frog_cmd_core_stop
from frog_code.cli.commands.ping import frog_cmd_ping
from frog_code.cli.commands.run import frog_cmd_run
from frog_code.cli.commands.trace import frog_cmd_trace
from frog_code.cli.commands.version import frog_cmd_version
from frog_code.core.config import frog_get_config
from frog_code.core.logging_setup import frog_setup_logging


# CLI 主入口：解析命令行参数并分发到对应子命令
def frog_main() -> None:
    parser = argparse.ArgumentParser(prog="frog", description="FrogCode CLI")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("frog-ping", help="Ping the FrogCode core daemon")
    subparsers.add_parser("frog-chat", help="Start a multi-turn FrogCode chat session")

    run_parser = subparsers.add_parser("frog-run", help="Run a FrogCode agent task")
    run_parser.add_argument("--goal", required=True, help="Goal for the agent to accomplish")

    core_parser = subparsers.add_parser("frog-core", help="Manage the FrogCode core daemon")
    core_sub = core_parser.add_subparsers(dest="core_command")
    core_sub.add_parser("frog-start", help="Start the daemon in the background")
    core_sub.add_parser("frog-stop", help="Stop the running daemon")
    core_sub.add_parser("frog-status", help="Show daemon status")

    trace_parser = subparsers.add_parser("frog-trace", help="View the FrogCode trace log")
    trace_parser.add_argument("run_id", nargs="?", default=None, help="Filter by run ID")
    trace_parser.add_argument("--layer", choices=["ipc", "event", "llm"], help="Filter by layer")
    trace_parser.add_argument("--direction", help="Filter by direction (e.g. CORE→LLM)")
    trace_parser.add_argument("--raw", action="store_true", help="Output raw NDJSON")
    trace_parser.add_argument("--follow", "-f", action="store_true", help="Follow new records")

    args = parser.parse_args()

    if args.version:
        frog_cmd_version()
        return

    config = frog_get_config()
    frog_setup_logging(config)

    if args.command == "frog-ping":
        frog_cmd_ping(config)
    elif args.command == "frog-chat":
        frog_cmd_chat(config)
    elif args.command == "frog-run":
        frog_cmd_run(args.goal, config)
    elif args.command == "frog-core":
        if args.core_command == "frog-start":
            frog_cmd_core_start(config)
        elif args.core_command == "frog-stop":
            frog_cmd_core_stop(config)
        elif args.core_command == "frog-status":
            frog_cmd_core_status(config)
        else:
            core_parser.print_help()
            sys.exit(1)
    elif args.command == "frog-trace":
        frog_cmd_trace(
            args.run_id,
            config,
            layer=args.layer,
            direction=args.direction,
            raw=args.raw,
            follow=args.follow,
        )
    else:
        parser.print_help()
        sys.exit(1)
