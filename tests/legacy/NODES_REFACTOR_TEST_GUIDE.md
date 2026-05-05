# core/nodes 拆分重构 - 调试测试方案

## 测试目标

验证 `core/nodes.py` 拆分为 `core/nodes/` 包后的正确性，确保：
1. 外部导入路径兼容（`from core.nodes import observer_node` 继续可用）
2. 所有节点函数行为未被改变
3. 测试文件能正常工作
4. LangGraph 图构建正常

## 测试环境要求

- Python 3.11+
- 项目根目录执行测试
- Windows 用户注意：部分测试因 Emoji 编码问题使用 AST 静态分析

## 快速验证命令

### 1. 语法编译检查（所有平台通用）

```bash
# 编译所有节点模块
python -c "import py_compile, os; [py_compile.compile(os.path.join('core/nodes', f), doraise=True) for f in os.listdir('core/nodes') if f.endswith('.py')]"

# 编译 graph_v2.py
python -m py_compile core/graph_v2.py
```

### 2. AST 结构验证

```bash
# 验证所有节点函数存在
python test/test_nodes_refactor.py -v
```

预期输出：12 项测试全部通过（OK）

### 3. 导入兼容性验证（Linux/macOS 或修复编码后的 Windows）

```bash
# 验证公开节点导入
python -c "from core.nodes import observer_node, planner_node, coder_node, executor_node, verifier_node, error_handler_node, cache_lookup_node, rag_node; print('OK')"

# 验证测试 helper 导入
python -c "from core.nodes import _extract_json_object, _validate_dpcli_action, _executor_dpcli_branch; print('OK')"
```

### 4. 原测试套件验证

```bash
# 运行 DP-CLI 相关测试（需修复 Windows 编码后）
python -m unittest test.test_dpcli_action_prompt -v
python -m unittest test.test_dpcli_executor_node -v
python -m unittest test.test_dpcli_observer_projection -v
python -m unittest test.test_verification_contract -v
```

## 问题排查

### Q1: `ModuleNotFoundError: No module named 'core'`

**原因**: Python 路径未包含项目根目录
**解决**:
```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)"  # Linux/macOS
set PYTHONPATH=%PYTHONPATH%;%CD%          # Windows
```

### Q2: `UnicodeEncodeError: 'gbk' codec can't encode character`

**原因**: Windows 控制台使用 GBK 编码，无法输出 Emoji
**解决**:
```bash
# 临时设置 UTF-8 编码
chcp 65001
set PYTHONIOENCODING=utf-8
```

### Q3: `ImportError: cannot import name 'xxx' from 'core.nodes'`

**原因**: `__init__.py` 缺少 re-export 或模块文件为空
**解决**:
1. 检查 `core/nodes/__init__.py` 是否包含该函数的 `from` 导入
2. 检查对应模块文件是否有内容（非 0 字节）
3. 检查模块间循环导入

### Q4: 循环导入错误

**原因**: 模块违反依赖方向（如 `_utils.py` 导入了节点模块）
**解决**:
1. 检查错误堆栈中的导入链
2. 确保基础模块（`_utils`, `_locators`, `_verification`）不导入节点模块
3. 使用函数体内延迟导入（`from skills.xxx import ...` 放在函数内部）

## 验证清单

- [ ] `python test/test_nodes_refactor.py` 全部通过
- [ ] `python -m py_compile core/graph_v2.py` 通过
- [ ] `python -m py_compile core/nodes/*.py` 全部通过
- [ ] 原 `test_dpcli_action_prompt.py` 测试通过
- [ ] 原 `test_dpcli_executor_node.py` 测试通过
- [ ] 原 `test_dpcli_observer_projection.py` 测试通过
- [ ] 原 `test_verification_contract.py` 测试通过
- [ ] `from core.nodes import *` 能导入所有 8 个节点函数

## 目录结构确认

```
core/
  nodes.py.bak          # 原单文件备份
  nodes/                # 新包结构
    __init__.py         # 统一对外导出
    _utils.py           # 通用工具
    _locators.py        # Locator 处理
    _verification.py    # Verification 结果
    _cache.py           # Cache 操作
    _context.py         # 上下文裁剪
    _dpcli.py           # DP-CLI 工具
    observer.py         # Observer 节点
    planner.py          # Planner 节点
    cache_lookup.py     # CacheLookup 节点
    rag.py              # RAGNode 节点
    coder.py            # Coder 节点
    executor.py         # Executor 节点
    verifier.py         # Verifier 节点
    error_handler.py    # ErrorHandler 节点
```

## 回滚方案

如拆分后出现问题，可快速回滚：

```bash
# 恢复原始单文件
git checkout main -- core/nodes.py
rm -rf core/nodes/

# 或切换到拆分前分支
git checkout main
```
