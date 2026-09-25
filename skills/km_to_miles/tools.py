import json

KM_PER_MILE = 1.609344


def km_to_miles(km: float = 0.0, **_):
    try:
        km = float(km)
    except (TypeError, ValueError):
        return {"result": json.dumps({"error": "invalid km value"}),
                "say": "I couldn't understand that distance, sir."}
    miles = km / KM_PER_MILE
    return {
        "result": json.dumps({"km": km, "miles": round(miles, 2)}),
        "say": f"{km} kilometres is {round(miles, 2)} miles, sir."
    }


def miles_to_km(miles: float = 0.0, **_):
    try:
        miles = float(miles)
    except (TypeError, ValueError):
        return {"result": json.dumps({"error": "invalid miles value"}),
                "say": "I couldn't understand that distance, sir."}
    km = miles * KM_PER_MILE
    return {
        "result": json.dumps({"miles": miles, "km": round(km, 2)}),
        "say": f"{miles} miles is {round(km, 2)} kilometres, sir."
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "km_to_miles",
            "description": "Convert a distance from kilometres to miles. Use when the user gives a km value and wants miles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "km": {"type": "number", "description": "Distance in kilometres to convert."}
                },
                "required": ["km"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "miles_to_km",
            "description": "Convert a distance from miles to kilometres. Use when the user gives a miles value and wants km.",
            "parameters": {
                "type": "object",
                "properties": {
                    "miles": {"type": "number", "description": "Distance in miles to convert."}
                },
                "required": ["miles"]
            }
        }
    }
]

FUNCTIONS = {"km_to_miles": km_to_miles, "miles_to_km": miles_to_km}

ACKS = {}

ACTIONS = []

GUARDS = {}

CONFIRM = {}
