from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FrogAgentProfile:
    name: str
    description: str
    frog_system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""


# 按两级优先级（项目本地 > 用户全局 > 内建）查找并解析角色配置
class FrogAgentProfileLoader:
    _BUILTIN_DIR = Path(__file__).parent / "builtin"

    # 查找指定角色配置；未找到返回 None
    def load(self, name: str) -> FrogAgentProfile | None:
        for path in self._frog_search_paths(name):
            if path.exists():
                try:
                    return self._frog_parse(path, name)
                except Exception:
                    return None
        return None

    # 返回 [项目本地, 用户全局, 内建] 路径；load() 返回第一个存在的，项目本地优先级最高
    def _frog_search_paths(self, name: str) -> list[Path]:
        builtin = self._BUILTIN_DIR / f"{name}.toml"
        global_ = Path("~/.frog/agents").expanduser() / f"{name}.toml"
        local = Path(".frog/agents") / f"{name}.toml"
        return [local, global_, builtin]

    # 解析 TOML 角色配置文件
    def _frog_parse(self, path: Path, name: str) -> FrogAgentProfile:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        agent = data.get("agent", {})
        return FrogAgentProfile(
            name=name,
            description=agent.get("description", ""),
            frog_system_prompt=agent.get("frog_system_prompt", "").strip(),
            allowed_tools=agent.get("allowed_tools", []),
            model=agent.get("model", ""),
        )
