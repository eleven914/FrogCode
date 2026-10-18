from __future__ import annotations

from pathlib import Path

import pytest

from frog_code.core.skills.loader import FrogSkill, FrogSkillLoader


# 功能：内建 review skill 应能被 FrogSkillLoader 查找到
# 设计：直接调用 resolve("frog_review")，不依赖文件系统之外的任何状态
def test_builtin_skill_found() -> None:
    loader = FrogSkillLoader()
    skill = loader.resolve("frog_review")
    assert skill is not None
    assert skill.name == "frog_review"
    assert "审查" in skill.description or "frog_review" in skill.description.lower()
    assert skill.system_prompt_template != ""


# 功能：内建 init / summarize / orchestrate skill 均可找到
# 设计：列举所有内建 skill 名，断言均能解析
@pytest.mark.parametrize("name", ["frog_init", "frog_review", "frog_summarize", "frog_orchestrate"])
def test_all_builtin_skills_found(name: str) -> None:
    loader = FrogSkillLoader()
    skill = loader.resolve(name)
    assert skill is not None, f"builtin skill '{name}' not found"


# 功能：不存在的 skill 名应返回 None
# 设计：查找一个不存在的名称，断言 resolve 返回 None 而非抛异常
def test_unknown_skill_returns_none() -> None:
    loader = FrogSkillLoader()
    result = loader.resolve("nonexistent_skill_xyz")
    assert result is None


# 功能：frog_render_prompt 应将 $ARGUMENTS 替换为传入的参数字符串
# 设计：构造含 $ARGUMENTS 的 skill，验证 frog_render_prompt 结果不含 "$ARGUMENTS" 且含参数值
def test_arguments_substituted() -> None:
    loader = FrogSkillLoader()
    skill = FrogSkill(
        name="test",
        description="test skill",
        system_prompt_template="Review this: $ARGUMENTS\nPlease be thorough.",
        allowed_tools=[],
    )
    rendered = loader.frog_render_prompt(skill, "src/foo.py")
    assert "$ARGUMENTS" not in rendered
    assert "src/foo.py" in rendered


# 功能：frontmatter 中的 allowed_tools 列表应被正确解析
# 设计：构造含 allowed_tools 的 Markdown 文件，通过 _frog_parse_skill_file 解析并验证结果
def test_frontmatter_parsed(tmp_path: Path) -> None:
    from frog_code.core.skills.loader import _frog_parse_skill_file

    content = """\
---
name: custom
description: 自定义 skill 测试
allowed_tools:
  - frog_read_file
  - frog_bash
---
你是一个测试助手，目标：$ARGUMENTS
"""
    p = tmp_path / "custom.md"
    p.write_text(content, encoding="utf-8")
    skill = _frog_parse_skill_file(p)
    assert skill.name == "custom"
    assert skill.description == "自定义 skill 测试"
    assert "frog_read_file" in skill.allowed_tools
    assert "frog_bash" in skill.allowed_tools
    assert "$ARGUMENTS" in skill.system_prompt_template


# 功能：无 frontmatter 的 Markdown 文件仍可加载，allowed_tools 为空列表
# 设计：写入纯正文 Markdown，断言解析成功且 allowed_tools=[]
def test_no_frontmatter(tmp_path: Path) -> None:
    from frog_code.core.skills.loader import _frog_parse_skill_file

    content = "你是助手，请帮助用户完成任务：$ARGUMENTS\n"
    p = tmp_path / "plain.md"
    p.write_text(content, encoding="utf-8")
    skill = _frog_parse_skill_file(p)
    assert skill.name == "plain"
    assert skill.allowed_tools == []
    assert "你是助手" in skill.system_prompt_template


# 功能：项目本地 skill 应覆盖内建同名 skill
# 设计：在 .frog/skills/ 中写入同名文件，用 monkeypatch 修改 cwd，断言加载到的是本地版本
def test_project_overrides_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_skills = tmp_path / ".frog" / "skills"
    local_skills.mkdir(parents=True)
    (local_skills / "frog_review.md").write_text(
        "---\nname: frog_review\ndescription: local override\n---\nlocal system prompt $ARGUMENTS\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    loader = FrogSkillLoader()
    skill = loader.resolve("frog_review")
    assert skill is not None
    assert skill.description == "local override"
    assert "local system prompt" in skill.system_prompt_template
