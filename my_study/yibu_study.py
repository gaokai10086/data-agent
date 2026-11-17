
import asyncio

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from langgraph.graph import (
    StateGraph,
    MessagesState,
    START,
    END
)
from langgraph.prebuilt import ToolNode, tools_condition


MAX_TOOL_CALLS = 3


# 1. 定义工具
@tool
def get_order_status(order_id: str) -> str:
    """查询订单的物流状态。"""
    print(f"执行工具：查询订单 {order_id}")
    return f"订单 {order_id} 已发货"


# 2. 模拟 LLM 节点
def llm_node(state: MessagesState):

    # 统计已经执行完成的工具调用次数
    count = sum(
        isinstance(msg, ToolMessage)
        for msg in state["messages"]
    )

    print(f"当前已完成工具调用：{count} 次")

    # 达到最大次数，生成最终回答
    if count >= MAX_TOOL_CALLS:
        return {
            "messages": [
                AIMessage(
                    content="已达到最大工具调用次数，停止查询。"
                )
            ]
        }

    # 故意模拟 LLM 不断请求相同工具
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_order_status",
                        "args": {"order_id": "1001"},
                        "id": f"call_{count + 1}",
                        "type": "tool_call"
                    }
                ]
            )
        ]
    }


# 3. 构建 Graph
builder = StateGraph(MessagesState)

builder.add_node("llm", llm_node)

builder.add_node(
    "tools",
    ToolNode([get_order_status])
)

builder.add_edge(START, "llm")

builder.add_conditional_edges(
    "llm",
    tools_condition,
    {
        "tools": "tools",
        "__end__": END
    }
)

builder.add_edge("tools", "llm")

graph = builder.compile()


# 4. 程序入口
async def main():

    result = await graph.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": "查询订单1001的物流状态"
            }
        ]
    })

    print("\n最终回答：")
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
