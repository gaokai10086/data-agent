from fastapi import FastAPI
from app.api.routers.chat_router import chat_router
from app.api.routers.meta_governance_router import router as meta_governance_router
from app.api.routers.metric_management_router import router as metric_management_router
from app.core.lifespan import lifespan
from app.core.middleware import RequestIDMiddleware
from app.api.routers.example_router import example_router

# 配置在 app.config.app_config 导入时从环境变量（本地 .env）加载。
# 应用实例化与生命周期绑定
app = FastAPI(
    title="Data-Agent",
    description="企业级数仓智能问数",
    version="1.0.0",
    lifespan=lifespan
)

# 3. 路由注册
# 将问答路由树挂载至主应用（统一增加 /api 前缀）
app.include_router(chat_router)
# 将金标资产管理路由挂载至主应用（统一增加 /api 前缀）
app.include_router(example_router)
# 将字段语义治理审批路由挂载至主应用
app.include_router(meta_governance_router)
# 将指标管理路由挂载至主应用
app.include_router(metric_management_router)


# 4. 中间件挂载
#。当一个 HTTP 问数请求到达时，中间件会生成一个唯一 UUID（例如 x-request-id）并挂载到请求头和日志上下文中。
# 无论后序在 Agent、大模型推理还是 MySQL 执行中哪一步报错，都可以根据这个 Request ID 秒级定位单次调用的完整日志链路。
app.add_middleware(RequestIDMiddleware)
