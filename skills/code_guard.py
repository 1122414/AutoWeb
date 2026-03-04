import ast
from typing import Dict, List


_BLOCKED_MODULE_PREFIXES = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "pathlib",
    "socket",
    "ctypes",
    "multiprocessing",
    "importlib",
}

_BLOCKED_BUILTIN_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "input",
    "breakpoint",
}

_BLOCKED_DOTTED_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "shutil.rmtree",
}

_BLOCKED_DUNDER_ATTRS = {
    "__subclasses__",
    "__globals__",
    "__code__",
    "__mro__",
}


def _is_blocked_module(module_name: str) -> bool:
    if not module_name:
        return False
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in _BLOCKED_MODULE_PREFIXES
    )


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.reasons: List[str] = []

    def _add_reason(self, text: str):
        if text not in self.reasons:
            self.reasons.append(text)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            if _is_blocked_module(alias.name):
                self._add_reason(f"禁止导入模块: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module_name = node.module or ""
        if _is_blocked_module(module_name):
            self._add_reason(f"禁止导入模块: {module_name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        callee = _dotted_name(node.func)
        if callee in _BLOCKED_DOTTED_CALLS:
            self._add_reason(f"禁止调用: {callee}")

        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_BUILTIN_CALLS:
            self._add_reason(f"禁止调用内建函数: {node.func.id}")

        root = callee.split(".", 1)[0] if callee else ""
        if _is_blocked_module(root):
            self._add_reason(f"禁止调用高危模块能力: {callee}")

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in _BLOCKED_DUNDER_ATTRS:
            self._add_reason(f"禁止访问高危属性: {node.attr}")
        self.generic_visit(node)


def scan_code_safety(code: str, max_reasons: int = 8) -> Dict[str, object]:
    source = (code or "").strip()
    if not source:
        return {"is_safe": False, "reasons": ["代码为空，拒绝执行"]}

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {
            "is_safe": False,
            "reasons": [f"语法错误: {e.msg} (line {e.lineno})"],
        }

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    reasons = visitor.reasons[:max_reasons]
    return {"is_safe": len(reasons) == 0, "reasons": reasons}
