"""异动归因 Agent 的控制流。

当前实现显式月份比较、指标上下文加载、总体查询、变化核实与 Skill 策略制定。
Graph 负责控制流；analyze_change 在单节点内部进行有限决策与维度工具执行。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.anomaly_agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.nodes.parse_request import parse_request
from app.agent.anomaly_agent.nodes.load_metric_context import load_metric_context
from app.agent.anomaly_agent.nodes.query_overall import query_overall
from app.agent.anomaly_agent.nodes.verify_change import verify_change
from app.agent.anomaly_agent.nodes.plan_analysis import plan_analysis, route_after_verification
from app.agent.anomaly_agent.nodes.analyze_change import analyze_change
from app.agent.anomaly_agent.state import AnomalyState


# StateGraph 依据 AnomalyState 合并各节点返回的部分状态。
builder = StateGraph(AnomalyState, context_schema=DataAgentContext)
builder.add_node("receive_question", receive_question)
builder.add_node("parse_request", parse_request)
builder.add_node("load_metric_context", load_metric_context)
builder.add_node("query_overall", query_overall)
builder.add_node("verify_change", verify_change)
builder.add_node("plan_analysis", plan_analysis)
builder.add_node("analyze_change", analyze_change)
builder.add_edge(START, "receive_question")
builder.add_edge("receive_question", "parse_request")
builder.add_edge("parse_request", "load_metric_context")
builder.add_edge("load_metric_context", "query_overall")
builder.add_edge("query_overall", "verify_change")
builder.add_conditional_edges("verify_change", route_after_verification,
                              {"plan_analysis": "plan_analysis", "end": END})
builder.add_edge("plan_analysis", "analyze_change")
builder.add_edge("analyze_change", END)

# 编译后的 Graph 由 Orchestrator 的适配节点调用。
anomaly_graph = builder.compile()
