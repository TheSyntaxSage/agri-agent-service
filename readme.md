# Agri Agent Service MVP

可独立测试的 FastAPI 农业决策中心。计算与建议全部为确定性规则，不依赖大模型；意图识别可选接入大模型（未配置或调用失败时自动回退中英文关键词规则）。工作流使用官方 LangGraph `StateGraph` 编排显式状态节点与边。

## 能力

- 请求头身份/租户/角色/权限上下文
- 意图识别：调用方显式指定，或经大模型分类，异常时回退中英文关键词匹配
- HTTPX 调用 Java 内部 API，传递 `X-Service-Token`、`X-Trace-Id`、租户、用户及防循环 hop/marker
- 防循环 marker 和最大 agent hop 限制
- 官方 LangGraph Graph API：`START → guard → recognize → authorize → fetch_context → analyze → finalize → END`
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

## 大模型意图识别（可选）

仅在 `intent=auto` 且 `query` 非空时调用，用于理解没有命中关键词的自然语言问法。计算、建议、风险判断仍由 `app/decisions.py` 的确定性规则完成，大模型不参与业务逻辑。

配置（`AGRI_LLM_BASE_URL` 与 `AGRI_LLM_API_KEY` 同时非空才启用，否则与不接入时行为一致）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGRI_LLM_BASE_URL` | 空 | OpenAI 兼容网关地址，请求发往 `{base_url}/responses` |
| `AGRI_LLM_API_KEY` | 空 | Bearer 令牌，只从环境变量读取 |
| `AGRI_LLM_MODEL` | `deepseek-flash` | 模型名 |
| `AGRI_LLM_TIMEOUT_SECONDS` | `20` | 单次调用超时 |
| `AGRI_LLM_REASONING_EFFORT` | `high` | 作为 `reasoning.effort` 发送，留空则不发送该字段 |

调用约定：

- 使用 Responses API，请求体含 `model`、`instructions`、`input`、`store=false`，模型只需输出意图标识字符串
- 返回文本经宽松解析（容忍引号、反引号和结尾句号），匹配不到唯一意图即视为不可用
- 网关超时、非 2xx、响应结构异常、意图标识无法识别时，都会记录一条 warning 并回退关键词规则
- 模型判断为 `auto`（无法确定）时同样回退关键词规则

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
  "parameters": {"productName": "番茄", "forecastDays": 7},
  "traceId": "optional-upstream-trace-id"
}
```

Java 侧约定提供：

```text
POST /internal/agent/decision-context
```

请求体为 `intent` 和经过意图白名单校验的 `parameters`。商品类分析必须显式传 `productName`；不会从问题文本猜商品，也不接受当前无法解析的 `productId`。返回 JSON 对象，按分析类型提供升序日汇总 kg 销量、当前库存等字段。服务 token 只从环境变量读取，Python 入站也会先以常量时间校验该 token，再信任租户和权限头。

反馈接口：

- `POST /api/v1/feedback`
- `GET /api/v1/feedback?trace_id=...`
- `GET /api/v1/error-analysis`

Swagger UI：`/docs`。
