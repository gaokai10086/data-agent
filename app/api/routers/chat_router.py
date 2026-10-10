import json

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse
from app.api.deps import get_chat_service
from app.schemas.chat import QuerySchema
from app.core.logging import logger

"""
不负责问大模型，也不负责查 MySQL，只负责把前端提的问题交给后面的师傅（chat_service）再把师傅一口一口吐出来的结果像流水一样还给前端
"""
# APIRouter(prefix="/api")：定门牌号。只要用户访问网址后面带 /api/... 的，都归这个文件管
chat_router = APIRouter(prefix="/api")


def _progress_message(stage: str) -> str:
    """把内部节点名称归并为少量用户可理解的业务阶段。"""
    if any(keyword in stage for keyword in ("SQL", "执行")):
        return "正在执行数据查询..."
    if any(keyword in stage for keyword in ("结果", "图表", "洞察", "建议", "汇总", "报告")):
        return "正在生成分析结果..."
    if any(keyword in stage for keyword in ("关键词", "召回", "关联", "候选表", "上下文")):
        return "正在准备查询数据..."
    return "正在理解问题..."

#  @chat_router.post("/query")：全路径就是 /api/query。当前端（比如网页上的发送按钮）用 POST 方式发请求时，就会触发这个函数。
# query: QuerySchema：前端发过来的数据必须符合 QuerySchema 定的规矩。如果前端乱发一通，代码在这里直接报错拒绝，根本不会往下走。
# chat_service=Depends(get_chat_service)：可以理解为“帮我把真正会干活的师傅叫过来，名字叫 chat_service
@chat_router.post("/query")
async def date_query(query: QuerySchema, chat_service=Depends(get_chat_service)):
    logger.info(
        "[Chat API] received provider={} model={} query={}",
        query.provider,
        query.model_tier,
        query.query[:80],
    )
    async def event_stream():
        last_progress = None
        try:
            #  chat_service.stream_chat(query.query)：拿着用户的提问去问大模型、去查数据库
            async for chunk in chat_service.stream_chat(
                query.query,
                provider=query.provider,
                model_tier=query.model_tier,
            ):
                # 细粒度节点只保留在服务端，前端仅接收聚合后的业务进度。
                if isinstance(chunk, dict) and (
                    "stage" in chunk or chunk.get("type") == "thought"
                ):
                    stage = str(chunk.get("stage") or chunk.get("content") or "")
                    progress = _progress_message(stage)
                    if progress != last_progress:
                        last_progress = progress
                        yield f"data: {json.dumps({'type': 'progress', 'content': progress}, ensure_ascii=False)}\n\n"
                    continue
                #  读取大模型每吐出一个词或者一句话（叫 chunk）
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:
            #  万一中间断网了或者报错，把错误原因包装成一句话推给前端看。
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False, default=str)}\n\n"

    # 大模型流式输出，产生数据立刻发送到前端
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )
