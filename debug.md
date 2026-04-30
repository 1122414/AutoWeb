# AutoWeb dp_cli 调试手册

## 结论

当前项目已经接入了 `dp_cli` 主链路，但还不能说“完全替换为 dp_cli”。

更准确的状态是：

- `dp_cli` 动作生成、执行、HITL 编辑、失败路由、快照观察、ActionCache、详情批处理策略都已经接入。
- 默认仍保留原来的 Python Coder + BrowserActor + CodeCache 链路。
- `DPCLI_ENABLED=False` 时，系统按旧链路运行。
- `DPCLI_ENABLED=True` 时，Coder 会优先尝试生成结构化 `generated_action`，Executor 通过 `DPCLIExecutor` 调用 `python -m dp_cli`。
- 当 dp_cli action 连续生成失败，或者任务包含下载、文件、API 等回退关键词时，会回到 Python Coder。

所以它现在是“dp_cli 双轨适配完成，可调试主链路”，不是“全项目纯 dp_cli 化”。

## 本次 dp_cli 改动入口

重点看这些文件：

```text
config.py
core/state_v2.py
core/nodes.py
main.py
prompts/dpcli_action_prompts.py
skills/dpcli_executor.py
skills/action_cache.py
skills/dpcli_crawl_policy.py
scripts/smoke_dpcli_executor.py
test/test_dpcli*.py
test/test_action_cache.py
```

## 运行前配置

在 `.env` 中先只打开最小 dp_cli 主链路：

```env
DPCLI_ENABLED=True
DPCLI_CWD=E:\GitHub\Repositories\drissionpage-cli
DPCLI_PYTHON=python
DPCLI_SESSION=autoweb-debug
DPCLI_HEADLESS=False
DPCLI_TIMEOUT_SECONDS=60
DPCLI_BATCH_TIMEOUT_SECONDS=900

DPCLI_OBSERVER_ENABLED=False
DPCLI_OBSERVER_FALLBACK_TO_DOM=True

ACTION_CACHE_ENABLED=False
ACTION_CACHE_THRESHOLD=0.75
ACTION_CACHE_STORE_PATH=./output/action_cache.json
```

先不要同时开启 snapshot observer 和 ActionCache。先让单步 action 生成和执行跑通，再逐项打开。

## 确认 drissionpage-cli 可用

在 `DPCLI_CWD` 指向的仓库里确认：

```bash
python -m dp_cli --help
```

在 AutoWeb 仓库里跑适配层冒烟：

```bash
python scripts/smoke_dpcli_executor.py
```

如果这里失败，先不要调 AutoWeb 主流程，优先检查：

- `DPCLI_CWD` 是否指向正确的 `drissionpage-cli` 仓库。
- `DPCLI_PYTHON` 是否是安装了 DrissionPage 和 dp_cli 依赖的 Python。
- 当前 Python 是否能在 `DPCLI_CWD` 下执行 `python -m dp_cli --help`。

## 推荐调试顺序

### 1. 跑单元测试

```bash
python -m unittest discover -s test -p "test_dpcli*.py" -v
python -m unittest discover -s test -p "test_action_cache.py" -v
```

当前已验证：

```text
test_dpcli*.py: 21 passed
test_action_cache.py: 2 passed
```

### 2. 跑 AutoWeb 主流程

```bash
python main.py
```

建议先用很简单的任务：

```text
打开 https://example.com
```

再试：

```text
打开 https://example.com，然后提取页面标题
```

最后再试需要交互的页面任务。

### 3. 打开 dp_cli snapshot observer

主链路跑通后，再打开：

```env
DPCLI_OBSERVER_ENABLED=True
DPCLI_OBSERVER_FALLBACK_TO_DOM=True
```

此时 Observer 会先调用 dp_cli snapshot，并将轻量视图写入：

```text
dpcli_snapshot
dpcli_snapshot_view
_observer_source=dp_cli
```

如果 snapshot 失败且 `DPCLI_OBSERVER_FALLBACK_TO_DOM=True`，系统会回退到旧 DOM Observer。

### 4. 打开 ActionCache

确认 dp_cli action 能稳定执行后，再打开：

```env
ACTION_CACHE_ENABLED=True
ACTION_CACHE_STORE_PATH=./output/action_cache.json
```

ActionCache 命中后，`CacheLookup` 会直接写入：

```text
generated_action
execution_mode=dp_cli
_action_source=action_cache
_action_cache_hit_id=<cache id>
```

如果缓存 action 执行失败，本轮会把 id 写入：

```text
_failed_action_cache_ids
```

避免同一轮反复命中坏缓存。

## 运行链路

### 默认旧链路

```text
Observer
  -> Planner
  -> CacheLookup
  -> CodeCache 或 Coder
  -> generated_code
  -> Executor BrowserActor
  -> Verifier
```

### dp_cli 主链路

```text
Observer
  -> Planner
  -> CacheLookup
  -> ActionCache 或 Coder
  -> generated_action
  -> Executor DPCLIExecutor
  -> python -m dp_cli
  -> dpcli_result
  -> Verifier
```

### dp_cli snapshot 观察链路

```text
Observer
  -> DPCLIExecutor.snapshot()
  -> dpcli_snapshot
  -> dpcli_snapshot_view
  -> Planner
```

### 详情批处理链路

```text
Executor 执行 extract 成功
  -> Verifier
  -> should_run_detail_batch()
  -> build_detail_batch_action()
  -> generated_action skill=batch-detail-extract
  -> Executor
```

注意：普通 Coder 当前不会直接生成 `batch-detail-extract`。这个动作由 Verifier 的详情策略注入。

## 关键状态字段

在调试中重点看这些 state 字段：

| 字段 | 含义 |
| --- | --- |
| `execution_mode` | `python_code` 或 `dp_cli` |
| `generated_code` | 旧链路 Python 策略代码 |
| `generated_action` | dp_cli 结构化动作 JSON |
| `dpcli_session` | dp_cli 浏览器会话名 |
| `dpcli_result` | 最近一次 dp_cli 执行结果 |
| `dpcli_snapshot` | dp_cli snapshot 原始结果 |
| `dpcli_snapshot_view` | 压缩后的 snapshot 视图 |
| `_action_source` | `llm` 或 `action_cache` |
| `_action_cache_hit_id` | 命中的 ActionCache id |
| `_failed_action_cache_ids` | 本轮失败 action 缓存黑名单 |
| `_dpcli_action_disabled` | dp_cli action 多次失败后禁用本轮 dp_cli Coder |
| `dpcli_detail_batch_ran` | 本轮是否已经触发过详情批处理 |

## 如何判断当前走的是哪条链路

看日志和 HITL 输出。

如果看到：

```text
Coder 使用 dp_cli action JSON 模式
execution_mode=dp_cli, 使用结构化 action 执行
【dp_cli action生成】
【dp_cli执行报告】
```

说明走的是 dp_cli 链路。

如果看到：

```text
代码来源: llm
当前生成的代码
BrowserActor
```

说明走的是旧 Python 链路。

## HITL 调试方法

当 Executor 前中断时：

- `execution_mode=dp_cli`：系统展示 `generated_action`。
- 可以选择编辑，动作会写入 `temp_action_edit.json`。
- 编辑后系统会读回 JSON，并用它继续执行。

一个典型 action：

```json
{
  "skill": "click",
  "params": {
    "ref": "e12"
  },
  "reason": "点击搜索按钮"
}
```

如果你想强制观察下一轮页面，可以把 action 改成：

```json
{
  "skill": "snapshot",
  "params": {},
  "reason": "重新获取页面快照"
}
```

## 当前支持的 dp_cli action

Coder 校验允许这些 action：

```text
open
snapshot
find
click
type
expand
list-items
extract
resolve-locator
session.inspect
```

Executor 额外支持：

```text
batch-detail-extract
```

`batch-detail-extract` 是详情批处理策略使用的动作，不是普通 Coder 直接生成的动作。

## 失败路由

dp_cli 执行失败后，Executor 根据错误类型路由：

| 错误类型 | 下一节点 | 含义 |
| --- | --- | --- |
| `ref_stale` | Observer | ref 过期，重新观察页面 |
| `ref_not_found` | Observer | ref 不存在，重新 snapshot/observe |
| `element_not_found` | Observer | 元素没找到 |
| `element_not_interactable` | Observer | 元素不可交互 |
| `invalid_ref_type` | Coder | action 参数类型错误 |
| `invalid_input` | Coder | action 输入错误 |
| `invalid_action` | Coder | action 本身无效 |
| 其他 | ErrorHandler | 交给通用错误处理 |

## 常见断点位置

建议从这些函数打断点：

```text
core/nodes.py::_should_use_dpcli_action
core/nodes.py::_dpcli_action_coder_node
core/nodes.py::_validate_dpcli_action
core/nodes.py::_executor_dpcli_branch
core/nodes.py::_observer_dpcli_snapshot
core/nodes.py::cache_lookup_node
core/nodes.py::verifier_node
skills/dpcli_executor.py::execute_action
skills/dpcli_executor.py::_run_raw
skills/action_cache.py::search
skills/action_cache.py::save_success
skills/dpcli_crawl_policy.py::should_run_detail_batch
skills/dpcli_crawl_policy.py::build_detail_batch_action
main.py::_needs_execution_confirmation
```

## 最小调试样例

### 只测 executor adapter

```python
from skills.dpcli_executor import DPCLIExecutor

executor = DPCLIExecutor(session="autoweb-debug", headless=False)
result = executor.execute_action({
    "skill": "open",
    "params": {"url": "https://example.com"},
    "reason": "打开测试页面"
})
print(result)
```

### 只测 action cache

```python
from skills.action_cache import ActionCacheManager

manager = ActionCacheManager("./output/action_cache.debug.json")
cache_id = manager.save_success(
    user_task="打开 example",
    goal="open example.com",
    url="https://example.com",
    action={"skill": "open", "params": {"url": "https://example.com"}},
)
print(cache_id)
print(manager.search("打开 example", "open example.com", "https://example.com"))
```

## 现在还不是完全 dp_cli 化的点

下面这些点说明项目还保留双轨，而不是完全 dp_cli 替代：

- Python Coder、BrowserActor、CodeCache 仍是有效路径。
- `DPCLI_ENABLED=False` 是默认值。
- `_should_use_dpcli_action()` 会根据 plan 关键词决定是否走 dp_cli，并对下载、文件、API 等任务回退 Python。
- dp_cli action 连续生成失败后，会设置 `_dpcli_action_disabled=True` 并回退 Python Coder。
- 普通 Coder 校验列表不包含 `batch-detail-extract`，该动作目前只由 Verifier 策略注入。
- 真实浏览器端到端能力依赖外部 `drissionpage-cli` 仓库和本地浏览器环境，不只由 AutoWeb 单元测试保证。

## 如果要推进到完全 dp_cli 化

后续可以按这个顺序做：

1. 把 Planner 的动作计划收敛为 dp_cli action vocabulary，不再依赖关键词启发。
2. 扩展 `_validate_dpcli_action()`，统一 Coder 和 Verifier 可生成 action 的能力边界。
3. 为下载、文件、API 等任务补齐 dp_cli action 或明确保留 Python fallback。
4. 将 CodeCache 逐步降级为兼容层，主缓存改为 ActionCache。
5. 增加真实浏览器端到端测试，覆盖 `open -> snapshot -> click/type -> extract -> verifier`。
6. 观察 dp_cli snapshot 质量稳定后，再考虑默认打开 `DPCLI_OBSERVER_ENABLED`。

## 快速排错

### Coder 仍然生成 Python

检查：

```env
DPCLI_ENABLED=True
```

再看当前 plan 是否包含 dp_cli 关键词，例如打开、点击、输入、搜索、提取、列表、详情。

### dp_cli action 生成失败

看：

```text
error_type=dpcli_action_json
reflections
coder_retry_count
```

连续失败后会回退 Python。

### dp_cli 执行失败

看：

```text
dpcli_result
execution_log
error_type=dpcli_<code>
```

如果是 ref 问题，通常让系统重新 Observer/snapshot。

### snapshot observer 不稳定

先关闭：

```env
DPCLI_OBSERVER_ENABLED=False
```

只保留 dp_cli Executor 主链路，等动作执行稳定后再打开 snapshot observer。

### ActionCache 命中坏动作

本轮会写入：

```text
_failed_action_cache_ids
```

也可以临时关闭：

```env
ACTION_CACHE_ENABLED=False
```

## 建议的首次调试配置

最稳配置：

```env
DPCLI_ENABLED=True
DPCLI_OBSERVER_ENABLED=False
ACTION_CACHE_ENABLED=False
DPCLI_HEADLESS=False
DPCLI_SESSION=autoweb-debug
```

进阶配置：

```env
DPCLI_ENABLED=True
DPCLI_OBSERVER_ENABLED=True
DPCLI_OBSERVER_FALLBACK_TO_DOM=True
ACTION_CACHE_ENABLED=True
DPCLI_HEADLESS=False
DPCLI_SESSION=autoweb-debug
```
