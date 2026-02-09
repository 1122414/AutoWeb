# ==============================================================================
# AutoWeb Prompt 模板系统
# ==============================================================================
# 使用 Python string.Template 替代原始 f-string 拼接，提升可维护性
# ==============================================================================

from string import Template


class PromptTemplates:
    """
    Prompt 模板管理器
    
    使用方式:
        from prompts.templates import prompts
        prompt = prompts.planner_step(task="爬取数据", finished_steps="打开页面")
    """
    
    # ==================== Planner Prompts ====================
    
    PLANNER_START = Template("""
你是一个网页自动化规划专家。

【用户任务】
$task

【当前状态】
浏览器刚启动，处于空白页/初始页。

请直接制定**第一步**计划（通常是打开目标网址）。

回复格式：
【计划已生成】
1. 打开网址 https://...
""")

    PLANNER_CONTINUE = Template("""
你是网页自动化规划专家。

⚠️ **核心禁令（违反则失败）**:
- 若计划涉及"进入新页面"，只能写"点击进入xxx"，**绝对禁止**同时规划新页面内的操作！
- **搜索也是跨页面**：搜索会跳转到结果页，必须拆分为两步（先搜索，下一轮再点击结果）
- **禁止词**: "随后"、"然后点击"、"然后返回"、"以便分析"

【用户任务】$task
【当前 URL】$current_url
【已完成步骤】$finished_steps_str

【规划规则】
1. 同页面批量操作：当前页面的遍历+翻页+保存可以一次完成
2. 跨页面跳转：只能规划一个原子操作，不能包含任何后续动作
3. **搜索操作**：只写"在搜索框输入xxx并搜索"，下一轮再规划点击结果

【回复格式】
【计划已生成】
1. [单一原子操作]

【示例】
✅ 在搜索框中输入"关键词"并执行搜索
✅ 点击搜索结果页的第一个链接
❌ 搜索"关键词"然后点击第一个结果（禁止！搜索和点击必须分开）
""")

    PLANNER_STEP = Template("""
你是一个精通网页自动化的规划专家。目前采用【迭代式规划】模式。

⚠️ **核心禁令（违反则失败）**:
- 若计划涉及"进入新页面"，只能写"点击进入xxx"，**绝对禁止**同时规划新页面内的操作！
- **禁止词**: "随后"、"然后返回"、"以便分析"、"分析结构"、"准备翻页"、"分析后"

【用户最终目标】
$task

【已完成步骤】
$finished_steps_str

【视觉辅助定位建议 (Visual Suggestions)】
$suggestions_str

【之前的失败教训】
$reflection_str

请制定**下一步**的行动计划。

【规划原则 - 核心铁律】
1. **基于证据规划 (Evidence-Based Planning - CRITICAL)**:
   - ⚠️ **你只能规划 Observer 实际观察到的元素！**
   - 如果【视觉辅助定位建议】中**没有**翻页按钮/下一页元素，**严禁**规划翻页或循环爬取多页！
   - 如果 Suggestion 中没有列表容器，**严禁**规划批量遍历！
2. **批量执行条件 (Batch Condition)**: 
   - **仅当** Observer 明确提供了 `next_page_locator` 或翻页按钮定位时，才允许规划多页循环
3. **详情页策略 (先探后批)**: 
   - **第一步（探）**: 只写"点击第一个条目进入详情页"，不写其他动作
   - **第二步（回）**: Observer 已分析详情页结构后，再规划"返回列表页"
   - **第三步（批）**: 返回列表页之后，规划批量循环爬取
4. **视觉优先**: 优先使用 Suggestion 中的定位符。
5. **任务终结**: 只有当目标彻底达成时，输出 "【任务已完成】"。

回复格式要求：
如果不结束，必须包含 "【计划已生成】" 字样，且**只有一行计划**。
如果结束，必须包含 "【任务已完成】" 字样。
""")

    # ==================== Verifier Prompts ====================
    
    VERIFIER_CHECK = Template("""
你是自动化测试验收员。请根据以下信息判断步骤是否成功。

【用户目标】$task
【当前计划】$current_plan
【当前 URL】$current_url
【执行日志】$execution_log

【验收原则】
1. **Warning 不算失败**: "Warning:"、"Failed to wait"、"没有等到新标签页" 等提示只是警告，不影响整体成功
2. **关注操作结果**: 判断计划中的核心操作是否执行成功，忽略无关紧要的副作用
3. **宽容对待非致命错误**: 只有导致任务无法继续的错误才算失败

格式:
Status: [STEP_SUCCESS | STEP_FAIL]
Summary: [简短描述]
""")

    # ==================== Error Handler Prompts ====================
    
    ERROR_RECOVERY = Template("""
系统在执行过程中遇到严重错误。
【错误信息】$error_msg
【已尝试的反思】$last_reflection

请分析是否可以重试或必须终止任务。
如果可以重试，请给出建议。
如果必须终止，请说明原因。

Status: [RETRY | TERMINATE]
Strategy: [策略描述]
""")

    # ==================== Helper Methods ====================
    
    @classmethod
    def planner_start(cls, task: str) -> str:
        return cls.PLANNER_START.substitute(task=task)
    
    @classmethod
    def planner_continue(cls, task: str, current_url: str, finished_steps_str: str) -> str:
        return cls.PLANNER_CONTINUE.substitute(
            task=task,
            current_url=current_url,
            finished_steps_str=finished_steps_str
        )
    
    @classmethod
    def planner_step(cls, task: str, finished_steps_str: str, 
                     suggestions_str: str, reflection_str: str) -> str:
        return cls.PLANNER_STEP.substitute(
            task=task,
            finished_steps_str=finished_steps_str,
            suggestions_str=suggestions_str,
            reflection_str=reflection_str
        )
    
    @classmethod
    def verifier_check(cls, task: str, current_plan: str, 
                       current_url: str, execution_log: str) -> str:
        return cls.VERIFIER_CHECK.substitute(
            task=task,
            current_plan=current_plan,
            current_url=current_url,
            execution_log=execution_log[-2000:]  # 限制日志长度
        )
    
    @classmethod
    def error_recovery(cls, error_msg: str, last_reflection: str) -> str:
        return cls.ERROR_RECOVERY.substitute(
            error_msg=error_msg,
            last_reflection=last_reflection or "None"
        )


# 全局实例
prompts = PromptTemplates()
