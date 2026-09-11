"""AST interpreter for verification expressions.

No eval/exec, no imports, no attribute access, no filesystem/network.
Named quantities come from the caller (typically Pint Quantity objects).
"""

from __future__ import annotations

import ast
import math
import operator
import time
from typing import Any

from ai_lab.checks.units import IncompatibleDimensionsError

# Names that must never appear even as innocent-looking identifiers.
_BANNED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "classmethod",
        "staticmethod",
        "property",
        "type",
        "memoryview",
        "exit",
        "quit",
        "help",
        "print",
        "os",
        "sys",
        "subprocess",
        "pathlib",
        "importlib",
        "builtins",
        "__builtins__",
    }
)

# Integer exponent cap — 2**1e7 would hang/OOM before the wall-clock timeout fires.
_MAX_ABS_EXPONENT = 1000

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Load,
    ast.Constant,
    ast.Name,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.And,
    ast.Or,
    ast.BoolOp,
    ast.IfExp,
)

_BINOPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_UNARY: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}

_CMP: dict[type[ast.cmpop], Any] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class ForbiddenExpressionError(ValueError):
    """Expression uses a disallowed AST node, name, or exceeds size limits."""


class EvaluationTimeoutError(TimeoutError):
    """Wall-clock limit hit while interpreting an expression."""


def _sqrt(x: Any) -> Any:
    if hasattr(x, "units"):
        return x ** 0.5
    return math.sqrt(float(x))


def _abs(x: Any) -> Any:
    return abs(x)


def _log(x: Any) -> Any:
    if hasattr(x, "units"):
        return math.log(float(x.to_base_units().magnitude))
    return math.log(float(x))


def _exp(x: Any) -> Any:
    if hasattr(x, "units"):
        if x.dimensionless:
            return math.exp(float(x.magnitude))
        raise TypeError("exp() requires a dimensionless argument")
    return math.exp(float(x))


_FUNCTIONS: dict[str, Any] = {
    "sqrt": _sqrt,
    "abs": _abs,
    "min": min,
    "max": max,
    "round": round,
    "log": _log,
    "exp": _exp,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "true": True,  # lowercase aliases for sanity-check conditions
    "false": False,
}


class SafeExpressionEvaluator:
    """Recursive AST walker. Never calls eval/exec/compile."""

    def __init__(
        self,
        *,
        max_ast_nodes: int,
        max_expression_chars: int,
        deadline_monotonic: float | None = None,
    ) -> None:
        self.max_ast_nodes = max_ast_nodes
        self.max_expression_chars = max_expression_chars
        self.deadline_monotonic = deadline_monotonic

    def evaluate(self, expression: str, names: dict[str, Any]) -> Any:
        if not expression or not expression.strip():
            raise ForbiddenExpressionError("Empty expression")
        if len(expression) > self.max_expression_chars:
            raise ForbiddenExpressionError(
                f"Expression exceeds max_expression_chars={self.max_expression_chars}"
            )
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ForbiddenExpressionError(f"Invalid expression: {exc}") from exc

        n_nodes = sum(1 for _ in ast.walk(tree))
        if n_nodes > self.max_ast_nodes:
            raise ForbiddenExpressionError(
                f"Expression exceeds max_ast_nodes={self.max_ast_nodes} (got {n_nodes})"
            )
        self._validate(tree)
        return self._eval(tree.body, names)

    def _check_deadline(self) -> None:
        if self.deadline_monotonic is not None and time.monotonic() > self.deadline_monotonic:
            raise EvaluationTimeoutError("Expression evaluation exceeded max_computation_seconds")

    def _validate(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                raise ForbiddenExpressionError("Attribute access is not allowed")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ForbiddenExpressionError("Import is not allowed")
            if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
                raise ForbiddenExpressionError(f"Forbidden name: {node.id}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BANNED_NAMES:
                raise ForbiddenExpressionError(f"Forbidden call: {node.func.id}")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
                raise ForbiddenExpressionError(
                    f"Constant type not allowed: {type(node.value).__name__}"
                )
            if not isinstance(node, _ALLOWED_NODES):
                raise ForbiddenExpressionError(f"Disallowed AST node: {type(node).__name__}")

    def _eval(self, node: ast.AST, names: dict[str, Any]) -> Any:
        self._check_deadline()
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            if node.id in _FUNCTIONS:
                return _FUNCTIONS[node.id]
            raise ForbiddenExpressionError(f"Unknown name: {node.id}")
        if isinstance(node, ast.UnaryOp):
            op = _UNARY.get(type(node.op))
            if op is None:
                raise ForbiddenExpressionError(f"Unary operator not allowed: {type(node.op).__name__}")
            return op(self._eval(node.operand, names))
        if isinstance(node, ast.BinOp):
            op = _BINOPS.get(type(node.op))
            if op is None:
                raise ForbiddenExpressionError(f"Operator not allowed: {type(node.op).__name__}")
            left = self._eval(node.left, names)
            right = self._eval(node.right, names)
            if isinstance(node.op, ast.Pow):
                self._guard_exponent(right)
            try:
                return op(left, right)
            except IncompatibleDimensionsError:
                raise
            except Exception as exc:
                # Pint raises DimensionalityError / OffsetUnitCalculusError on bad ops.
                name = type(exc).__name__
                if "Dimensionality" in name or "OffsetUnit" in name:
                    raise IncompatibleDimensionsError(str(exc)) from exc
                raise
        if isinstance(node, ast.BoolOp):
            values = [self._eval(v, names) for v in node.values]
            if isinstance(node.op, ast.And):
                result = True
                for v in values:
                    result = result and v
                    if not result:
                        return result
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for v in values:
                    result = result or v
                    if result:
                        return result
                return result
            raise ForbiddenExpressionError(f"Bool operator not allowed: {type(node.op).__name__}")
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, names)
            for op_node, comparator in zip(node.ops, node.comparators):
                fn = _CMP.get(type(op_node))
                if fn is None:
                    raise ForbiddenExpressionError(f"Comparator not allowed: {type(op_node).__name__}")
                right = self._eval(comparator, names)
                try:
                    ok = fn(left, right)
                except Exception as exc:
                    name = type(exc).__name__
                    if "Dimensionality" in name:
                        raise IncompatibleDimensionsError(str(exc)) from exc
                    raise
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body, names) if self._eval(node.test, names) else self._eval(node.orelse, names)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ForbiddenExpressionError("Only named functions may be called")
            if node.keywords:
                raise ForbiddenExpressionError("Keyword arguments are not allowed")
            func_name = node.func.id
            if func_name not in _FUNCTIONS:
                raise ForbiddenExpressionError(f"Function not allowed: {func_name}")
            args = [self._eval(a, names) for a in node.args]
            return _FUNCTIONS[func_name](*args)
        raise ForbiddenExpressionError(f"Disallowed AST node: {type(node).__name__}")

    def _guard_exponent(self, exponent: Any) -> None:
        mag: float
        if hasattr(exponent, "magnitude") and getattr(exponent, "dimensionless", False):
            mag = float(exponent.magnitude)
        elif isinstance(exponent, (int, float)):
            mag = float(exponent)
        else:
            raise ForbiddenExpressionError("Exponent must be dimensionless")
        if abs(mag) > _MAX_ABS_EXPONENT:
            raise ForbiddenExpressionError(
                f"Exponent {mag} exceeds safety cap {_MAX_ABS_EXPONENT}"
            )
