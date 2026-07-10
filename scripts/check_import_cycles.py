#!/usr/bin/env python3
"""check_import_cycles.py — app/ 模块级 import 循环依赖测量(只读,零副作用).

背景:codex-260710-suggestion §2.1 报 4 组循环,architecture-audit-v0.55 报 6 组,
必有一个基于过时快照。本脚本给出可重复的基线:AST 静态解析(含函数内延迟
import,单独标注)→ 模块级有向图 → Tarjan SCC → 输出每组成员与成环边。

用法:
    python scripts/check_import_cycles.py            # 全部(含延迟 import 的环)
    python scripts/check_import_cycles.py --top-only # 只统计模块顶层 import 的环
    python scripts/check_import_cycles.py --edges    # 附带打印每组的成环边

退出码:0 = 无环;1 = 有环(可挂 CI 冻结新增)。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"


def module_name(py: Path) -> str:
    rel = py.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def collect_imports(py: Path) -> tuple[set[str], set[str]]:
    """Return (top_level_targets, deferred_targets) as dotted module names."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"[warn] 解析失败 {py}: {exc}", file=sys.stderr)
        return set(), set()

    mod = module_name(py)
    pkg_parts = mod.split(".")[:-1]

    top: set[str] = set()
    deferred: set[str] = set()

    def _is_type_checking_guard(node: ast.If) -> bool:
        t = node.test
        return (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
            isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"
        )

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0  # >0 ⇒ 函数/方法体内(延迟 import)

        def visit_FunctionDef(self, node):  # noqa: N802
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_If(self, node):  # noqa: N802
            # `if TYPE_CHECKING:` 只在类型检查期存在, 不构成运行时依赖边
            if _is_type_checking_guard(node):
                for n in node.orelse:
                    self.visit(n)
                return
            self.generic_visit(node)

        def _add(self, name: str) -> None:
            if not name.startswith("app"):
                return
            (deferred if self.depth else top).add(name)

        def visit_Import(self, node):  # noqa: N802
            for a in node.names:
                self._add(a.name)

        def visit_ImportFrom(self, node):  # noqa: N802
            if node.level:  # relative import → 折算绝对
                base = pkg_parts[: len(pkg_parts) - node.level + 1]
                stem = ".".join(base + ([node.module] if node.module else []))
            else:
                stem = node.module or ""
            if stem:
                self._add(stem)
                # `from X import Y` 的 Y 可能本身是子模块
                for a in node.names:
                    self._add(f"{stem}.{a.name}")

    V().visit(tree)
    return top, deferred


def build_graph(top_only: bool) -> dict[str, set[str]]:
    files = {module_name(p): p for p in APP.rglob("*.py")}
    known = set(files)
    graph: dict[str, set[str]] = {m: set() for m in known}
    for mod, py in files.items():
        top, deferred = collect_imports(py)
        targets = top if top_only else top | deferred
        for t in targets:
            # 归一到已知模块(from app.x import name → app.x)
            while t and t not in known:
                t = t.rpartition(".")[0]
            if t and t != mod:
                graph[mod].add(t)
    return graph


def tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = [0]
    sys.setrecursionlimit(100000)

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in sorted(graph):
        if v not in index:
            strong(v)
    return sorted(out, key=len, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-only", action="store_true", help="只统计模块顶层 import")
    ap.add_argument("--edges", action="store_true", help="打印每组的组内边")
    args = ap.parse_args()

    graph = build_graph(top_only=args.top_only)
    sccs = tarjan_scc(graph)
    scope = "顶层 import" if args.top_only else "顶层+延迟 import"
    print(f"图规模: {len(graph)} 模块 / "
          f"{sum(len(v) for v in graph.values())} 边 ({scope})")
    if not sccs:
        print("✅ 无循环依赖组")
        return 0
    print(f"❌ {len(sccs)} 组循环依赖:")
    for i, comp in enumerate(sccs, 1):
        print(f"\n[{i}] {len(comp)} 模块:")
        for m in comp:
            print(f"    {m}")
        if args.edges:
            cs = set(comp)
            for m in comp:
                for t in sorted(graph[m] & cs):
                    print(f"      {m} -> {t}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
