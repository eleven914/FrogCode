# FrogCode

FrogCode 是一个运行在本地的 AI 编程 Agent。它采用常驻 Core daemon 与 CLI/TUI 多客户端架构，支持多步工具调用、事件流、权限审批、会话记忆、上下文压缩、Skills、子 Agent 和 MCP。

本项目基于 [KamaClaude](https://github.com/youngyangyang04/KamaClaude) 进行二次开发，沿用其核心技术路线，并完成了 Frog 品牌、Python 包、类与函数、命令、工具、协议命名空间、配置目录和文档体系的迁移。原项目版权及 MIT 许可证保留在 [LICENSE](LICENSE) 中。

## 架构

```text
frog / frog-tui
        │
        │ JSON-RPC 2.0 + NDJSON over TCP
        ▼
frog-core daemon
        ├── FrogAgentRunner / FrogAgentLoop
        ├── LLM Provider
        ├── FrogToolRegistry
        ├── FrogPermissionManager
        ├── FrogEventBus / trace / events.jsonl
        ├── session / thread / notes / compact
        └── Skills / Subagents / MCP
```

Core 负责任务执行和持久化，CLI 与 TUI 只是客户端。客户端退出后，daemon 的运行状态与会话数据仍可独立管理。

## 技术栈

- Python 3.12、asyncio、Pydantic v2
- Anthropic SDK，可通过兼容接口接入其他模型服务
- Textual TUI
- TCP、NDJSON、JSON-RPC 2.0
- pytest、pytest-asyncio、Ruff、mypy strict
- uv 与 Hatchling

## 快速开始

安装依赖并创建本地配置：

```powershell
uv sync
Copy-Item .env.example .env
```

在 `.env` 中配置模型密钥和模型名称。不要提交 `.env`。

启动 Core：

```powershell
uv run frog-core
```

另开一个终端验证连接或启动 TUI：

```powershell
uv run frog frog-ping
uv run frog-tui
```

## Frog 命令

```text
frog --version
frog frog-ping
frog frog-run --goal "分析当前项目并给出改进建议"
frog frog-chat
frog frog-trace [RUN_ID]
frog frog-core frog-start
frog frog-core frog-status
frog frog-core frog-stop
```

TUI 内置命令为 `/frog-compact`。内置 Skill 包括 `/frog_init`、`/frog_review`、`/frog_summarize` 和 `/frog_orchestrate`。

内置工具统一使用 Frog 名称：

```text
frog_read_file    frog_write_file    frog_list_dir
frog_bash         frog_note_save     frog_task_create
frog_task_get     frog_task_list     frog_task_update
frog_spawn_agent  frog_agent_result
```

MCP 服务提供的远端工具保留原名，由 FrogCode 使用 `server__tool` 形式隔离命名空间。

## 配置

配置优先级从低到高为：内建默认值、`~/.frog/config.toml`、项目 `.frog/config.toml`、`.env`、系统环境变量。

常用变量：

```dotenv
FROG_HOST=127.0.0.1
FROG_PORT=7437
FROG_LOG_LEVEL=INFO
FROG_LOG_FILE=~/.frog/logs/core.log
FROG_LLM_DEFAULT_MODEL=claude-sonnet-4-6
FROG_MAX_STEPS=20
```

完整配置和运维说明见 [RUNBOOK.md](RUNBOOK.md)，IPC 契约见 [WIRE_PROTOCOL.md](WIRE_PROTOCOL.md)。

## 开发与验证

```powershell
uv run ruff check src tests scripts frog_trace_permission_flow.py
uv run mypy src
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run python scripts/frog_gen_protocol_doc.py --check
```

开发路线与阶段验收标准见 [FrogCode 项目开发计划](docs/FrogCode-development-plan.md)。

## 目录

```text
src/frog_code/       FrogCode 源码
tests/unit/          单元测试
tests/integration/   双进程与 IPC 集成测试
scripts/             协议文档生成脚本
.frog/               项目级上下文与配置入口
docs/                开发计划和项目文档
```

## 开源说明

FrogCode 根据 KamaClaude 的 MIT 许可证进行修改与再发布。公开仓库应保留 [LICENSE](LICENSE) 中的原版权声明，并在派生项目说明中保留上游链接。FrogCode 的新增命名、文档、兼容性修复和后续功能提交应通过 Git 历史明确记录。
