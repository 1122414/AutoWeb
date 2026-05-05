import unittest

from skills.code_guard import scan_code_safety


class CodeGuardTests(unittest.TestCase):
    def test_safe_automation_code(self):
        code = """
results = []
print("start")
items = tab.eles('x://div')
toolbox.save_data(results, 'output/demo.json')
"""
        result = scan_code_safety(code)
        self.assertTrue(result["is_safe"])
        self.assertEqual(result["reasons"], [])

    def test_block_import_os(self):
        code = "import os\nprint('x')"
        result = scan_code_safety(code)
        self.assertFalse(result["is_safe"])
        self.assertTrue(any("禁止导入模块" in r for r in result["reasons"]))

    def test_block_os_system_call(self):
        code = "os.system('echo hi')"
        result = scan_code_safety(code)
        self.assertFalse(result["is_safe"])
        self.assertTrue(any("os.system" in r for r in result["reasons"]))

    def test_block_subprocess_call(self):
        code = "subprocess.run(['cmd', '/c', 'dir'])"
        result = scan_code_safety(code)
        self.assertFalse(result["is_safe"])
        self.assertTrue(any("subprocess.run" in r for r in result["reasons"]))

    def test_block_builtin_exec_and_open(self):
        code = "exec('print(1)')\nopen('a.txt', 'w')"
        result = scan_code_safety(code)
        self.assertFalse(result["is_safe"])
        self.assertTrue(any("禁止调用内建函数" in r for r in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
