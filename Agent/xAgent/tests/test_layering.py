"""分层依赖规则测试：把架构约束变成可执行的 CI 检查。

顶层只有 core 与 tui 两个包，规则一句话：

    tui 只能依赖 core；core 内部依赖只能由外向内。

core 内部（依赖只能由外向内）：

    core.application → core.ports → core.domain
    core.infrastructure → core.ports → core.domain
    core.composition → core.application + core.infrastructure
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "xagent"
STDLIB = set(sys.stdlib_module_names)

# core 各层允许依赖的 xagent 子包 / 第三方顶层包
CORE_LAYER_RULES: dict[str, tuple[set[str], set[str]]] = {
    "core/domain": ({"xagent.core.domain"}, set()),
    "core/ports": ({"xagent.core.domain", "xagent.core.ports"}, set()),
    "core/application": ({"xagent.core.domain", "xagent.core.ports", "xagent.core.application"}, set()),
    "core/infrastructure": (
        {"xagent.core.domain", "xagent.core.ports", "xagent.core.infrastructure"},
        {"requests", "trafilatura"},
    ),
}

# tui 是 core 的消费者：可以用 core 的 domain / ports / application（事件契约），
# 但不得触碰 infrastructure / composition（那是组合根的职责）。
TUI_ALLOWED = {
    "xagent.core.domain",
    "xagent.core.ports",
    "xagent.core.application",
    "xagent.tui",
}
TUI_THIRD_PARTY = {"textual", "rich"}

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


def check_layer(
    layer: str,
    allowed_xagent: set[str],
    allowed_third_party: set[str],
    *,
    allowed_core: set[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        for target in sorted(import_targets(path)):
            top = target.split(".")[0]
            if top == "xagent":
                allowed = set(allowed_xagent)
                if allowed_core:
                    allowed |= allowed_core
                if not any(target == a or target.startswith(a + ".") for a in allowed):
                    violations.append(f"{path.relative_to(ROOT)} -> {target}（跨层依赖）")
            elif top == "__future__" or top in STDLIB:
                continue
            elif top not in allowed_third_party:
                violations.append(f"{path.relative_to(ROOT)} -> {target}（该层不允许第三方依赖）")
    return violations


def test_core_layers_only_depend_inward() -> None:
    violations: list[str] = []
    for layer, (allowed_xagent, third_party) in CORE_LAYER_RULES.items():
        violations.extend(check_layer(layer, allowed_xagent, third_party))
    assert violations == [], "\n".join(violations)


def test_tui_only_depends_on_core_contract() -> None:
    """tui 只能依赖 core 的契约层，不得触碰 infrastructure / composition。"""
    violations = check_layer("tui", TUI_ALLOWED, TUI_THIRD_PARTY)
    assert violations == [], "\n".join(violations)


def test_core_never_imports_tui() -> None:
    """core 不得 import tui——否则界面无法替换，core 就不再 UI 无关。"""
    violations: list[str] = []
    for path in sorted((ROOT / "core").rglob("*.py")):
        for target in sorted(import_targets(path)):
            if target == "xagent.tui" or target.startswith("xagent.tui."):
                violations.append(f"{path.relative_to(ROOT)} -> {target}")
    assert violations == [], "core 禁止依赖 tui：\n" + "\n".join(violations)


def test_domain_is_pure() -> None:
    violations: list[str] = []
    for path in sorted((ROOT / "core" / "domain").rglob("*.py")):
        for target in sorted(import_targets(path)):
            if target.split(".")[0] in DOMAIN_FORBIDDEN:
                violations.append(f"{path.relative_to(ROOT)} -> {target}")
    assert violations == [], "领域层禁止 I/O 依赖：\n" + "\n".join(violations)


def test_composition_does_not_import_tui() -> None:
    violations = [
        target for target in import_targets(ROOT / "core" / "composition.py") if target.startswith("xagent.tui")
    ]
    assert violations == []


def test_entrypoint_is_the_only_place_that_may_import_all_layers() -> None:
    targets = import_targets(ROOT / "entrypoint.py")
    assert any(target.startswith("xagent.core.composition") for target in targets)
    assert any(target.startswith("xagent.tui") for target in targets)
