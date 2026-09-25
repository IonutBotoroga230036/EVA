import json
import tools


def test_km_to_miles_normal():
    res = tools.km_to_miles(km=10)
    data = json.loads(res["result"])
    assert data["km"] == 10
    assert abs(data["miles"] - 6.21) < 0.01
    assert "sir" in res["say"]


def test_miles_to_km_normal():
    res = tools.miles_to_km(miles=10)
    data = json.loads(res["result"])
    assert data["miles"] == 10
    assert abs(data["km"] - 16.09) < 0.01
    assert "sir" in res["say"]


def test_km_to_miles_invalid():
    res = tools.km_to_miles(km="not_a_number")
    data = json.loads(res["result"])
    assert "error" in data
    assert "sir" in res["say"]


def test_miles_to_km_invalid():
    res = tools.miles_to_km(miles="bad")
    data = json.loads(res["result"])
    assert "error" in data
    assert "sir" in res["say"]


def test_zero_km():
    res = tools.km_to_miles(km=0)
    data = json.loads(res["result"])
    assert data["miles"] == 0.0
