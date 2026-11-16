
import asyncio

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition


@tool
def get_order_status(order_id: str) -> str:
    """查询订单物流状态。"""
    print("执行订单查询工具")
    return f"订单{order_id}已经发货"


async def llm_node(state: MessagesState):

    last_message = state["messages"][-1]

    # 如果刚拿到工具结果，就生成最终回答
    if isinstance(last_message, ToolMessage):
        return {
            "messages": [
                AIMessage(
                    content=f"查询结果：{last_message.content}"
                )
            ]
        }

    # 否则模拟 LLM 请求调用工具
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_order_status",
                        "args": {"order_id": "1001"},
                        "id": "call_001",
                        "type": "tool_call"
                    }
                ]
            )
        ]
    }


builder = StateGraph(MessagesState)

builder.add_node("llm", llm_node)
builder.add_node("tools", ToolNode([get_order_status]))

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


async def main():

    result = await graph.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": "订单1001到哪里了？"
            }
        ]
    })

    for msg in result["messages"]:
        print(type(msg).__name__, ":", msg.content)


if __name__ == "__main__":
    asyncio.run(main())
