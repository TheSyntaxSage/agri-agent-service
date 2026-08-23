from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def build_demo_context(parameters: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fixture data. It is guarded by test-only configuration in the workflow."""
    today = date.today()
    quantities = [82, 88, 90, 93, 96, 101, 104, 108, 111, 115, 118, 121, 124, 128]
    context: dict[str, Any] = {
        "productName": "测试番茄",
        "salesHistory": [
            {
                "date": (today - timedelta(days=len(quantities) - index)).isoformat(),
                "quantity": quantity,
                "price": 6.0,
                "cost": 3.6,
            }
            for index, quantity in enumerate(quantities)
        ],
        "currentStock": 350,
        "leadTimeDays": 3,
        "safetyStockDays": 2,
        "forecastDays": 7,
        "readyCropQuantity": 900,
        "wasteRate": 0.06,
        "suppliers": [
            {"name": "示例供应商A", "unitPrice": 4.1, "qualityScore": 92, "leadTimeDays": 2, "reliability": 0.96},
            {"name": "示例供应商B", "unitPrice": 3.8, "qualityScore": 84, "leadTimeDays": 4, "reliability": 0.88},
        ],
        "weather": {"minTemp": 12, "maxTemp": 34, "rainfallMm": 65, "windKph": 18},
        "cropCandidates": [
            {"name": "番茄", "expectedYield": 4200, "unitPrice": 6.0, "unitCost": 3.2, "weatherRisk": 0.18},
            {"name": "黄瓜", "expectedYield": 5000, "unitPrice": 4.5, "unitCost": 2.8, "weatherRisk": 0.12},
        ],
        "basePrice": 6.0,
        "unitCost": 3.6,
        "baseDemand": 110,
        "priceElasticity": -1.2,
    }
    context.update(parameters)
    return context

