import json

import tools


def fake(url, params):
    rates = {"RON": 4.977, "USD": 1.0412}
    want = params.get("symbols", "").split(",") if params.get("symbols") else list(rates)
    return {"amount": 1.0, "base": params["base"], "date": "2026-11-24", "rates": {k: rates[k] for k in want if k in rates}}


def test_convert():
    tools.FETCH = fake
    out = tools.convert_currency(amount=100, from_currency="euro", to_currency="lei")
    assert json.loads(out["result"])["converted"] == 497.7


def test_bad_amount_is_an_error():
    out = tools.convert_currency(amount="not a number", from_currency="EUR", to_currency="RON")
    assert "error" in json.loads(out["result"])
