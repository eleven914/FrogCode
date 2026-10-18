# FrogCode contributor guide

## Commands

```bash
uv sync --frozen
uv run ruff check src tests scripts frog_trace_permission_flow.py
uv run mypy src
uv run python scripts/frog_check_naming.py
uv run pytest tests/unit -q
uv run pytest tests/integration --ignore=tests/integration/test_run_e2e.py -q
uv run python scripts/frog_gen_protocol_doc.py --check
```

`test_run_e2e.py` calls a real model and is run separately when `ANTHROPIC_API_KEY` is intentionally configured.

## Architecture

FrogCode is a dual-process local Agent runtime. `frog-core` listens on TCP loopback and accepts JSON-RPC 2.0 requests framed as NDJSON. `frog` and `frog-tui` are clients; TUI is the main product interface.

The execution path is `FrogCoreApp → FrogAgentRunner → FrogAgentLoop → FrogLLMProvider/FrogToolRegistry`. Events flow through `FrogEventBus` to clients, JSONL storage, and trace records. Sessions, permissions, compaction, Skills, subagents, and MCP share this daemon path.

## Naming

Project-owned classes use `Frog*`; module-level functions and business methods use `frog_*` or `_frog_*`; commands, tools, events, environment variables, and configuration paths follow [docs/frog-naming-map.md](docs/frog-naming-map.md). Framework hooks and lifecycle methods listed there are explicit exceptions.

MCP remote tool names are external protocol data and must remain unchanged. Use the adapter namespace instead of rewriting them.

## Protocol and tests

Commands and events are Pydantic discriminated unions in `src/frog_code/core/bus/`. Regenerate `WIRE_PROTOCOL.md` after changing them:

```bash
uv run python scripts/frog_gen_protocol_doc.py
```

Production functions have a concise Chinese comment immediately above their definitions. Test functions keep the existing two-line `功能` and `设计` comments. Add tests for behavior and boundaries rather than mirroring implementation.
