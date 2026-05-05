from DrissionPage import Chromium

def get_coder_llm_context(tab):
    """提取页面关键可交互节点，并直接生成 DrissionPage 定位语法供 Coder 使用"""
    
    # 1. 捞出所有潜在的可交互元素
    raw_elements = tab.eles('css:a, button, input, select, textarea, [role="button"]')
    
    llm_prompt_lines = []
    
    for ele in raw_elements:
        # 2. 过滤：只保留肉眼可见的元素
        if not ele.states.is_displayed:
            continue
            
        tag = ele.tag
        # 提取核心展示信息
        text = ele.text or ele.attr('value') or ele.attr('placeholder') or ele.attr('title') or ele.attr('aria-label') or ''
        text = " ".join(text.split())[:50] # 清理空白符并截断
        
        # 3. 核心逻辑：为 Coder 生成最稳健的 DP 定位字符串
        ele_id = ele.attr('id')
        ele_name = ele.attr('name')
        
        if ele_id:
            # 优先级 1: ID 定位 (DP 极简语法)
            locator = f"#{ele_id}"
        elif ele_name:
            # 优先级 2: name 属性定位
            locator = f"@@name={ele_name}"
        elif text and tag in ['a', 'button']:
            # 优先级 3: 明确的文本定位 (按钮或链接)
            locator = f"text={text}"
        else:
            # 优先级 4: 绝对 XPath 兜底 (DP 元素的 .xpath 属性直接返回它在 DOM 树中的绝对路径)
            locator = f"xpath:{ele.xpath}"
            
        # 4. 过滤无效的“盲盒”元素（没字也没 ID/Name 的直接丢弃）
        if not text and not ele_id and not ele_name:
            continue

        # 5. 组装给 Planner/Coder LLM 看的上下文
        # 明确告诉大模型：如果想操作这个元素，请用后面的定位器
        desc = f"- <{tag}> 文本/提示:'{text}'  | 定位器: '{locator}'"
        llm_prompt_lines.append(desc)

    return "\n".join(llm_prompt_lines)


# === 实际业务流程演示 ===

# 连接浏览器并获取最新标签页
browser = Chromium()  
tab = browser.latest_tab  
tab.get('https://www.wangfei.la/')  

# 1. 提取上下文喂给 LLM
context_for_llm = get_coder_llm_context(tab)
print("--- 将以下内容发送给你的 LLM ---\n")
print(context_for_llm)
print("\n------------------------------")