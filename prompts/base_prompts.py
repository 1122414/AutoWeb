class PromptTemplate:
    @staticmethod
    def critical_rule(text: str) -> str:
        return f"⚠️ **最高优先级规则 - 违反则失败**:\n{text}"
    
    @staticmethod
    def warning_block(text: str) -> str:
        return f"🚨 {text}"


LOCATOR_SAFETY_RULES = """
【定位安全性规则】
1. **禁止 contains(@class) 子串陷阱**: 使用精确匹配或空格边界匹配
2. **多类名处理**: 必须使用全量匹配，严禁只取部分
3. **禁止 CSS 后代选择器**: 必须使用 XPath 或链式结构
4. **空格敏感**: Class 可能包含额外空格，必须原样保留
5. **对象原则**: 必须定位到 Element 节点，严禁定位到 TextNode 或 Attribute
""".strip()

TOOLBOX_DESCRIPTION = """
# 🔧 工具箱 (Toolbox)
`toolbox` 对象已注入，包含以下工具：

| 工具 | 用途 | 调用示例 |
|------|------|---------|
| `toolbox.save_data(data, filename)` | 保存数据到文件 | `toolbox.save_data(results, "data.json")` |
| `toolbox.http_request(url)` | 发送 HTTP 请求 | `toolbox.http_request("https://api.example.com")` |
| `toolbox.download_file(url, path)` | 下载文件 | `toolbox.download_file(img_url, "cover.jpg")` |
| `toolbox.db_insert(table, data)` | 插入数据库 | `toolbox.db_insert("movies", data)` |
| `toolbox.notify(msg)` | 发送通知 | `toolbox.notify("任务完成")` |
| `toolbox.clean_html(html)` | 清洗 HTML | `toolbox.clean_html(el.html)` |

**快捷别名**:
- `save_data(...)` = `toolbox.save_data(...)`
- `http_request(...)` = `toolbox.http_request(...)`
""".strip()

DRISSION_CHEATSHEET = """
## DrissionPage v4 语法速查
- **跳转**: `tab.get(url)`
- **查询列表**: `tab.eles('x://div')`
- **查询单个**: `ele.ele('x://span')`
- **读取文本**: `el.text`
- **读取属性**: `el.attr('href')`
- **读取链接**: `el.link` (绝对 URL)
- **点击**: `el.click(by_js=True)`
- **输入**: `el.input('text')`
- **等待加载**: `tab.wait.load_start()`
- **等待元素**: `tab.wait.ele_displayed('x://...')`
- **状态检查**: `el.states.is_displayed`, `el.states.is_enabled`
- **新标签页**: `new_tab = el.click.for_new_tab()`
""".strip()
