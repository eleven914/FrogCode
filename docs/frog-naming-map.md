# FrogCode 命名规范

FrogCode 自有的包、类、模块级函数、内部辅助方法、CLI 命令、RPC 方法、事件、工具、Agent、Skill、环境变量和配置目录都必须带 Frog 标识。

| 类型 | 规则 | 示例 |
|---|---|---|
| Python 包 | `frog_code` | `src/frog_code/` |
| 类 | `Frog*` | `FrogAgentRunner` |
| 模块级函数 | `frog_*` / `_frog_*` | `frog_get_config()` |
| 业务方法 | `frog_*` / `_frog_*` | `frog_send_command()` |
| 可执行入口 | `frog*` | `frog`、`frog-core`、`frog-tui` |
| CLI 子命令 | `frog-*` | `frog-run`、`frog-trace` |
| RPC 与事件 | `frog.*` | `frog.agent.run`、`frog.run.started` |
| 内置工具 | `frog_*` | `frog_read_file` |
| 内置 Agent | `frog_*` | `frog_planner` |
| 内置 Skill | `frog_*` | `frog_review` |
| 环境变量 | `FROG_*` | `FROG_PORT` |
| 配置目录 | `.frog` | `~/.frog/config.toml` |

少量方法必须遵循 Python 或框架协议，或者本身是由 `Frog*` 类限定的生命周期接口。包括双下划线方法、Textual 的 `compose`、`on_*`、`action_quit`、`_on_key`，以及 `run/start/stop/close/connect/load/get/register/create/update/resolve` 等通用接口。它们的完整限定名仍然带 Frog，例如 `FrogCoreApp.run()`。

MCP 远端工具名称属于外部协议，不直接篡改。FrogCode 在注册时通过 `server__tool` 形式隔离远端命名空间。

运行以下检查验证生产源码：

```powershell
uv run python scripts/frog_check_naming.py
```
