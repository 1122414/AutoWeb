from prompts.base_prompts import (
    DRISSION_CHEATSHEET, 
    TOOLBOX_DESCRIPTION,
    PromptTemplate
)

ACTION_CODE_GEN_PROMPT = PromptTemplate.critical_rule(
    "- **只做计划中的事**: 你必须且只能实现【Planner 的执行计划】中描述的操作\n"
    "- **禁止擅自扩展**: 如果计划是'点击进入详情页'，你只能点击，不能顺便爬取数据或返回，不要做任何计划之外的事\n"
    "- 🚨 **强制要求：数据提取必须使用字段级 try-except**: 提取任何页面的**每个**字段时，都**必须单独使用 try-except 包裹**提取语句。严禁将所有字段包裹在一个大 try 中，严禁使用 `if ele:` 检查元素，违反必定导致代码异常！"
) + """

将 XPath 策略转化为健壮的 Python 代码。

# 上下文 (Context)
- `tab`: 当前激活的浏览器 Tab 对象 (严禁重新实例化)。
- `strategy`: 定位策略字典。
- `results`: 结果列表 List[Dict]。

""" + TOOLBOX_DESCRIPTION + """

【工具使用铁律】
1. **爬取数据后必须保存**: 每当你采集到数据 (`results` 列表非空)，**必须调用 `toolbox.save_data(results, "output/xxx.json")`**！
2. **数据结构化要求 (CRITICAL)**：采集到的数据**必须**是 **List[Dict]** 格式，且每条 Dict **应尽量包含以下字段**（有则填写，缺失则留空 `""`）：
   - `title`: 标题/名称
   - `category`: 分类 (如 "movie", "guide", "article")
   - `platform`: 来源平台 (如 "douban", "ctrip")
   - `text` 或 `content`: 主要文本内容
   - 其他爬取到的字段也一并写入（如 rating, director, year, price 等）
   - ⚠️ **禁止**将所有内容拼成一个大字符串！必须保留字段结构！
   - **示例**：
     ```
     results.append({{"title": title, "category": "movie", "platform": "douban", "text": detail_text, "rating": rating}})
     ```
3. **尊重用户格式偏好**: 
   - 用户说"保存为CSV" → 使用 `toolbox.save_data(results, "output/data.csv")`
   - 用户说"保存为JSON" → 使用 `toolbox.save_data(results, "output/data.json")`
   - 扩展名会自动决定格式，无需传 format 参数
4. **描述性文件名**: 文件名应反映内容，如 `douban_movies.csv` 而非 `data.csv`（系统会自动加时间戳防覆盖）
5. **下载文件用 toolbox**: 需要下载图片/文件时，**必须用 `toolbox.download_file(url, path)`**，严禁用浏览器下载。
6. **API 优先**: 如果目标有 API 接口，优先用 `toolbox.http_request()` 而非浏览器渲染。

# 核心铁律 (Critical Rules)
1. **禁止实例化**: 严禁 `ChromiumPage()`。只能用 `tab`。

""" + DRISSION_CHEATSHEET + """

## 浏览器交互：点击与标签页维护规则 (CRITICAL)
操作浏览器时，必须根据 strategy 字段和页面反馈严格管理标签页，防止 Agent 在错误的页面上运行。

### 点击策略判断
- ⚠️ **严禁盲目使用 `click.for_new_tab()`**！绝大多数链接是**当前页跳转**而非新标签页！
- **检查字段**：查看 `strategy.get('opens_new_tab')` 标记。

- **模式 A：明确新标签页** (值为 `true`)
  - 必须使用 `el.click.for_new_tab()`。
  - **后续动作**：操作完成后必须 `new_tab.close()`，否则会导致浏览器内存溢出和 Observer 获取到错误的 DOM。
- **模式 B：当前页跳转或未知** (值为 `false` 或缺失)
  - **严禁**使用 `for_new_tab()`。
  - **执行方式**：使用 `el.click(by_js=True)`，JS 点击具有更好的反爬穿透性。

### 标签页计数健壮逻辑 (防死锁方案)
如果任务涉及跳转（如点击搜索结果），必须在代码中包含"状态校验"。请按以下标准模板编写：
  ```
  old_url = tab.url
  old_tab_ids = browser.tab_ids
  el.click(by_js=True)
  tab.wait(1.5, 3)
  if len(browser.tab_ids) > len(old_tab_ids):
      new_tab = browser.get_tab(browser.latest_tab)
      print(f"-> 检测到新标签页: {{new_tab.url}}")
      # 操作 new_tab...
  elif tab.url != old_url:
      print(f"-> 当前页面已跳转: {{old_url}} -> {{tab.url}}")
      # 继续在 tab 上操作...
  else:
      print(f"-> 点击后留在原页面，尝试检查页面元素变化")
  ```

### 循环爬取/翻页场景
- **列表页 -> 详情页循环**：点击进入(新标签) -> 提取数据 -> `new_tab.close()` -> 回到列表页继续。**严禁**在不关闭新标签页的情况下连续打开多个详情页。
- **翻页逻辑**：翻页操作通常不产生新标签页，仅需判断 `tab.url` 是否改变或特定元素是否刷新。

## 流程控制 - 循环安全 (CRITICAL)
- **严禁 `while True`** 和任何没有明确上限的循环！
- **所有循环必须有 max_iterations 保护**，防止死循环卡死程序：
```python
# ✅ 正确: 翻页循环必须有上限
MAX_PAGES = 20
for page_num in range(MAX_PAGES):
    # ... 采集当前页数据 ...
    try:
        next_btn = tab.ele("x://button[@class='next']")
        next_btn.click(by_js=True)
        tab.wait(2)
    except:
        print(f"-> 翻页结束，共翻 {{page_num + 1}} 页")
        break
```
- ❌ **严禁**仅靠数据量判断退出（`if count >= 40: break`），因为如果采集失败 count 永远不增长，程序就会死循环！
- ✅ **必须**用 `for ... in range(MAX)` 或同时设置最大迭代次数作为兜底出口。

## 数据安全 (Data Saving - CRITICAL)
- **严禁**手动编写 `open()`/`csv.writer()` 代码保存数据！
- **必须**使用 `toolbox.save_data(results, 'data/movies.json')`。
- `toolbox` 对象已内置，直接调用即可。它会自动处理目录创建、格式转换(json/csv)和异常捕获。

## 反幻觉 (Anti-Hallucination) & 严谨定位
- **严禁**凭空臆造 XPath。生成的代码必须基于 `strategy` 字典中的定位符。
- **原样使用**: 如果 `strategy` 中包含 `@@class=...` 或长字符串定位符，**必须原封不动**地写入代码 (`ele('@@class=...')`)。
  - **禁止自作聪明**地将其简化为 `.cls`，这会导致定位失败！
- **嵌套定位防降级 (Nested Safety)**:
  - 严禁将复杂的嵌套路径 (如 `x://div[@class='list']/ul/li`) 简化为 CSS 后代选择器 (如 `.list li`)。
  - 原因：CSS 选择器对空格敏感且层级模糊，容易误选中隐藏元素。即便看起来罗嗦，也必须使用明确的 `ele().ele()` 链式调用或完整 XPath。
- 如果 `strategy` 中缺少某字段的定位符，请在代码中打印 Warning 并跳过该字段，绝不要瞎编。

## 元素提取简洁原则 (EAFP Style - CRITICAL)
- **严禁**先用 `if ele:` 检查元素存在性再取值，这种写法多此一举且容易报错！
- **必须**直接用 `try...except` 包裹元素提取操作。
- ⚠️ **字段级粒度 (CRITICAL)**：在循环提取多个字段时，**必须为每个字段单独使用 try-except**！
  - **严禁**将整条记录的所有字段包裹在一个大的 try 块中！否则一个字段失败会导致整条记录丢失！
  - 正确模式：先创建 `row = {{}}` 字典，然后每个字段单独 try-except 赋值，最后判断是否有有效值再 append。

## 元素失效防护 (Stale Element Prevention - CRITICAL)
- ⚠️ **核心问题**: 当执行 `tab.back()` 或关闭标签页后，页面刷新，**之前获取的元素引用会全部失效** (Stale Element)！
- ⚠️ **致命错误**: 预先获取元素列表然后循环 (`items = tab.eles(); for item in items: ...`)，在第一次 `back()` 后所有 `items` 都失效！
- ✅ **正确做法**: 使用**索引循环** + **标签页计数健壮逻辑**，每次迭代**重新获取**元素列表：
  ```python
  for idx in range(len(tab.eles('.item'))):
      items = tab.eles('.item')
      item = items[idx]
      # ... 点击和采集逻辑 ...
  ```

# 输出与稳健性 (Output & Robustness)
1. **纯粹代码**: 严禁包含Markdown标记，严禁 `import`(除toolbox)，严禁 `tab = ChromiumPage()`，严禁注释，仅输出函数体逻辑
2. **防崩溃 (CRITICAL - 分层保护)**:
   - **核心流程**: 主要数据采集逻辑，失败之后报错让 Verifier 介入即可，然后注意根据反馈内容和日志修改代码
   - **非核心流程** (翻页、可选元素、辅助功能): **必须**用 `try...except` 包裹！
   - **原则**: 一个翻页按钮找不到，不应该让已采集的数据功亏一篑！

# 输入
策略: {xpath_plan}

# 输出
(仅 Python 代码，包括 print 语句)
"""

CODER_TASK_WRAPPER = """
⚠️ **【唯一任务】** - 你必须且只能完成以下计划，禁止做任何其他事情！
{plan}

---
{base_prompt}
"""
