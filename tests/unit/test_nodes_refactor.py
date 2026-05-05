"""core/nodes 拆分重构验证测试

验证拆分后的 core/nodes/ 包结构是否满足以下要求:
1. 所有模块语法正确
2. __init__.py re-export 完整
3. 测试文件所需的私有 helper 可访问
4. 无循环导入
5. graph_v2.py 编译通过

注意: 本测试使用 AST 静态分析，避免运行时导入 side-effect 模块
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class ModuleSyntaxTests(unittest.TestCase):
    def test_all_modules_compile(self):
        nodes_dir = Path("core/nodes")
        errors = []
        for module in sorted(nodes_dir.glob("*.py")):
            try:
                ast.parse(module.read_text(encoding="utf-8"))
            except SyntaxError as e:
                errors.append(f"{module.name}: {e}")
        if errors:
            self.fail("以下模块语法错误:\n" + "\n".join(errors))

    def test_graph_v2_compiles(self):
        graph_file = Path("core/graph_v2.py")
        try:
            ast.parse(graph_file.read_text(encoding="utf-8"))
        except SyntaxError as e:
            self.fail(f"graph_v2.py 语法错误: {e}")


class ImportCompatibilityTests(unittest.TestCase):
    def test_init_py_reexports_all_nodes(self):
        init_file = Path("core/nodes/__init__.py")
        tree = ast.parse(init_file.read_text(encoding="utf-8"))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)

        expected_nodes = [
            "observer_node", "planner_node", "coder_node", "executor_node",
            "verifier_node", "error_handler_node", "cache_lookup_node", "rag_node",
        ]
        for name in expected_nodes:
            self.assertIn(name, imported_names, f"__init__.py 缺少 {name} 导出")

    def test_init_py_reexports_test_helpers(self):
        init_file = Path("core/nodes/__init__.py")
        tree = ast.parse(init_file.read_text(encoding="utf-8"))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)

        test_helpers = [
            "_extract_json_object", "_validate_dpcli_action",
            "_executor_dpcli_branch", "_compact_dpcli_snapshot",
            "_render_dpcli_snapshot_text", "_observer_dpcli_snapshot",
            "_build_verification_result",
        ]
        for name in test_helpers:
            self.assertIn(name, imported_names, f"__init__.py 缺少测试 helper: {name}")

    def test_verification_contract_compatibility(self):
        init_file = Path("core/nodes/__init__.py")
        tree = ast.parse(init_file.read_text(encoding="utf-8"))
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)

        required = [
            "_build_verification_result",
            "_coerce_verification_result",
            "_parse_verifier_result_content",
            "_looks_like_global_rewrite_plan",
        ]
        for name in required:
            self.assertIn(name, imported_names, f"__init__.py 缺少 verification_contract 所需: {name}")


class ModuleStructureTests(unittest.TestCase):
    def test_no_cycles_in_base_modules(self):
        base_modules = ["_utils.py", "_locators.py", "_verification.py"]
        forbidden = [
            "from core.nodes.observer", "from core.nodes.planner",
            "from core.nodes.coder", "from core.nodes.executor",
            "from core.nodes.verifier", "from core.nodes.cache_lookup",
            "from core.nodes.rag", "from core.nodes.error_handler",
        ]
        nodes_dir = Path("core/nodes")
        for module_name in base_modules:
            content = (nodes_dir / module_name).read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertNotIn(pattern, content, f"{module_name} 违规导入节点模块")

    def test_node_modules_have_return_annotations(self):
        nodes_dir = Path("core/nodes")
        for module_file in nodes_dir.glob("*.py"):
            if module_file.name == "__init__.py":
                continue
            tree = ast.parse(module_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and "_node" in node.name and not node.name.startswith("_"):
                    self.assertIsNotNone(
                        node.returns,
                        f"{module_file.name}:{node.lineno} {node.name} 缺少返回类型注解"
                    )


class FunctionalityTests(unittest.TestCase):
    def test_build_verification_result_via_ast(self):
        verif_file = Path("core/nodes/_verification.py")
        tree = ast.parse(verif_file.read_text(encoding="utf-8"))
        func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        self.assertIn("_build_verification_result", func_names)

    def test_planner_helpers_via_ast(self):
        planner_file = Path("core/nodes/planner.py")
        tree = ast.parse(planner_file.read_text(encoding="utf-8"))
        func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        required = ["planner_node", "_looks_like_global_rewrite_plan", "_planner_completion_is_premature", "_planner_forced_extract_plan"]
        for name in required:
            self.assertIn(name, func_names, f"planner.py 缺少函数: {name}")

    def test_dpcli_helpers_via_ast(self):
        dpcli_file = Path("core/nodes/_dpcli.py")
        tree = ast.parse(dpcli_file.read_text(encoding="utf-8"))
        func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        required = ["_extract_json_object", "_validate_dpcli_action", "_compact_dpcli_snapshot"]
        for name in required:
            self.assertIn(name, func_names, f"_dpcli.py 缺少函数: {name}")

    def test_all_nodes_defined(self):
        nodes_dir = Path("core/nodes")
        expected = {
            "observer.py": "observer_node",
            "planner.py": "planner_node",
            "cache_lookup.py": "cache_lookup_node",
            "rag.py": "rag_node",
            "coder.py": "coder_node",
            "executor.py": "executor_node",
            "verifier.py": "verifier_node",
            "error_handler.py": "error_handler_node",
        }
        for module_name, func_name in expected.items():
            module_file = nodes_dir / module_name
            tree = ast.parse(module_file.read_text(encoding="utf-8"))
            func_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            self.assertIn(func_name, func_names, f"{module_name} 缺少节点函数: {func_name}")


class GraphImportTests(unittest.TestCase):
    def test_graph_v2_imports_all_nodes(self):
        graph_file = Path("core/graph_v2.py")
        tree = ast.parse(graph_file.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core.nodes":
                for alias in node.names:
                    imports.append(alias.name)

        expected = [
            "observer_node", "planner_node", "coder_node", "executor_node",
            "verifier_node", "error_handler_node", "cache_lookup_node", "rag_node",
        ]
        for node_name in expected:
            self.assertIn(node_name, imports, f"graph_v2.py 缺少 {node_name} 导入")


if __name__ == "__main__":
    unittest.main(verbosity=2)
