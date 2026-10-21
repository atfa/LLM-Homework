"""分层依赖规则测试：把架构约束变成可执行的 CI 检查。

依赖方向（只允许由外向内）：

    interfaces → application → ports → domain
    infrastructure → ports → domain
    composition → application + infrastructure
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "xagent"
STDLIB = set(sys.stdlib_module_names)

# 每层允许依赖的 xagent 子包 / 第三方顶层包
LAYER_RULES: dict[str, tuple[set[str], set[str]]] = {
    "domain": ({"xagent.domain"}, set()),
    "ports": ({"xagent.domain", "xagent.ports"}, set()),
    "application": ({"xagent.domain", "xagent.ports", "xagent.application"}, set()),
    "infrastructure": ({"xagent.domain", "xagent.ports", "xagent.infrastructure"}, {"requests"}),
    "interfaces": (
        {"xagent.domain", "xagent.ports", "xagent.application", "xagent.interfaces"},
        {"textual", "rich"},
    ),
}

# 领域层禁止出现 I/O / 网络相关导入
DOMAIN_FORBIDDEN = {"os", "pathlib", "subprocess", "socket", "shutil", "urllib", "requests", "http"}


def module_name(path: Path) -> str:
    parts = path.relative_to(ROOT.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = module_name(path)
    base = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    targets: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix_len = len(base) - (node.level - 1)
                if prefix_len <= 0:
                    continue
                prefix = base[:prefix_len]
                if node.module:
                    prefix.append(node.module)
                targets.add(".".join(prefix))
            elif node.module:
                targets.add(node.module)
    return targets


def check_layer(layer: str, allowed_xagent: set[str], allowed_third_party: set[str]) -> list[str]:
    violations: list[str] = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        for target in sorted(import_targets(path)):
            top = target.split(".")[0]
            if top == "xagent":
                if not any(target == allowed or target.startswith(allowed + ".") for allowed in allowed_xagent):
                    violations.append(f"{path.relative_to(ROOT)} -> {target}（跨层依赖）")
            elif top == "__future__" or top in STDLIB:
                continue
            elif top not in allowed_third_party:
                violations.append(f"{path.relative_to(ROOT)} -> {target}（该层不允许第三方依赖）")
    return violations


def test_layers_only_depend_inward() -> None:
    violations: list[str] = []
    for layer, (allowed_xagent, third_party) in LAYER_RULES.items():
        violations.extend(check_layer(layer, allowed_xagent, third_party))
    assert violations == [], "\n".join(violations)


def test_domain_is_pure() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "domain").rglob("*.py")):
        for target in sorted(import_targets(path)):
            if target.split(".")[0] in DOMAIN_FORBIDDEN:
                violations.append(f"{path.relative_to(ROOT)} -> {target}")
    assert violations == [], "领域层禁止 I/O 依赖：\n" + "\n".join(violations)


def test_composition_does_not_import_interfaces() -> None:
    violations = [
        target for target in import_targets(ROOT / "composition.py") if target.startswith("xagent.interfaces")
    ]
    assert violations == []


def test_entrypoint_is_the_only_place_that_may_import_all_ui_layers() -> None:
    targets = import_targets(ROOT / "entrypoint.py")
    assert any(target.startswith("xagent.composition") for target in targets)
