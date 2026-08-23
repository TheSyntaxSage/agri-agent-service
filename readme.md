# Agri Agent Service MVP

可独立测试的 FastAPI 农业决策中心。运行时不调用 LLM；工作流采用 LangGraph 风格的显式状态节点与边，所有意图识别、计算和建议均为确定性规则。

## 能力

- 请求头身份/租户/角色/权限上下文
- 中英文关键词意图识别，或调用方显式指定意图
- HTTPX 调用 Java 内部 API，传递 `X-Service-Token`、`X-Trace-Id`、租户、用户及防循环 hop/marker
- 防循环 marker 和最大 agent hop 限制
- 销量预测、库存覆盖、缺货/滞销、采购、采摘、供应商匹配、天气影响、种植、价格利润敏感性
- 统一结构化输出：`recommendation/evidence/calculations/risks/confidence/requiresHumanConfirmation/traceId`
- SQLite + SQLAlchemy 决策反馈、MAE/MAPE/采纳率分析
- Java 不可达时的测试 demo 降级：仅当服务环境为 `test`、配置开关开启、且请求显式 `allowDemoFallback=true` 三者同时满足

## 本地运行

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8090
```

测试：

```powershell
.venv\Scripts\python -m pytest
```

## API

决策请求头示例：

```text
X-Tenant-Id: tenant-1
X-User-Id: user-1
X-Roles: manager
X-Permissions: agent:decide,agent:purchase,agent:supplier
```

请求：

```json
POST /api/v1/decisions
{
  "query": "预测番茄未来七天销量",
  "intent": "auto",
  "parameters": {"productId": 101},
  "traceId": "optional-upstream-trace-id"
}
```

Java 侧约定提供：

```text
POST /internal/agent/decision-context
```

请求体为 `intent` 和 `parameters`。返回 JSON 对象，按分析类型提供 `salesHistory`、`currentStock`、`suppliers`、`weather` 等字段。服务 token 只从环境变量读取。

反馈接口：

- `POST /api/v1/feedback`
- `GET /api/v1/feedback?trace_id=...`
- `GET /api/v1/error-analysis`

Swagger UI：`/docs`。
