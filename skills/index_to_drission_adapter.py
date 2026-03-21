"""
Index-to-DrissionPage Adapter
将基于 Index 的 Observer 输出转换为 DrissionPage 代码
"""

from typing import Dict, Optional
from skills.indexed_dom import IndexedDOMExtractor


class IndexToDrissionAdapter:
    """
    Index → DrissionPage 代码适配器
    
    核心职责：
    1. 接收 Observer 的 Index 引用（如 @e5）
    2. 查询 IndexedDOMExtractor 获取实际 XPath
    3. 生成可执行的 DrissionPage Python 代码
    """
    
    def __init__(self, extractor: IndexedDOMExtractor):
        self.extractor = extractor
        
    def generate_code(self, observer_result: Dict) -> str:
        """
        生成 DrissionPage 可执行代码
        
        Args:
            observer_result: Observer 返回的 JSON
                {
                    "element_ref": "@e5",
                    "action": "click",
                    "value": null,
                    "opens_new_tab": false
                }
        
        Returns:
            可执行的 Python 代码字符串
        """
        ref = observer_result.get('element_ref', '').replace('@', '')
        action = observer_result.get('action', 'click')
        value = observer_result.get('value')
        opens_new_tab = observer_result.get('opens_new_tab', False)
        
        # 获取 DrissionPage 定位符
        locator = self.extractor.get_drission_locator(ref)
        
        if not locator:
            return f"""# 错误：无法找到元素 {ref}
raise ElementNotFoundError("Element {ref} not found in current page")
"""
        
        # 根据操作类型生成代码
        code_generators = {
            'click': self._gen_click_code,
            'input': self._gen_input_code,
            'extract': self._gen_extract_code,
            'scroll': self._gen_scroll_code,
        }
        
        generator = code_generators.get(action, self._gen_click_code)
        return generator(locator, value, opens_new_tab, ref)
    
    def _gen_click_code(self, locator: str, value, opens_new_tab: bool, ref: str) -> str:
        """生成点击代码"""
        code = f"""# 点击元素 {ref}
ele = tab.ele("{locator}")
if ele:
    ele.click()
    tab.wait.load_start()
    tab.wait(0.5)
else:
    raise ElementNotFoundError("Element {ref} not found: {locator}")
"""
        if opens_new_tab:
            code += """
# 处理新标签页
tab = browser.latest_tab
tab.wait(0.5)
"""
        return code
    
    def _gen_input_code(self, locator: str, value: str, opens_new_tab: bool, ref: str) -> str:
        """生成输入代码"""
        safe_value = (value or '').replace('"', '\\"')
        return f"""# 输入文本到元素 {ref}
ele = tab.ele("{locator}")
if ele:
    ele.clear()
    ele.input("{safe_value}")
    tab.wait(0.3)
else:
    raise ElementNotFoundError("Input {ref} not found: {locator}")
"""
    
    def _gen_extract_code(self, locator: str, value, opens_new_tab: bool, ref: str) -> str:
        """生成数据提取代码"""
        return f"""# 提取元素 {ref} 的数据
ele = tab.ele("{locator}")
if ele:
    data = {{
        "text": ele.text,
        "href": ele.attr("href") if ele.attr("href") else None,
        "src": ele.attr("src") if ele.attr("src") else None
    }}
    results.append(data)
else:
    print(f"Warning: Element {ref} not found for extraction")
"""
    
    def _gen_scroll_code(self, locator: str, value, opens_new_tab: bool, ref: str) -> str:
        """生成滚动代码"""
        return f"""# 滚动页面
tab.scroll.to_bottom()
tab.wait(0.5)
"""
    
    def generate_batch_code(self, list_info: Dict) -> str:
        """
        生成列表页批量处理代码
        
        Args:
            list_info: 列表页信息
                {
                    "item_locator": "//ul/li",
                    "next_button_locator": "//a[@class='next']",
                    "item_fields": ["title", "price"]
                }
        """
        item_locator = list_info.get('item_locator', '')
        next_locator = list_info.get('next_button_locator', '')
        fields = list_info.get('item_fields', ['text'])
        
        # 生成字段提取代码
        field_extractors = []
        for field in fields:
            if field == 'text':
                field_extractors.append('        "text": item.text,')
            elif field == 'link':
                field_extractors.append('        "link": item.ele("tag:a").attr("href") if item.ele("tag:a", timeout=0.5) else None,')
            elif field == 'image':
                field_extractors.append('        "image": item.ele("tag:img").attr("src") if item.ele("tag:img", timeout=0.5) else None,')
            else:
                field_extractors.append(f'        "{field}": item.ele(f"@@class={field}").text if item.ele(f"@@class={field}", timeout=0.5) else "",')
        
        field_code = "\n".join(field_extractors)
        
        code = f"""# 批量提取列表数据
page_count = 0
max_pages = 10

while page_count < max_pages:
    page_count += 1
    print(f"Processing page {{page_count}}...")
    
    # 获取当前页所有列表项
    items = tab.eles("{item_locator}")
    print(f"Found {{len(items)}} items")
    
    for i, item in enumerate(items, 1):
        try:
            data = {{
                "page": page_count,
                "index": i,
{field_code}
            }}
            results.append(data)
        except Exception as e:
            print(f"Item {{i}} failed: {{e}}")
    
    # 翻页
    try:
        next_btn = tab.ele("{next_locator}", timeout=2)
        if next_btn and next_btn.is_enabled():
            next_btn.click()
            tab.wait.load_start()
            tab.wait(1)
        else:
            print("No more pages")
            break
    except Exception as e:
        print(f"Pagination ended: {{e}}")
        break

print(f"Total collected: {{len(results)}} items from {{page_count}} pages")
"""
        return code
