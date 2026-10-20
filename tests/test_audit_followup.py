from app.decisions import sales_forecast, price_profit_sensitivity


def test_sparse_dates_do_not_become_adjacent_days():
    result = sales_forecast({'productName': 'tomato', 'forecastDays': 3, 'salesHistory': [
        {'date': '2026-09-01', 'quantityKg': 10}, {'date': '2026-09-08', 'quantityKg': 20}
    ]}, False)
    assert result['confidence'] == 0
    assert result['calculations'] == []


def test_invalid_price_does_not_become_zero_cost_advice():
    result = price_profit_sensitivity({'productName': 'tomato', 'basePrice': 6,
        'unitCost': 'missing', 'baseDemand': 10, 'priceElasticity': -1}, False)
    assert result['confidence'] == 0
    assert result['calculations'] == []


def test_invalid_numeric_parameter_rejected(client, auth_headers):
    response = client.post('/api/v1/decisions', headers=auth_headers, json={
        'intent': 'sales_forecast', 'parameters': {'productName': 'tomato', 'forecastDays': 'unknown'}})
    assert response.status_code == 422
