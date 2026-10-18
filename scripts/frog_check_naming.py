"""Check FrogCode-owned Python symbols for the Frog naming convention."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FROG_METHOD_EXCEPTIONS = {
    "_on_key",  # Textual TextArea framework hook
    "action_quit",  # Textual action dispatch hook
    "all",  # Registry protocol method, qualified by a Frog class
    "close",  # Async resource lifecycle method, qualified by a Frog class
    "compose",  # Textual composition hook
    "connect",  # Socket lifecycle method, qualified by a Frog class
    "create",  # Manager protocol method, qualified by a Frog class
    "get",  # Registry protocol method, qualified by a Frog class
    "get_tools",  # MCP adapter protocol method, qualified by a Frog class
    "list_all",  # Loader protocol method, qualified by a Frog class
    "load",  # Loader protocol method, qualified by a Frog class
    "register",  # Registry/transport protocol method, qualified by a Frog class
    "resolve",  # Skill loader protocol method, qualified by a Frog class
    "run",  # Agent and daemon lifecycle method, qualified by a Frog class
    "start",  # Async resource lifecycle method, qualified by a Frog class
    "stop",  # Async resource lifecycle method, qualified by a Frog class
    "update",  # Task manager protocol method, qualified by a Frog class
}


# 判断名称是否符合 FrogCode 自有函数命名约定
def frog_function_name_is_valid(name: str, *, is_method: bool) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True
    if name.startswith("frog_") or name.startswith("_frog_"):
        return True
    if is_method and (name.startswith("on_") or name in FROG_METHOD_EXCEPTIONS):
        return True
    return False


# 检查单个 Python 文件中的类名和函数名
def frog_check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    errors: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if not node.name.startswith("Frog"):
                errors.append(f"{path}:{node.lineno}: class {node.name!r} must start with 'Frog'")
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not frog_function_name_is_valid(
                    child.name, is_method=True
                ):
                    errors.append(
                        f"{path}:{child.lineno}: method {node.name}.{child.name} must use Frog naming"
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not frog_function_name_is_valid(
            node.name, is_method=False
        ):
            errors.append(f"{path}:{node.lineno}: function {node.name!r} must use Frog naming")
    return errors


# 扫描生产源码并返回所有命名错误
def frog_check_source(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        errors.extend(frog_check_file(path))
    return errors


# 命名检查命令入口
def frog_main() -> int:
    errors = frog_check_source(Path("src/frog_code"))
    errors.extend(frog_check_source(Path("scripts")))
    errors.extend(frog_check_file(Path("frog_trace_permission_flow.py")))
    if errors:
        print("\n".join(errors))
        return 1
    print("Frog naming check passed")
    return 0


if __name__ == "__main__":
    sys.exit(frog_main())
