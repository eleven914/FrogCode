from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FrogPermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


# 检测 bash 命令是否操作 cwd 之外路径的正则规则列表（强制触发 ASK，不可被 allow_patterns 绕过）
OUTSIDE_CWD_HEURISTICS: list[str] = [
    r"(^|\s)/[^\s]",              # absolute path
    r"(^|\s)~",                   # tilde home
    r"(^|\s)\.\.(/|$|\s)",        # parent traversal
    r"\$\{?HOME\b",               # $HOME variable
    r"\$\{?PWD\b",                # $PWD variable
    r"(^|\s|;|&&|\|\|)cd(\s|$)",  # explicit cd
]

_OUTSIDE_CWD_RE: list[re.Pattern[str]] = [re.compile(p) for p in OUTSIDE_CWD_HEURISTICS]


# 判断 bash 命令是否命中 outside-cwd 启发式规则
def frog_matches_outside_cwd(command: str) -> bool:
    return any(pat.search(command) for pat in _OUTSIDE_CWD_RE)


@dataclass
class FrogToolPolicy:
    default: FrogPermissionDecision
    allow_patterns: list[str] = field(default_factory=list)
    deny_patterns: list[str] = field(default_factory=list)


DEFAULT_POLICIES: dict[str, FrogToolPolicy] = {
    "frog_bash":       FrogToolPolicy(default=FrogPermissionDecision.ASK),
    "frog_write_file": FrogToolPolicy(default=FrogPermissionDecision.ASK),
    "frog_read_file":  FrogToolPolicy(default=FrogPermissionDecision.ALLOW),
    "frog_list_dir":   FrogToolPolicy(default=FrogPermissionDecision.ALLOW),
    "frog_note_save":  FrogToolPolicy(default=FrogPermissionDecision.ALLOW),
}

# 未在 DEFAULT_POLICIES 中登记的工具的兜底策略
_UNKNOWN_TOOL_DEFAULT = FrogPermissionDecision.ASK

# bash 参数中展示用的关键字段映射
_PREVIEW_KEY: dict[str, str] = {
    "frog_bash":       "command",
    "frog_read_file":  "path",
    "frog_write_file": "path",
    "frog_list_dir":   "path",
    "frog_note_save":  "content",
}
_PREVIEW_MAX = 60


# 为权限审批事件生成人类可读的参数摘要
def frog_param_preview(tool_name: str, params: dict[str, Any]) -> str:
    key = _PREVIEW_KEY.get(tool_name)
    if key and key in params:
        val = str(params[key])
        if len(val) > _PREVIEW_MAX:
            val = val[:_PREVIEW_MAX] + "…"
        return f"{key}={val!r}"
    snippet = str(params)
    return snippet[:_PREVIEW_MAX] if len(snippet) > _PREVIEW_MAX else snippet


# 对工具 + 参数执行 4 层静态策略评估，返回 ALLOW/DENY/ASK
def frog_evaluate(
    tool_name: str,
    params: dict[str, Any],
    policy: FrogToolPolicy | None = None,
) -> FrogPermissionDecision:
    if policy is None:
        policy = DEFAULT_POLICIES.get(tool_name)

    if policy is None:
        return _UNKNOWN_TOOL_DEFAULT

    command = str(params.get("command", "")) if tool_name == "frog_bash" else ""

    # Tier 1: deny_patterns (bash only)
    if command:
        for pat in policy.deny_patterns:
            if re.search(pat, command):
                return FrogPermissionDecision.DENY

    # Tier 2: OUTSIDE_CWD_HEURISTICS — forced ASK, not bypassable
    if command and frog_matches_outside_cwd(command):
        return FrogPermissionDecision.ASK

    # Tier 3: allow_patterns (bash only)
    if command:
        for pat in policy.allow_patterns:
            if re.search(pat, command):
                return FrogPermissionDecision.ALLOW

    # Tier 4: tool default
    return policy.default
