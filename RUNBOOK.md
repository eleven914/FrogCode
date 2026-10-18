# 运维手册（RUNBOOK）

## 日常操作

### 启动守护进程

```bash
uv run frog-core
```

默认监听 `127.0.0.1:7437`，按 `Ctrl+C` 优雅退出。

### 验证连通

```bash
uv run frog frog-ping
# → pong server=0.1.0 uptime=12ms latency=2ms
```

### 停止守护进程

```bash
kill $(pgrep -f frog-core)
```

---

## 配置

优先级（低 → 高）：**内建默认值 → `~/.frog/config.toml` → 项目 `.frog/config.toml` → `.env` → 系统环境变量**。

### `~/.frog/config.toml`

```toml
[core]
host = "127.0.0.1"
port = 7437

[logging]
level  = "INFO"
file   = "~/.frog/logs/core.log"
format = "text"    # "text" | "json"
```

### `.env`

从 `.env.example` 复制后修改，存放本机配置与密钥（不提交 git）：

```bash
cp .env.example .env
```

### 系统环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FROG_CONFIG` | `~/.frog/config.toml` | 覆盖配置文件路径 |
| `FROG_HOST` | `127.0.0.1` | TCP 监听地址 |
| `FROG_PORT` | `7437` | TCP 监听端口 |
| `FROG_LOG_LEVEL` | `INFO` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `FROG_LOG_FILE` | `~/.frog/logs/core.log` | 日志文件路径（留空则仅输出 stderr） |
| `FROG_LOG_FORMAT` | `text` | 日志格式（`text` 或 `json`） |
| `FROG_LLM_DEFAULT_MODEL` | `claude-sonnet-4-6` | 模型名称 |
| `FROG_LLM_BASE_URL` | 空 | Anthropic Messages 兼容接口地址；DeepSeek 使用 `https://api.deepseek.com/anthropic` |

---

## 开发

```bash
uv run ruff check src tests scripts   # lint
uv run mypy src                       # 类型检查
uv run pytest tests/ -v               # 全量测试
uv run pytest tests/unit/ -v         # 仅单元测试（无需启动 daemon）

make docs                             # 重新生成 WIRE_PROTOCOL.md
make verify-s0                        # 完整验证（lint + 类型 + 测试 + 协议同源检查）
```

---

## 日志

```bash
tail -f ~/.frog/logs/core.log
```

---

## 常见错误

| 报错 | 原因 | 处理 |
|------|------|------|
| `core already running at 127.0.0.1:7437` | 已有守护进程在运行 | `kill $(pgrep -f frog-core)` |
| `core not running` | 未启动守护进程 | `uv run frog-core` |
| `Address already in use` | 端口被其他进程占用 | `FROG_PORT=8000 uv run frog-core` |
| `Config error: FROG_PORT must be an integer` | `.env` 或环境变量中端口值非整数 | 检查 `FROG_PORT` 的值 |
| `403 Request not allowed` | DeepSeek 模型被发往 Anthropic 官方接口，或 API Key 无权限 | 设置 `FROG_LLM_BASE_URL=https://api.deepseek.com/anthropic`，确认 Key 属于 DeepSeek 后重启 Core |
