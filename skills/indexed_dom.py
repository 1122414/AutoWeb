# ============================================================
# skills/indexed_dom.py - Index-Driven DOM 提取系统 [V7]
# ============================================================
"""
基于 Index 的 DOM 元素定位系统
借鉴 browser-use 和 agent-Browser 的优点，但保持 DrissionPage 代码生成

核心改进：
1. 只提取可交互元素，大幅减少 Token (~80% reduction)
2. 使用 Index 引用 (@e1, @e2) 代替完整 XPath
3. Observer 和 Coder 之间传递 Index，Coder 再转换为 XPath
4. 保持 DomCache 兼容
"""

import json
import re
from typing import Dict, List, Optional, Tuple, NamedTuple
from dataclasses import dataclass, asdict
from urllib.parse import urljoin


@dataclass
class IndexedElement:
    """可交互元素的索引表示"""
    index: int                    # @e1, @e2...
    tag: str                      # button, input, a...
    element_type: str             # interactive, content, form
    text: str                     # 可见文本（截断）
    xpath: str                    # 完整 XPath（给 Coder 用）
    css_selector: str            # CSS 选择器（备选）
    attributes: Dict[str, str]   # id, class, href, type...
    is_visible: bool
    bbox: Optional[Tuple[int, int, int, int]]  # (x, y, w, h)
    
    def to_compact_str(self) -> str:
        """生成 LLM 友好的紧凑表示"""
        parts = [f"[@e{self.index}] {self.tag}"]
        
        if self.text:
            parts.append(f'"{self.text[:50]}"')
        
        # 关键属性
        attrs = []
        if self.attributes.get('id'):
            attrs.append(f"id={self.attributes['id']}")
        if self.attributes.get('type'):
            attrs.append(f"type={self.attributes['type']}")
        if self.attributes.get('href'):
            attrs.append(f"href={self.attributes['href'][:30]}...")
            
        # ARIA 属性
        if self.attributes.get('aria-label'):
            attrs.append(f"aria-label={self.attributes['aria-label'][:30]}")
        if self.attributes.get('role'):
            attrs.append(f"role={self.attributes['role']}")
            
        if attrs:
            parts.append("| " + " ".join(attrs))
            
        return " ".join(parts)
    
    def to_drission_locator(self) -> str:
        """转换为 DrissionPage 定位符"""
        # 优先使用 XPath
        if self.xpath:
            return f"x:{self.xpath}"
        
        # 其次使用 ID
        if self.attributes.get('id'):
            return f"#{self.attributes['id']}"
        
        # 再次使用 CSS 选择器
        if self.css_selector:
            return self.css_selector
        
        # 兜底：使用 tag + text
        if self.text:
            return f'{self.tag}:has-text("{self.text[:20]}")'
        
        return self.tag


class IndexedDOMExtractor:
    """
    可交互元素索引提取器
    在浏览器页面执行 JS 提取所有可交互元素
    """
    
    # 可交互的标签和角色
    INTERACTIVE_TAGS = {
        'a', 'button', 'input', 'select', 'textarea', 'form',
        'details', 'summary'
    }
    
    INTERACTIVE_ROLES = {
        'button', 'link', 'textbox', 'checkbox', 'radio', 'combobox',
        'listbox', 'menuitem', 'menuitemcheckbox', 'menuitemradio',
        'option', 'searchbox', 'slider', 'spinbutton', 'switch',
        'tab', 'treeitem'
    }
    
    CONTENT_ROLES = {
        'heading', 'cell', 'gridcell', 'article', 'region', 
        'navigation', 'listitem'
    }
    
    def __init__(self, tab):
        self.tab = tab
        self.elements: List[IndexedElement] = []
        self.index_map: Dict[str, IndexedElement] = {}  # "e1" -> element
        
    def extract(self, include_content: bool = False) -> Dict:
        """
        提取页面可交互元素
        
        Args:
            include_content: 是否包含内容元素（heading, article等）
        
        Returns:
            {
                "element_list": "[@e1] button...\n[@e2] input...",  # LLM 看到的
                "total_count": 15,
                "element_map": {"e1": {...}, "e2": {...}},  # 完整信息给 Coder
                "stats": {"buttons": 3, "inputs": 5, "links": 7}
            }
        """
        js_code = self._build_extraction_js(include_content)
        raw_data = self.tab.run_js(js_code)
        
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                return {"error": "Failed to parse DOM data", "raw": raw_data[:200]}
        
        self.elements = self._parse_elements(raw_data.get('elements', []))
        self._build_index_map()
        
        return {
            "element_list": self._generate_compact_list(),
            "total_count": len(self.elements),
            "element_map": {f"e{e.index}": asdict(e) for e in self.elements},
            "stats": self._calculate_stats(),
            "url": self.tab.url
        }
    
    def _build_extraction_js(self, include_content: bool) -> str:
        """构建提取 JS"""
        content_roles_js = json.dumps(list(self.CONTENT_ROLES)) if include_content else "[]"
        
        return f"""
        (function() {{
            const INTERACTIVE_TAGS = {json.dumps(list(self.INTERACTIVE_TAGS))};
            const INTERACTIVE_ROLES = {json.dumps(list(self.INTERACTIVE_ROLES))};
            const CONTENT_ROLES = {content_roles_js};
            
            function getXPath(element) {{
                if (!element) return '';
                if (element.id && /^[a-zA-Z][a-zA-Z0-9_-]*$/.test(element.id)) {{
                    return '//*[@id="' + element.id + '"]';
                }}
                if (element === document.body) return '/html/body';
                
                let ix = 0;
                const siblings = element.parentNode ? element.parentNode.children : [];
                for (let i = 0; i < siblings.length; i++) {{
                    if (siblings[i] === element) {{
                        const parentPath = getXPath(element.parentNode);
                        return parentPath + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                    }}
                    if (siblings[i].tagName === element.tagName) ix++;
                }}
                return '';
            }}
            
            function buildCSSSelector(element) {{
                if (element.id) return '#' + element.id;
                const classes = (element.className || '').split(' ').filter(c => c && !c.match(/^(active|selected|hover|focus)/));
                if (classes.length > 0) {{
                    return element.tagName.toLowerCase() + '.' + classes.slice(0, 2).join('.');
                }}
                return element.tagName.toLowerCase();
            }}
            
            function isInteractive(element) {{
                const tag = element.tagName.toLowerCase();
                const role = element.getAttribute('role') || '';
                
                // 标签匹配
                if (INTERACTIVE_TAGS.has(tag)) return true;
                
                // ARIA 角色匹配
                if (INTERACTIVE_ROLES.has(role)) return true;
                
                // 事件监听检测
                if (element.onclick || element.getAttribute('onclick')) return true;
                if (element.getAttribute('tabindex') && element.getAttribute('tabindex') !== '-1') return true;
                
                // Cursor 检测
                const style = window.getComputedStyle(element);
                if (style.cursor === 'pointer') return true;
                
                return false;
            }}
            
            function isVisible(element) {{
                const rect = element.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                
                const style = window.getComputedStyle(element);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                
                return true;
            }}
            
            function getElementType(element) {{
                const tag = element.tagName.toLowerCase();
                const role = element.getAttribute('role') || '';
                
                if (['button', 'a', 'submit'].includes(tag) || role === 'button' || role === 'link') return 'interactive';
                if (['input', 'select', 'textarea'].includes(tag) || role === 'textbox') return 'form';
                if (CONTENT_ROLES.includes(role)) return 'content';
                return 'interactive';
            }}
            
            // 主提取逻辑
            const elements = [];
            const allElements = document.querySelectorAll('*');
            let index = 1;
            
            for (const el of allElements) {{
                if (!isInteractive(el) && !({str(include_content).lower()} && CONTENT_ROLES.includes(el.getAttribute('role') || ''))) continue;
                if (!isVisible(el)) continue;
                
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').trim().substring(0, 100);
                
                // 跳过无文本的纯容器
                if (!text && !el.getAttribute('aria-label') && !el.placeholder) {{
                    // 除非是明确的交互元素
                    if (!['button', 'a', 'input'].includes(el.tagName.toLowerCase())) continue;
                }}
                
                elements.push({{
                    index: index++,
                    tag: el.tagName.toLowerCase(),
                    element_type: getElementType(el),
                    text: text,
                    xpath: getXPath(el),
                    css_selector: buildCSSSelector(el),
                    attributes: {{
                        id: el.id || '',
                        class: (el.className || '').substring(0, 100),
                        href: el.href || el.getAttribute('href') || '',
                        type: el.type || '',
                        placeholder: el.placeholder || '',
                        'aria-label': el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || '',
                        name: el.name || '',
                        value: (el.value || '').toString().substring(0, 50)
                    }},
                    is_visible: true,
                    bbox: [Math.round(rect.left), Math.round(rect.top), 
                           Math.round(rect.width), Math.round(rect.height)]
                }});
            }}
            
            return {{
                elements: elements,
                url: window.location.href,
                title: document.title
            }};
        }})();
        """
    
    def _parse_elements(self, raw_elements: List[Dict]) -> List[IndexedElement]:
        """解析原始数据为 IndexedElement"""
        elements = []
        for data in raw_elements:
            try:
                ele = IndexedElement(
                    index=data['index'],
                    tag=data['tag'],
                    element_type=data.get('element_type', 'interactive'),
                    text=data.get('text', ''),
                    xpath=data.get('xpath', ''),
                    css_selector=data.get('css_selector', ''),
                    attributes=data.get('attributes', {}),
                    is_visible=data.get('is_visible', True),
                    bbox=tuple(data['bbox']) if data.get('bbox') else None
                )
                elements.append(ele)
            except Exception as e:
                print(f"   ⚠️ Failed to parse element: {e}")
        return elements
    
    def _build_index_map(self):
        """构建索引映射"""
        for ele in self.elements:
            self.index_map[f"e{ele.index}"] = ele
    
    def _generate_compact_list(self) -> str:
        """生成紧凑的元素列表"""
        lines = []
        for ele in self.elements:
            lines.append(ele.to_compact_str())
        return "\n".join(lines)
    
    def _calculate_stats(self) -> Dict:
        """计算统计信息"""
        stats = {}
        for ele in self.elements:
            key = ele.tag
            stats[key] = stats.get(key, 0) + 1
        return stats
    
    def get_element_by_index(self, index_ref: str) -> Optional[IndexedElement]:
        """
        通过索引引用获取元素
        
        Args:
            index_ref: "e1", "@e1", "E1" 都可接受
        """
        # 规范化索引引用
        ref = index_ref.lower().replace('@', '').strip()
        return self.index_map.get(ref)
    
    def get_drission_locator(self, index_ref: str) -> Optional[str]:
        """
        获取 DrissionPage 可用的定位符
        
        这是 Index → XPath 转换的关键函数
        """
        ele = self.get_element_by_index(index_ref)
        if ele:
            return ele.to_drission_locator()
        return None


# ============================================================
# 列表页批量处理增强
# ============================================================

class ListPageDetector:
    """
    自动检测列表页结构
    借鉴 browser-use 的 pagination detection
    """
    
    def __init__(self, tab):
        self.tab = tab
    
    def detect(self) -> Optional[Dict]:
        """
        检测页面是否包含列表结构
        
        Returns:
            {
                "is_list_page": True,
                "item_selector": "//ul[@class='list']/li",
                "item_count": 15,
                "next_button_xpath": "//a[@class='next']",
                "sample_fields": ["title", "price", "link"]
            }
        """
        js_code = """
        (function() {
            // 常见的列表选择器
            const listSelectors = [
                'ul > li', 'ol > li',
                '[class*="list"] > *',
                '[class*="item"]:not([class*="container"])',
                'table > tbody > tr',
                '[role="list"] > *',
                '[role="grid"] > *'
            ];
            
            for (const selector of listSelectors) {
                const items = document.querySelectorAll(selector);
                if (items.length >= 3) {
                    const parent = items[0].parentElement;
                    
                    // 分析列表项结构
                    const firstItem = items[0];
                    const fields = [];
                    
                    // 提取可能的字段
                    const links = firstItem.querySelectorAll('a');
                    if (links.length > 0) fields.push('link');
                    
                    const images = firstItem.querySelectorAll('img');
                    if (images.length > 0) fields.push('image');
                    
                    const prices = firstItem.innerText.match(/[$￥€]\\s*[\\d,]+(\\.\\d{2})?/);
                    if (prices) fields.push('price');
                    
                    // 查找翻页按钮
                    const nextPatterns = ['next', '下一页', '»', '>', 'suivant', 'weiter'];
                    let nextBtn = null;
                    for (const pattern of nextPatterns) {
                        const btn = document.querySelector(`a[href]:not([href="#"]):contains("${pattern}")`) ||
                                   document.querySelector(`button:contains("${pattern}")`);
                        if (btn) {
                            nextBtn = btn;
                            break;
                        }
                    }
                    
                    return {
                        is_list_page: true,
                        item_selector: selector,
                        item_count: items.length,
                        item_xpath: getXPath(items[0]).replace(/\\[\\d+\\]$/, ''),
                        parent_xpath: getXPath(parent),
                        sample_fields: fields,
                        has_next_button: !!nextBtn,
                        next_button_xpath: nextBtn ? getXPath(nextBtn) : null
                    };
                }
            }
            
            return { is_list_page: false };
            
            function getXPath(element) {
                // XPath 生成逻辑（同上）
                if (element.id) return '//*[@id="' + element.id + '"]';
                // ... 简化版
                return '';
            }
        })();
        """
        
        result = self.tab.run_js(js_code)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                return None
        
        if result and result.get('is_list_page'):
            return result
        return None


# ============================================================
# 使用示例
# ============================================================

if __name__ == "__main__":
    print("""
IndexedDOMExtractor 使用示例:

from skills.indexed_dom import IndexedDOMExtractor, ListPageDetector
from drivers.drission_driver import BrowserDriver

# 初始化浏览器
driver = BrowserDriver.get_browser()
tab = driver.get_latest_tab()
tab.get("https://example.com")

# 提取可交互元素
extractor = IndexedDOMExtractor(tab)
dom_state = extractor.extract()

print(f"找到 {dom_state['total_count']} 个可交互元素")
print("\\n元素列表（LLM 看到的）:")
print(dom_state['element_list'][:500])

# 检测列表页
list_detector = ListPageDetector(tab)
list_info = list_detector.detect()

if list_info:
    print(f"\\n检测到列表页，共 {list_info['item_count']} 项")
    print(f"翻页按钮: {list_info.get('next_button_xpath')}")

# Index → DrissionPage 转换
locator = extractor.get_drission_locator("e3")
print(f"\\n@e3 对应的 DrissionPage 定位符: {locator}")
    """)
