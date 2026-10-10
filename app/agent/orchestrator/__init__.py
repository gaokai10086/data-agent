"""应用级 Agent 编排层：负责意图识别和子 Agent 分流。

Graph 不在包初始化阶段自动导入，避免其他 Agent 只引用
``app.agent.orchestrator.state`` 时触发整张 Graph，造成循环依赖。
需要 Graph 时请显式从 ``app.agent.orchestrator.graph`` 导入。
"""
