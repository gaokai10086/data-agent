"""异动归因 Agent 的控制流。

当前只实现两步：接收原始问题、解析显式月份比较。Graph 负责节点先后顺序；
具体的文本解析放在 nodes/，避免把执行逻辑堆在 graph.py。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.data_agent.context import DataAgentContext
from app.agent.anomaly_agent.nodes.receive_question import receive_question
from app.agent.anomaly_agent.nodes.parse_request import parse_request
from app.agent.anomaly_agent.nodes.load_metric_context import load_metric_context
from app.agent.anomaly_agent.nodes.query_overall import query_overall
from app.agent.anomaly_agent.state import AnomalyState


# StateGraph 依据 AnomalyState 合并各节点返回的部分状态。
builder = StateGraph(AnomalyState, context_schema=DataAgentContext)
builder.add_node("receive_question", receive_question)
builder.add_node("parse_request", parse_request)
builder.add_node("load_metric_context", load_metric_context)
builder.add_node("query_overall", query_overall)
# 解析成功后读取共享 Metadata；Metadata 缺失时让错误尽早暴露。
builder.add_edge(START, "receive_question")
builder.add_edge("receive_question", "parse_request")
builder.add_edge("parse_request", "load_metric_context")
builder.add_edge("load_metric_context", "query_overall")
builder.add_edge("query_overall", END)

# 编译后的 Graph 由 Orchestrator 的适配节点调用。
anomaly_graph = builder.compile()
