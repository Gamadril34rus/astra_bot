"""P0 regression: arm produce_pending must not sit inside risk except."""
from __future__ import annotations

import ast
from pathlib import Path

CLOCK = Path(__file__).resolve().parents[1] / "scripts" / "zeus_live_clock.py"


def test_produce_pending_not_in_except_handler() -> None:
    src = CLOCK.read_text(encoding="utf-8")
    tree = ast.parse(src)

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []
            self.in_except: list[int] = []
            self.ok: list[int] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(type(node).__name__)
            super().generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name == "produce_pending":
                if "ExceptHandler" in self.stack:
                    self.in_except.append(node.lineno)
                else:
                    self.ok.append(node.lineno)
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    assert v.ok, "produce_pending call not found in zeus_live_clock.py"
    assert not v.in_except, (
        f"produce_pending still inside ExceptHandler at lines {v.in_except}"
    )


def test_run_arm_entries_not_in_except_handler() -> None:
    src = CLOCK.read_text(encoding="utf-8")
    tree = ast.parse(src)

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []
            self.in_except: list[int] = []
            self.ok: list[int] = []

        def generic_visit(self, node: ast.AST) -> None:
            self.stack.append(type(node).__name__)
            super().generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name == "run_arm_entries":
                if "ExceptHandler" in self.stack:
                    self.in_except.append(node.lineno)
                else:
                    self.ok.append(node.lineno)
            self.generic_visit(node)

    v = V()
    v.visit(tree)
    assert v.ok, "run_arm_entries call not found"
    assert not v.in_except, (
        f"run_arm_entries still inside ExceptHandler at lines {v.in_except}"
    )


def test_session_profile_is_applied_before_exit_and_stop_management():
    src = CLOCK.read_text(encoding="utf-8")
    applied = src.index("_sp = apply_session_to_environ()")
    parity = src.index("_par = await apply_tp_parity_v1")
    ensure = src.index("ens = await broker.ensure_protection")
    dead_tp = src.index("heal = await heal_dead_tp_legs(broker)")
    partial_be = src.index("partial = await sync_partial_breakeven")
    assert applied < parity < ensure
    assert applied < dead_tp < partial_be
    assert "trigger_r=_sp.be_trigger_r if _sp is not None else None" in src
