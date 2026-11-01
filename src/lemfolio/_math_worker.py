"""Bounded symbolic worker. It never evaluates Python or SymPy source strings."""
from __future__ import annotations

import ast
import json
import sys


def calculate(payload: dict) -> dict:
    import sympy as s

    assumptions = payload.get("assumptions") or {}
    if not isinstance(assumptions, dict) or len(assumptions) > 32:
        raise ValueError("assumptions must be a dictionary with at most 32 symbols")
    allowed_assumptions = {"real", "positive", "negative", "nonzero", "integer", "nonnegative"}
    symbols = {}
    for name, values in assumptions.items():
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError("invalid symbol name")
        if not isinstance(values, dict) or any(k not in allowed_assumptions or not isinstance(v, bool) for k, v in values.items()):
            raise ValueError("assumptions use boolean real/positive/negative/nonzero/integer/nonnegative fields")
        symbols[name] = s.Symbol(name, **values)
    functions = {"sin": s.sin, "cos": s.cos, "tan": s.tan, "exp": s.exp, "log": s.log,
                 "sqrt": s.sqrt, "abs": s.Abs, "Abs": s.Abs, "sinh": s.sinh, "cosh": s.cosh}

    def parse(text):
        if not isinstance(text, str) or not text or len(text) > 1024:
            raise ValueError("expressions must contain 1 to 1024 characters")
        tree = ast.parse(text.replace("^", "**"), mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 180:
            raise ValueError("expression is too complex")

        def bounded(value):
            if value.is_Rational and (int(value.p).bit_length() > 4096 or int(value.q).bit_length() > 4096):
                raise ValueError("intermediate numeric value exceeds limit")
            return value

        def build(node, depth=0):
            if depth > 24:
                raise ValueError("expression is too deeply nested")
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                if len(str(node.value)) > 32 or abs(node.value) > 10**12:
                    raise ValueError("numeric constant exceeds limit")
                return s.Rational(str(node.value))
            if isinstance(node, ast.Name) and node.id.isidentifier() and not node.id.startswith("_"):
                if node.id in {"pi", "E"}:
                    return getattr(s, node.id)
                if len(symbols) >= 32 and node.id not in symbols:
                    raise ValueError("too many symbols")
                return symbols.setdefault(node.id, s.Symbol(node.id))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = build(node.operand, depth + 1)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                left, right = build(node.left, depth + 1), build(node.right, depth + 1)
                if isinstance(node.op, ast.Pow):
                    # Symbolic exponents and towers are intentionally outside this checker.
                    if not right.is_number or right.is_real is not True or abs(right) > 30:
                        raise ValueError("exponent must be a real constant between -30 and 30")
                    return bounded(left ** right)
                if isinstance(node.op, ast.Add):
                    return bounded(left + right)
                if isinstance(node.op, ast.Sub):
                    return bounded(left - right)
                if isinstance(node.op, ast.Mult):
                    return bounded(left * right)
                return bounded(left / right)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and len(node.args) == 1 and not node.keywords:
                return functions[node.func.id](build(node.args[0], depth + 1))
            raise ValueError("unsupported expression syntax; use arithmetic, symbols and approved single-argument functions")

        return build(tree.body)

    lhs, rhs = parse(payload.get("lhs")), parse(payload.get("rhs"))
    if lhs.has(s.zoo, s.oo, -s.oo, s.nan) or rhs.has(s.zoo, s.oo, -s.oo, s.nan):
        raise ValueError("undefined or infinite expression")
    difference = s.simplify(lhs - rhs)
    result = "symbolically_equivalent" if difference == 0 else "not_equivalent" if difference.is_zero is False else "inconclusive"
    counterexample = None
    if difference != 0:
        ordered = sorted(lhs.free_symbols | rhs.free_symbols, key=str)
        for value in (1, 2, s.Rational(1, 2), -1, -2, 0):
            value = s.Rational(value)
            substitutions = {symbol: value + index if value > 0 else value - index for index, symbol in enumerate(ordered)}
            if any(any(getattr(symbol, "is_" + k) is True and not getattr(substitutions[symbol], "is_" + k) for k in allowed_assumptions) for symbol in ordered):
                continue
            lvalue, rvalue = lhs.subs(substitutions), rhs.subs(substitutions)
            if lvalue.has(s.zoo, s.oo, -s.oo, s.nan) or rvalue.has(s.zoo, s.oo, -s.oo, s.nan):
                continue
            delta = s.simplify(lvalue - rvalue)
            if delta.is_zero is False and abs(complex(delta.evalf())) > 1e-10:
                counterexample = {"values": {str(k): str(v) for k, v in substitutions.items()}, "lhs": str(lvalue), "rhs": str(rvalue)}
                result = "not_equivalent"
                break
    return {"status": result, "lhs": str(lhs), "rhs": str(rhs), "difference": str(difference)[:4000],
            "assumptions": assumptions, "counterexample": counterexample,
            "evidence_class": "limited_symbolic_check", "proof": False, "domain_check": "not_performed",
            "limitations": "SymPy simplification compares expressions under stated assumptions. Domains, singularities and unstated hypotheses need human review; this is not a proof checker."}


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read(8000))
        output = calculate(payload)
    except ImportError:
        output = {"status": "unavailable", "error": "SymPy is not installed", "proof": False}
    except Exception as exc:
        output = {"status": "unsupported", "error": str(exc)[:400], "proof": False}
    print(json.dumps(output, ensure_ascii=False))
