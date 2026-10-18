# FrogCode 项目开发计划

编制日期：2026-10-18。状态：可用于准备工作的初版；语雀正文尚未读取，教程章节对照待补齐。

## 1. 目标与范围

将 KamaClaude 按其技术路线、核心实现方法和阶段式开发方式，改造为 FrogCode 本地 AI 编程 Agent。完成 Frog 品牌、函数与命令体系、CLI/TUI、运行配置、文档和测试的统一，最终发布到自己的 GitHub 仓库，并形成可演示、可复盘的个人项目经历。

第一版以原项目能力对齐为目标。Web 前端、云端服务、向量数据库、额外 Agent 框架不纳入本轮范围，避免偏离参考实现。技术选型变化须有教程或源码依据。

项目经历定位为“基于 KamaClaude 二次开发的 FrogCode 本地编程 Agent”。用实际提交、测试和演示说明个人完成的工作；品牌改名与上游已有架构分别说明，性能与可靠性指标以实测为准。

## 2. 资料可用性与本地基线

| 项目 | 已核实情况 | 计划中的处理 |
|---|---|---|
| GitHub 上游 | 可读取 README、项目配置、协议文档等 | 作为技术与功能参考 |
| 语雀教程 | 当前网页工具两次访问失败，未取得密码输入页或正文；不能判断密码是否有效 | 先补齐可读取的教程目录和对应章节，再锁定实施细节 |
| 当前分支 | `stage/s0`，HEAD 为 `3bf7826` | 从当前状态准备，不直接切换并覆盖工作区 |
| 当前源码 | Git 跟踪 `.gitignore`、`LICENSE`；`src/`、`tests/` 中没有 `.py` 文件，存在缓存 | 不将缓存和既有虚拟环境视为已完成实现或验收证据 |
| 本地远程引用 | `origin/main` 及 `origin/stage/s0` 至 `origin/stage/s7` 存在 | 在实施前固定阶段提交并逐阶段比较 |
| 本地 main 参考提交 | `a7adac2c3ecb5f66460d006840aea1e5743db45f` | 仅代表现有本地引用，尚未确认与远端最新提交一致 |
| Git 远程 | `origin` 指向 KamaClaude 上游 | 发布阶段配置自己的 FrogCode 仓库，保留上游追溯关系 |
| 本地环境 | `.env`、`.venv`、`uv.lock` 存在；本次未读取 `.env` | 后续检查运行环境，密钥不写入计划或公开资料 |

语雀尚不可读，因此本计划不能声称已经逐章遵循教程。下面的 S0–S7 是依据公开阶段说明整理的初稿。公开材料对 Trace 的位置描述并不完全一致，最终以教程目录及对应阶段源码核对结果为准。

## 3. 技术路线

采用 Python 3.12、uv、Hatchling、asyncio、Pydantic v2、Textual、Anthropic SDK、python-dotenv 和 httpx；质量工具沿用 pytest、pytest-asyncio、Ruff 与 mypy strict。具体版本由参考阶段配置和可复现锁文件确定。

模型接入沿用上游 Provider 路径，先验证所用服务的协议兼容性，不仅凭模型名称认定兼容。默认开发验证使用可控的模型替身，真实模型用于单独的接入与端到端验收。

运行链路如下：

```text
frog / frog-tui
    → TCP loopback 上的 JSON-RPC 2.0 + NDJSON
    → frog-core 常驻进程
    → FrogAgentRunner / FrogAgentLoop
    → LLM Provider → FrogToolRegistry → FrogPermissionManager
    → 工具结果回填 → 下一轮执行或结束

事件输出 → FrogEventBus → CLI/TUI、events.jsonl、trace
会话管理 → session、thread、notes、上下文预算与压缩
扩展接入 → Skills、Subagents、MCP
```

TUI 是主要交互界面，CLI 承担脚本调用和调试。持久化布局、配置优先级、重试规则和扩展加载机制逐阶段对齐源码，不另造一套实现。

## 4. Frog 命名约定

以下名称为 FrogCode 的拟定规则，并非上游已存在的接口。

| 对象 | FrogCode 规则或示例 |
|---|---|
| 项目与仓库 | `FrogCode` |
| Python 包 | `frog_code`，源码根目录 `src/frog_code/` |
| 可执行入口 | `frog`、`frog-core`、`frog-tui` |
| CLI 子命令 | `frog frog-ping`、`frog frog-run`、`frog frog-chat`、`frog frog-trace` |
| 自有 CLI 参数 | 如 `--frog-host`、`--frog-port`；标准 `--help`、`--version` 保留 |
| TUI 自有斜杠命令 | `/frog-help`、`/frog-clear`、`/frog-compact`，逐个映射实际已有能力 |
| 业务函数与方法 | `frog_load_config()`、`frog_run_loop()`、`frog_execute_tool()` |
| 私有函数与方法 | `_frog_dispatch_request()` |
| 类 | `FrogCoreApp`、`FrogAgentRunner`、`FrogPermissionManager` |
| 测试与 fixture | `test_frog_ping_roundtrip()`、`frog_daemon_fixture` |
| 项目自有 RPC 方法 | `frog.core.ping`、`frog.agent.run`、`frog.session.create` |
| 项目自有事件类型 | `frog.run.started`、`frog.tool.finished`，连同订阅过滤器同步迁移 |
| 内置工具名称 | `frog_read_file`、`frog_write_file` 等，以实际工具清单为准 |
| 内置 Agent 名称 | 如 `frog_planner`、`frog_executor`、`frog_reviewer` |
| 自有环境变量 | `FROG_HOST`、`FROG_PORT`、`FROG_CONFIG` 等 |
| 用户与项目配置目录 | `~/.frog/`、项目内 `.frog/`，与参考实现保持对应层级 |
| 自有脚本 | `frog_gen_protocol_doc.py`、`frog_check_naming.py` |

命名执行边界：

1. 所有可自主命名的业务函数与方法均包含 `frog`，测试函数保留 pytest 必需的 `test_` 开头。
2. Python 特殊方法、Textual 生命周期或事件处理约定、第三方接口覆盖方法、pytest 固定钩子保留规定名称；将业务逻辑委托给 `frog_*` 方法。维护逐项说明原因的命名例外清单。
3. JSON-RPC 的 `jsonrpc`、`id`、`method`、`params` 等字段，MCP 标准方法和字段，SDK 参数与服务商要求的环境变量保持协议要求。
4. MCP 远端工具名称不直接篡改。需要向模型暴露 Frog 别名时，在适配层保留别名到真实工具名的映射，验证重名处理与转发。
5. 不改写版权、引用链接及来源记录中的 KamaClaude。其他旧品牌残留逐项检查，而非无差别全局替换。
6. CLI 子命令也加 `frog-`，从严落实“指令命名都与 Frog 有关”的要求。命令映射表在开发前固定，并同步到帮助、示例、补全及测试。

改名影响面包括 Python 导入、打包入口、反射和字符串注册、工具 schema、系统提示词、TUI 绑定、RPC 路由、事件订阅、配置路径、持久化读写、测试 fixture、脚本和文档。所有引用应随对应模块一起迁移。

## 5. 阶段计划与验收

阶段工作量为单人有效工作日估算，包含实现、理解源码和验证，不包含等待教程资料及账号配置的时间；不是教程原定工期。

| 阶段 | 主要工作 | 交付物 | 完成标准 | 估算 |
|---|---|---|---|---|
| P0 准备 | 补齐教程；固定参考提交；核对阶段差异；建立命名表及架构对照 | 教程映射表、命名规范、基线记录 | 每阶段有教程来源、源码来源和验收目标 | 1–2 天 |
| S0 骨架与协议 | 建立 FrogCode 包与入口；配置、日志、类型化消息、TCP IPC | 可启动的 `frog-core` 和 CLI | 两个独立进程完成 ping/pong；非法请求、端口占用、退出行为可验证 | 2–3 天 |
| S1 最小 Agent 闭环 | Provider、运行循环、工具注册、结果回填、运行文件 | 一次可复现的目标执行 | 模型替身完成工具调用并结束；真实模型单独跑通一次；失败有明确记录 | 2–3 天 |
| S2 事件流 | Runner 进入 daemon；事件总线、持久化、IPC 订阅 | CLI/TUI 共享的事件通路 | 事件顺序与 run 归属正确；客户端断开不意外终止 daemon 任务 | 2–3 天 |
| S3 规划与 TUI | 任务拆解、任务状态、流式展示、工具展示、Frog 视觉和帮助 | 可操作的 Frog TUI | 能演示多步任务；状态与事件一致；异常可见；按教程边界验证中断行为 | 3–4 天 |
| Trace 专项 | 串联 IPC、事件及模型调用记录，支持参考实现中的查看或回放方式 | 运行时间线与定位示例 | 可从 run 追到工具调用及错误；日志中的凭据被遮蔽 | 1–2 天 |
| S4 会话与记忆 | session、thread、notes 的创建、恢复与上下文装配 | 可续接的多轮会话 | 多轮信息可复用、不同会话隔离；重启后按参考实现恢复历史 | 2–3 天 |
| S5 工具安全 | 参数校验、审批、策略、错误分类和有限重试 | TUI 审批流程与策略记录 | 拒绝后没有工具副作用；无效参数被拦截；重试不重复执行不可重复操作 | 2–3 天 |
| S6 上下文治理 | 预算、水位、工具结果截断、自动及手动压缩 | 可观察的上下文管理 | 超预算路径可控；压缩保留目标、关键约束与必要工具关联；失败可定位 | 2–3 天 |
| S7 扩展 | Skills、子 Agent、MCP 加载和调用 | 三类扩展示例 | 各跑通一个示例；子任务遵守权限边界；MCP 断连有可理解的失败结果 | 3–4 天 |
| R1 发布 | 全链路回归、安装验证、文档、演示、GitHub 仓库与 Release | FrogCode v0.1.0 | 新环境可安装运行；关键检查通过；源码、说明与发布内容一致 | 2–3 天 |

合计约 22–33 个有效工作日。资料齐备后，以 S0 的实际耗时重新估算剩余阶段。Trace 的具体插入位置待教程核对，完成 S3 前应具备支持后续调试的基础追踪能力。

S5 之前的真实工具演示限定在临时测试目录，避免在权限链路未完成时操作正式工程。

## 6. 每个阶段的固定开发流程

1. 阅读对应教程，比较参考阶段与前一阶段的变化，记录“新增能力、调用链、设计原因”。
2. 更新教程—上游符号—Frog 符号映射，再迁移该阶段实现。保留来源与必要注释，不把所有阶段代码一次搬入后虚构开发过程。
3. 同步更新配置、协议、提示词与测试；接口模型变化后重新生成协议文档。
4. 先跑改动涉及的测试，再执行该阶段要求的静态检查和集成检查；失败解决后再进入下一阶段。
5. 记录命令、实际输出、界面截图或事件样例，以及已知限制。所有演示材料去除个人路径与密钥。
6. 更新阶段笔记并形成真实提交。开发分支使用 `codex/frog-s0` 等名称，阶段标签使用 `frog-s0` 等名称；不改写上游作者信息。

测试优先覆盖有行为风险的链路：协议与路由、事件顺序、工具副作用、权限拒绝、会话隔离、压缩边界、子任务和 MCP 转发。单纯颜色与文案调整以人工界面检查为主。

## 7. 验证与验收证据

建立项目后使用以下质量检查，命令中的路径须与当期实际目录一致：

```powershell
uv run ruff check src tests scripts
uv run mypy src
uv run pytest tests/unit -v
uv run pytest tests/integration -v
uv run python scripts/frog_gen_protocol_doc.py --check
uv run python scripts/frog_check_naming.py
```

区分无网络的确定性测试与真实模型测试；真实调用单独记录服务、模型标识、耗时、错误与费用数据（接口提供时）。检查脚本用 AST 检查函数命名，并结合有理由的例外清单；字符串扫描只负责定位旧名称，避免误删归属信息。

发布前至少保留五段可复现演示：

- 启动 daemon，通过 CLI 验证连接，再进入 TUI。
- 给出多步骤代码任务，展示规划、工具调用与执行结果。
- 对一次文件修改选择拒绝，证明目标文件未改变，再演示批准路径。
- 恢复会话并触发上下文压缩，验证关键约束仍有效。
- 各展示一个 Skill、子 Agent 和 MCP 调用，至少包含一个失败处理案例。

本机 Windows/PowerShell 是首要验证环境；GitHub CI 规划覆盖 Windows 与 Linux。平台信号、路径、进程退出和 shell 工具行为按平台验证，不在未经验证的平台上宣称支持。

## 8. 计划中的文档与仓库结构

```text
FrogCode/
  src/frog_code/                 # 沿用上游模块分层
  tests/unit/
  tests/integration/
  scripts/frog_gen_protocol_doc.py
  scripts/frog_check_naming.py
  docs/FrogCode-development-plan.md
  docs/frog-tutorial-map.md      # 教程章节、阶段、提交、验收映射
  docs/frog-naming-map.md        # 旧新名称与固定接口例外
  docs/frog-architecture.md
  docs/frog-stage-notes/         # 每阶段设计与验证记录
  docs/frog-demo.md
  docs/frog-project-experience.md
  .github/workflows/frog-ci.yml
  .frog/
  .env.example
  pyproject.toml
  uv.lock
  README.md
  RUNBOOK.md
  WIRE_PROTOCOL.md
  CHANGELOG.md
  LICENSE
```

本轮只创建开发计划；其余文件在相应阶段产生。运行数据和包含个人信息的 `.frog/` 内容不纳入版本控制，只提交经过整理的配置模板。应用型项目拟提交锁文件保障环境复现，这是相对当前忽略规则的明确工程调整。

## 9. GitHub 发布安排

保留现有 MIT 许可证及其版权声明，并在 README 写明基于 KamaClaude 二次开发、链接上游、列出 FrogCode 的实际修改。语雀教程内容仅用于开发参考，不将受限正文、访问密码或课程附件直接复制到公开仓库。

完成发布材料后配置自己的 `FrogCode` 仓库，将原来源保留为 `upstream`；确认推送目的地属于自己的仓库。个人 GitHub 命名空间与可见性在发布阶段确定，不影响当前计划。

发布清单：

- README 包含功能、架构、环境、模型配置、快速启动、命令表、演示、限制和来源。
- 使用全新环境安装构建产物，验证 `frog`、`frog-core`、`frog-tui` 三个入口。
- CI 执行适用的静态检查、测试、协议一致性和命名检查。
- `.env`、密钥、教程密码、私人会话和机器缓存不进入提交及发布附件。
- 创建首个 `v0.1.0` Release，附实际功能清单、安装方法及已知限制。
- 项目经历从实际负责的模块、遇到的问题、验证数据及演示证据提炼，不预填未经测量的优化百分比。

## 10. 下一步

第一优先级是取得可读取的语雀目录及 S0 章节，建立逐章对照表；可通过用户提供的本地导出文件或正文完成。拿到目录后修订阶段顺序，拿到各阶段正文后再落实对应实现细节。

不依赖教程的准备事项包括：命名清单、参考提交记录、目录设计与验收方案。正式实现应先完成 P0 对照，再推进 S0；当前不将“未读取教程”替换为“已完全遵循教程”的假设。

## 参考资料

- [KamaClaude 上游仓库与公开阶段目录](https://github.com/youngyangyang04/KamaClaude)
- [上游项目配置](https://github.com/youngyangyang04/KamaClaude/blob/main/pyproject.toml)
- [上游协议文档](https://github.com/youngyangyang04/KamaClaude/blob/main/WIRE_PROTOCOL.md)
- [上游开发说明](https://github.com/youngyangyang04/KamaClaude/blob/main/AGENT.md)
- [上游许可证](https://github.com/youngyangyang04/KamaClaude/blob/main/LICENSE)
- [指定语雀教程，当前未取得正文](https://www.yuque.com/chengxuyuancarl/nvd81p)

技术信息来自上游公开资料；本计划中的 Frog 名称、验收细则、工期和发布安排为针对本次需求制定的方案。
