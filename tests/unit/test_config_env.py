from __future__ import annotations

from pathlib import Path

import pytest

from frog_code.core.config import frog_get_config


def _write_env(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# 功能：验证 .env 文件中的值被正确加载并覆盖内建默认值
# 设计：写 .env 到临时目录并 chdir 进去，清除同名系统环境变量排除干扰，确认 .env 加载路径有效
def test_dotenv_base_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "FROG_PORT=9999\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FROG_PORT", raising=False)

    cfg = frog_get_config()

    assert cfg.port == 9999


# 功能：验证系统环境变量的优先级高于 .env 文件中的值
# 设计：.env 写 9999，系统环境变量写 8888，确认最终值为 8888，对应四级优先链的顶层约束
def test_system_env_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    _write_env(env_file, "FROG_PORT=9999\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FROG_PORT", "8888")

    cfg = frog_get_config()

    assert cfg.port == 8888


# 功能：验证 .env 文件不存在时静默跳过，使用内建默认值（不抛异常）
# 设计：chdir 到空目录，清除系统环境变量，确认 frog_get_config() 不因 .env 缺失而崩溃，默认端口为 7437
def test_missing_env_file_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FROG_PORT", raising=False)

    cfg = frog_get_config()

    assert cfg.port == 7437


# 功能：验证 .env 中设置的 FROG_CONFIG 能正确影响 TOML 配置文件的加载路径
# 设计：.env 指向自定义 TOML 文件，TOML 中写入不同端口，确认 .env 在 TOML 加载前被读取（优先级链的正确顺序）
def test_dotenv_before_toml_kama_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "custom.toml"
    toml_path.write_bytes(b'[core]\nport = 5555\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, f"FROG_CONFIG={toml_path}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FROG_CONFIG", raising=False)
    monkeypatch.delenv("FROG_PORT", raising=False)

    cfg = frog_get_config()

    assert cfg.port == 5555


# 功能：验证同一变量经过完整四级优先链后，最终值为最高优先级来源（系统环境变量）
# 设计：同时设置默认值(7437)/TOML(6000)/.env(7000)/系统环境变量(8000)，确认最终值为 8000，是优先级链的综合正确性验证
def test_priority_chain_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 默认值：7437
    # TOML：6000
    # .env：7000
    # 系统环境变量：8000（最高）
    toml_path = tmp_path / "frog.toml"
    toml_path.write_bytes(b'[core]\nport = 6000\n')

    env_file = tmp_path / ".env"
    _write_env(env_file, "FROG_PORT=7000\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FROG_CONFIG", str(toml_path))
    monkeypatch.setenv("FROG_PORT", "8000")

    cfg = frog_get_config()

    assert cfg.port == 8000


# 功能：验证 FrogCode 能从 .env 读取 Anthropic 兼容网关地址并去掉末尾斜杠
# 设计：DeepSeek 等兼容服务必须将 SDK 请求改发到网关，避免模型名与 Anthropic 官方接口不匹配
def test_llm_base_url_loaded_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_env(tmp_path / ".env", "FROG_LLM_BASE_URL=https://api.deepseek.com/anthropic/\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FROG_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    cfg = frog_get_config()

    assert cfg.llm.base_url == "https://api.deepseek.com/anthropic"
