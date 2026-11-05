"""Orchestrator 的各执行节点。

每个节点只做一件小事：
- rewrite_question：改写问题（多轮补全）
- intent_detection：识别意图
- run_data_agent：调用智能问数子 Agent 并转发事件
- run_anomaly_agent：调用异动分析子 Agent 并转发事件
- run_direct_answer：执行直接回答并流式输出
"""
