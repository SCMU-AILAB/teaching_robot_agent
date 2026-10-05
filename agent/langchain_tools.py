# agent/langchain_tools.py
"""将按任务绑定的业务工具注册为 LangChain 工具."""

from langchain_core.tools import BaseTool, StructuredTool

from agent.tools import ToolAdapter


def build_langchain_tools(adapter: ToolAdapter) -> tuple[BaseTool, ...]:
    """通用任务只注册机器人与知识工具，课程工具按需开放."""
    common: tuple[BaseTool, ...] = (
        StructuredTool.from_function(coroutine=adapter.observe_scene),
        StructuredTool.from_function(func=adapter.lookup_knowledge),
        StructuredTool.from_function(coroutine=adapter.submit_action),
        StructuredTool.from_function(func=adapter.get_action_status),
        StructuredTool.from_function(coroutine=adapter.cancel_action),
    )
    if adapter.teaching_enabled:
        return (
            *common,
            StructuredTool.from_function(func=adapter.get_teaching_state),
            StructuredTool.from_function(coroutine=adapter.evaluate_answer),
        )
    return common
