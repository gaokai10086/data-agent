"""向前端发送可读的节点结果摘要，而不是完整 State。"""


def emit_node_result(runtime, message: str) -> None:
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"type": "node_result", "content": message})
