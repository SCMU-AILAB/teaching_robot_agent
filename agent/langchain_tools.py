# agent/langchain_tools.py
"""将按任务绑定的业务工具注册为 LangChain 工具."""

from langchain_core.tools import BaseTool, StructuredTool

from agent.tools import ToolAdapter


def build_langchain_tools(adapter: ToolAdapter) -> tuple[BaseTool, ...]:
    """注册七个模型工具；等待动作由宿主负责，不暴露轮询工具."""
    return (
        StructuredTool.from_function(coroutine=adapter.observe_scene),
        StructuredTool.from_function(func=adapter.lookup_knowledge),
        StructuredTool.from_function(func=adapter.get_teaching_state),
        StructuredTool.from_function(coroutine=adapter.evaluate_answer),
        StructuredTool.from_function(coroutine=adapter.submit_action),
        StructuredTool.from_function(func=adapter.get_action_status),
        StructuredTool.from_function(coroutine=adapter.cancel_action),
    )
