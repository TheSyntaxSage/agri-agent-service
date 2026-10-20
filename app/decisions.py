from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import ceil, isfinite
from statistics import fmean
from typing import Any, Callable

from app.models import Calculation, Evidence, Intent, Risk


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _valid_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _sales(context: dict[str, Any]) -> list[float]:
    daily: dict[date, float] = defaultdict(float)
    for row in context.get("salesHistory", []):
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        try:
            sales_date = date.fromisoformat(row["date"])
        except ValueError:
            continue
        daily[sales_date] += max(0.0, _number(row.get("quantityKg", row.get("quantity"))))
    return [daily[sales_date] for sales_date in sorted(daily)][-30:]


def _forecast(context: dict[str, Any]) -> tuple[float, float, list[float]]:
    values = _sales(context)
    if not values:
        return 0.0, 0.0, []
    window = values[-min(7, len(values)) :]
    moving_average = fmean(window)
    if len(window) >= 2:
        daily_trend = (window[-1] - window[0]) / (len(window) - 1)
    else:
        daily_trend = 0.0
    next_day = max(0.0, moving_average + daily_trend)
    return next_day, daily_trend, window


def _base_evidence(context: dict[str, Any], demo: bool) -> list[Evidence]:
    product = context.get("productName", "未指定农产品")
    return [Evidence(source="java_internal_api" if not demo else "test_demo_fixture", description="分析对象", value=product, demo_data=demo)]


def _insufficient(context: dict[str, Any], demo: bool, missing: list[str]) -> dict[str, Any]:
    description = "、".join(missing)
    return {
        "recommendation": f"数据不足，不能生成经营建议：缺少{description}。请补齐后重新分析。",
        "evidence": _base_evidence(context, demo),
        "calculations": [],
        "risks": [Risk(level="high", description=f"缺少{description}，任何数值结论都可能误导", mitigation="补齐真实数据并重新发起人工分析")],
        "confidence": 0.0,
        "requires_human_confirmation": True,
    }


def _missing_sales_inputs(context: dict[str, Any], include_stock: bool = False) -> list[str]:
    missing: list[str] = []
    if not context.get("productName"):
        missing.append("明确商品")
    if not _sales(context):
        missing.append("按日汇总销量")
    else:
        dates = []
        for row in context.get("salesHistory", []):
            try:
                dates.append(date.fromisoformat(row["date"]))
            except (ValueError, TypeError, KeyError):
                continue
        dates = sorted(set(dates))[-7:]
        if any((right - left).days != 1 for left, right in zip(dates, dates[1:])):
            missing.append("连续自然日销量（缺失日期不能当作相邻日期或零销量）")
    if include_stock and not _valid_number(context.get("currentStock")):
        missing.append("当前库存")
    return missing


def sales_forecast(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    missing = _missing_sales_inputs(context)
    if context.get("forecastDays") is None:
        missing.append("预测天数")
    if missing:
        return _insufficient(context, demo, missing)
    daily, trend, window = _forecast(context)
    days = max(1, int(_number(context.get("forecastDays"), 7)))
    total = sum(max(0.0, daily + trend * index) for index in range(days))
    volatility = (max(window) - min(window)) / max(fmean(window), 1.0) if window else 1.0
    confidence = max(0.35, min(0.95, 0.92 - volatility * 0.5 - (0.15 if len(window) < 7 else 0)))
    return {
        "recommendation": f"未来{days}天预计销量约 {total:.1f} kg，建议以日均 {total / days:.1f} kg 作为人工排产参考。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="sales_history", description="移动窗口销量", value=window, demo_data=demo)],
        "calculations": [
            Calculation(metric="next_day_forecast", formula="最近7个有销量日期的移动平均 + 日期序列趋势", result=round(daily, 2), unit="kg/天"),
            Calculation(metric="period_forecast", formula="未来各日趋势外推值之和", result=round(total, 2), unit="kg"),
        ],
        "risks": [Risk(level="medium" if volatility > 0.25 else "low", description="历史销量波动可能降低短期外推准确性", mitigation="结合促销、节假日和订单数据人工复核")],
        "confidence": confidence,
        "requires_human_confirmation": True,
    }


def inventory_coverage(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    missing = _missing_sales_inputs(context, include_stock=True)
    if context.get("leadTimeDays") is None or context.get("safetyStockDays") is None:
        missing.append("提前期与安全库存天数")
    if missing:
        return _insufficient(context, demo, missing)
    daily, _, _ = _forecast(context)
    stock = max(0.0, _number(context.get("currentStock")))
    coverage = stock / daily if daily > 0 else 999.0
    lead = max(0.0, _number(context.get("leadTimeDays"), 3))
    safety = max(0.0, _number(context.get("safetyStockDays"), 2))
    reorder = daily * (lead + safety)
    gap = max(0.0, reorder - stock)
    if daily <= 0:
        recommendation = "缺少有效销量数据，无法可靠计算库存覆盖天数。"
    elif gap > 0:
        recommendation = f"库存约覆盖 {coverage:.1f} 天，低于提前期与安全期合计 {lead + safety:.1f} 天，建议人工核对至少 {ceil(gap)} kg 的补货缺口。"
    else:
        recommendation = f"库存约覆盖 {coverage:.1f} 天，当前高于补货触发线，可继续监控。"
    return {
        "recommendation": recommendation,
        "evidence": _base_evidence(context, demo) + [Evidence(source="inventory", description="当前可用库存", value=stock, demo_data=demo)],
        "calculations": [
            Calculation(metric="coverage_days", formula="当前库存 / 预测日销量", result=round(coverage, 2), unit="天"),
            Calculation(metric="reorder_gap", formula="日销量×(提前期+安全期)-库存", result=round(gap, 2), unit="kg"),
        ],
        "risks": [Risk(level="high" if gap > 0 else "low", description="供应提前期或销量突增会改变覆盖水平", mitigation="在关键品类设置动态安全库存")],
        "confidence": 0.86 if daily > 0 else 0.35,
        "requires_human_confirmation": True,
    }


def stock_issue(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    missing = _missing_sales_inputs(context, include_stock=True)
    if context.get("leadTimeDays") is None or context.get("shelfLifeDays") is None:
        missing.append("提前期与保鲜期")
    if missing:
        return _insufficient(context, demo, missing)
    daily, _, _ = _forecast(context)
    stock = max(0.0, _number(context.get("currentStock")))
    coverage = stock / daily if daily > 0 else 999.0
    shelf_life = max(1.0, _number(context.get("shelfLifeDays"), 7))
    shortage = coverage < max(1.0, _number(context.get("leadTimeDays"), 3))
    slow = coverage > shelf_life * 0.8
    if shortage:
        label, rec, level = "缺货风险", "立即核查在途与可调拨库存，并触发加急补货。", "high"
    elif slow:
        label, rec, level = "滞销风险", "减少采购/采摘批次，并考虑分级促销或跨渠道调拨。", "medium"
    else:
        label, rec, level = "库存健康", "暂不调整，按当前节奏滚动监控。", "low"
    return {
        "recommendation": f"判定为{label}：{rec}",
        "evidence": _base_evidence(context, demo),
        "calculations": [Calculation(metric="coverage_days", formula="库存 / 预测日销量", result=round(coverage, 2), unit="天")],
        "risks": [Risk(level=level, description=f"当前{label}", mitigation=rec)],
        "confidence": 0.83,
        "requires_human_confirmation": True,
    }


def purchase_advice(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    missing = _missing_sales_inputs(context, include_stock=True)
    if context.get("leadTimeDays") is None or context.get("safetyStockDays") is None:
        missing.append("提前期与安全库存天数")
    if missing:
        return _insufficient(context, demo, missing)
    daily, _, _ = _forecast(context)
    stock = max(0.0, _number(context.get("currentStock")))
    lead = max(0.0, _number(context.get("leadTimeDays"), 3))
    safety = max(0.0, _number(context.get("safetyStockDays"), 2))
    target = daily * (lead + safety)
    quantity = max(0, ceil(target - stock))
    price_value = context.get("purchaseUnitPrice", context.get("unitCost"))
    unit_price = max(0.0, _number(price_value)) if price_value is not None else None
    amount = quantity * unit_price if unit_price is not None else None
    amount_text = f"，按输入单价估算采购额 {amount:.2f} 元" if amount is not None else "；未提供采购单价，不估算金额"
    return {
        "recommendation": f"建议人工核对 {quantity} kg 的采购缺口{amount_text}。确认前须核对当前货盘订单和供应商交期。" if quantity else "当前计算未显示新增采购缺口，仍须人工核对当前货盘订单。",
        "evidence": _base_evidence(context, demo),
        "calculations": [
            Calculation(metric="purchase_quantity", formula="max(0, 日销量×(提前期+安全期)-库存)", result=quantity, unit="kg"),
            *([Calculation(metric="purchase_amount", formula="采购数量×调用方提供的采购单价", result=round(amount, 2), unit="元")] if amount is not None else []),
        ],
        "risks": [Risk(level="medium", description="重复采购、预测偏差或供应商延期可能造成损失", mitigation="必须与在途采购单及预算联查")],
        "confidence": 0.84,
        "requires_human_confirmation": True,
        "purchase_amount": amount or 0.0,
    }


def harvest_advice(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    missing = _missing_sales_inputs(context, include_stock=True)
    if context.get("readyCropQuantity") is None:
        missing.append("人工确认的可采收量")
    if context.get("forecastDays") is None or context.get("wasteRate") is None:
        missing.append("预测天数与损耗率")
    if missing:
        return _insufficient(context, demo, missing)
    daily, _, _ = _forecast(context)
    stock = max(0.0, _number(context.get("currentStock")))
    ready = max(0.0, _number(context.get("readyCropQuantity")))
    days = max(1, int(_number(context.get("forecastDays"), 7)))
    waste = min(0.9, max(0.0, _number(context.get("wasteRate"), 0.05)))
    need = max(0.0, daily * days - stock)
    gross_harvest = min(ready, need / max(0.01, 1 - waste))
    return {
        "recommendation": f"建议未来{days}天人工安排分批采摘约 {ceil(gross_harvest)} kg，优先按订单锁定量复核，避免一次性采摘。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="production", description="当前可采收量", value=ready, demo_data=demo)],
        "calculations": [Calculation(metric="harvest_quantity", formula="min(可采量, (预测需求-库存)/(1-损耗率))", result=round(gross_harvest, 2), unit="kg")],
        "risks": [Risk(level="medium", description="成熟度、天气和采后损耗可能改变实际可售量", mitigation="按批次抽检成熟度和损耗率")],
        "confidence": 0.81,
        "requires_human_confirmation": True,
    }


def supplier_match(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    suppliers = context.get("suppliers", [])
    scored: list[tuple[float, dict[str, Any]]] = []
    prices = [_number(item.get("unitPrice")) for item in suppliers if isinstance(item, dict)]
    max_price = max(prices, default=1.0) or 1.0
    for supplier in suppliers:
        if not isinstance(supplier, dict):
            continue
        price_score = 1 - _number(supplier.get("unitPrice")) / max_price
        quality = _number(supplier.get("qualityScore")) / 100
        reliability = _number(supplier.get("reliability"), 0.5)
        lead_score = 1 / (1 + max(0.0, _number(supplier.get("leadTimeDays"))))
        score = 0.25 * price_score + 0.3 * quality + 0.3 * reliability + 0.15 * lead_score
        scored.append((score, supplier))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("name", ""))))
    if not scored:
        return {
            "recommendation": "暂无供应商数据，不能生成匹配建议。",
            "evidence": _base_evidence(context, demo),
            "calculations": [],
            "risks": [Risk(level="high", description="供应商候选为空", mitigation="补充合格供应商及履约数据")],
            "confidence": 0.2,
            "requires_human_confirmation": True,
        }
    score, best = scored[0]
    return {
        "recommendation": f"综合评分首选 {best.get('name', '未命名供应商')}（{score * 100:.1f}分），签约前应完成资质、样品和合同复核。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="supplier_master", description="候选供应商数量", value=len(scored), demo_data=demo)],
        "calculations": [Calculation(metric="supplier_score", formula="25%价格+30%质量+30%可靠性+15%交期", result=round(score * 100, 2), unit="分")],
        "risks": [Risk(level="medium", description="历史评分不能替代当前批次质量与合规审查", mitigation="保留备选供应商并小批量试单")],
        "confidence": min(0.9, 0.6 + len(scored) * 0.05),
        "requires_human_confirmation": True,
    }


def weather_impact(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    weather = context.get("weather", {}) if isinstance(context.get("weather", {}), dict) else {}
    if not weather or any(not _valid_number(weather.get(key)) for key in ("maxTemp", "minTemp", "rainfallMm", "windKph")):
        return _insufficient(context, demo, ["真实天气观测或预报"])
    max_temp = _number(weather.get("maxTemp"))
    min_temp = _number(weather.get("minTemp"))
    rain = _number(weather.get("rainfallMm"))
    wind = _number(weather.get("windKph"))
    hazards: list[str] = []
    score = 0
    if max_temp >= 35:
        hazards.append("高温")
        score += 2
    if min_temp <= 5:
        hazards.append("低温")
        score += 2
    if rain >= 50:
        hazards.append("强降雨/积水")
        score += 2
    if wind >= 40:
        hazards.append("大风")
        score += 2
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    recommendation = "、".join(hazards) if hazards else "无显著极端天气信号"
    return {
        "recommendation": f"天气影响等级为 {level}：{recommendation}。建议调整灌排、遮阴、防风和作业窗口。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="weather", description="天气输入", value=weather, demo_data=demo)],
        "calculations": [Calculation(metric="weather_risk_score", formula="高温/低温/强降雨/大风阈值计分", result=score, unit="分")],
        "risks": [Risk(level=level, description=recommendation, mitigation="以本地预警和田间实测为准，必要时暂停户外作业")],
        "confidence": 0.78,
        "requires_human_confirmation": True,
    }


def planting_advice(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    candidates = context.get("cropCandidates", [])
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        revenue = _number(item.get("expectedYield")) * _number(item.get("unitPrice"))
        cost = _number(item.get("expectedYield")) * _number(item.get("unitCost"))
        risk = min(1.0, max(0.0, _number(item.get("weatherRisk"))))
        adjusted_profit = (revenue - cost) * (1 - risk)
        ranked.append((adjusted_profit, item))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("name", ""))))
    if not ranked:
        return {
            "recommendation": "候选作物数据不足，暂不建议做种植结构变更。",
            "evidence": _base_evidence(context, demo),
            "calculations": [],
            "risks": [Risk(level="high", description="缺少产量、成本、价格和气象风险数据")],
            "confidence": 0.2,
            "requires_human_confirmation": True,
        }
    profit, crop = ranked[0]
    return {
        "recommendation": f"在给定候选中优先评估种植 {crop.get('name', '未命名作物')}，风险调整后预计利润 {profit:.2f} 元；先做小面积验证。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="crop_candidates", description="候选作物数量", value=len(ranked), demo_data=demo)],
        "calculations": [Calculation(metric="risk_adjusted_profit", formula="产量×(售价-单位成本)×(1-天气风险)", result=round(profit, 2), unit="元")],
        "risks": [Risk(level="medium", description="价格、病虫害、土壤和轮作约束未必完整", mitigation="由农艺师结合地块检测和轮作计划确认")],
        "confidence": 0.72,
        "requires_human_confirmation": True,
    }


def price_profit_sensitivity(context: dict[str, Any], demo: bool) -> dict[str, Any]:
    required = [key for key in ("basePrice", "unitCost", "baseDemand", "priceElasticity") if not _valid_number(context.get(key))]
    if required or _number(context.get("basePrice")) <= 0 or _number(context.get("unitCost")) < 0 or _number(context.get("baseDemand")) < 0:
        return _insufficient(context, demo, ["价格、成本、需求与弹性完整输入"])
    price = max(0.01, _number(context.get("basePrice")))
    cost = max(0.0, _number(context.get("unitCost")))
    demand = max(0.0, _number(context.get("baseDemand"), 0))
    elasticity = _number(context.get("priceElasticity"), -1)
    rows = []
    for change in (-0.1, 0.0, 0.1):
        scenario_price = price * (1 + change)
        scenario_demand = max(0.0, demand * (1 + elasticity * change))
        profit = (scenario_price - cost) * scenario_demand
        rows.append({"priceChange": change, "price": round(scenario_price, 2), "demand": round(scenario_demand, 2), "profit": round(profit, 2)})
    best = max(rows, key=lambda row: row["profit"])
    return {
        "recommendation": f"三档敏感性测算中，价格调整 {best['priceChange']:+.0%} 的估算利润最高（{best['profit']:.2f} 元）；实际调价前应做小范围验证。",
        "evidence": _base_evidence(context, demo) + [Evidence(source="pricing_inputs", description="基础价格、成本、需求及弹性", value={"price": price, "cost": cost, "demand": demand, "elasticity": elasticity}, demo_data=demo)],
        "calculations": [Calculation(metric="price_profit_scenarios", formula="(调后价格-成本)×基础需求×(1+弹性×调价幅度)", result=rows, unit="元")],
        "risks": [Risk(level="medium", description="线性价格弹性只适用于小幅、短期变化", mitigation="避免直接全量调价，跟踪转化率和竞争价格")],
        "confidence": 0.75,
        "requires_human_confirmation": True,
    }


ANALYZERS: dict[Intent, Callable[[dict[str, Any], bool], dict[str, Any]]] = {
    Intent.SALES_FORECAST: sales_forecast,
    Intent.INVENTORY_COVERAGE: inventory_coverage,
    Intent.STOCK_ISSUE: stock_issue,
    Intent.PURCHASE_ADVICE: purchase_advice,
    Intent.HARVEST_ADVICE: harvest_advice,
    Intent.SUPPLIER_MATCH: supplier_match,
    Intent.WEATHER_IMPACT: weather_impact,
    Intent.PLANTING_ADVICE: planting_advice,
    Intent.PRICE_PROFIT_SENSITIVITY: price_profit_sensitivity,
}
