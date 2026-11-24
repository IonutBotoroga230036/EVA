"""Currency conversion with the European Central Bank's reference rates, via frankfurter.dev (free, no key).

GET https://api.frankfurter.dev/v1/latest?base=EUR&symbols=RON
-> {"amount": 1.0, "base": "EUR", "date": "2026-11-24", "rates": {"RON": 4.977}}
"""

import json
import re
from datetime import date
from typing import Callable, Optional

URL = "https://api.frankfurter.dev/v1/latest"
FETCH: Optional[Callable[[str, dict], dict]] = None          # tests answer here instead of the service

NAMES = {"eur": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR",
         "ron": "RON", "lei": "RON", "leu": "RON", "lej": "RON", "romanian leu": "RON", "romanian lei": "RON",
         "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD", "us dollar": "USD", "us dollars": "USD",
         "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "£": "GBP", "sterling": "GBP", "british pound": "GBP",
         "chf": "CHF", "swiss franc": "CHF", "swiss francs": "CHF", "franc": "CHF", "francs": "CHF",
         "jpy": "JPY", "yen": "JPY", "¥": "JPY", "huf": "HUF", "forint": "HUF", "forints": "HUF",
         "pln": "PLN", "zloty": "PLN", "zlotys": "PLN", "czk": "CZK", "koruna": "CZK", "crowns": "CZK",
         "sek": "SEK", "swedish krona": "SEK", "nok": "NOK", "norwegian krone": "NOK", "dkk": "DKK",
         "danish krone": "DKK", "bgn": "BGN", "lev": "BGN", "leva": "BGN", "try": "TRY", "lira": "TRY",
         "turkish lira": "TRY", "cad": "CAD", "canadian dollar": "CAD", "canadian dollars": "CAD",
         "aud": "AUD", "australian dollar": "AUD", "australian dollars": "AUD", "cny": "CNY", "yuan": "CNY",
         "inr": "INR", "rupee": "INR", "rupees": "INR"}
SPOKEN = {"EUR": ("euro", "euros"), "RON": ("leu", "lei"), "USD": ("dollar", "dollars"), "GBP": ("pound", "pounds"),
          "CHF": ("Swiss franc", "Swiss francs"), "JPY": ("yen", "yen"), "HUF": ("forint", "forints"),
          "PLN": ("zloty", "zlotys")}


class RateError(RuntimeError):
    pass


def code(name: str) -> str:
    t = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return NAMES.get(t) or (t.upper() if re.fullmatch(r"[a-z]{3}", t) else "")


def _get(url: str, params: dict) -> dict:
    if FETCH is not None:
        return FETCH(url, params)
    import httpx
    try:
        r = httpx.get(url, params=params, timeout=8.0, follow_redirects=True)
    except httpx.HTTPError as e:
        raise RateError(f"no connection to the rate service ({e.__class__.__name__})")
    if r.status_code == 404:
        raise RateError("the rate service doesn't know that currency")
    if r.status_code != 200:
        raise RateError(f"the rate service answered {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise RateError("the rate service sent something that isn't a rate")


def _rates(base: str, symbols: str = "") -> dict:
    params = {"base": base}
    if symbols:
        params["symbols"] = symbols
    data = _get(URL, params)
    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        raise RateError("the rate service sent no rates")
    return data


def _when(d: str) -> str:
    try:
        x = date.fromisoformat(d)
        return f"{x.day} {x.strftime('%B')}"
    except (TypeError, ValueError):
        return "today"


def _words(n: float, c: str) -> str:
    one, many = SPOKEN.get(c, (c, c))
    shown = f"{n:,.2f}" if n != int(n) or c == "RON" or n >= 1000 else f"{int(n)}"
    return f"{shown} {one if n == 1 else many}"


def _bad(why: str, say: str):
    return {"result": json.dumps({"error": why}), "say": say}


def convert_currency(amount: float = 1.0, from_currency: str = "EUR", to_currency: str = "RON", **_):
    try:
        amount = float(str(amount).replace(",", "."))
    except (TypeError, ValueError):
        return _bad("amount is not a number", "I need an amount as a number, sir.")
    f, t = code(from_currency), code(to_currency)
    if not f or not t:
        unknown = to_currency if not t else from_currency
        return _bad(f"unknown currency {unknown!r}", f"I don't know the currency {unknown}, sir.")
    if f == t:
        return {"result": json.dumps({"converted": round(amount, 2), "rate": 1.0}),
                "say": f"That's the same currency, sir: {_words(amount, f)}."}
    try:
        data = _rates(f, t)
    except RateError as e:
        return _bad(str(e), f"I couldn't get the exchange rate, sir: {e}.")
    rate = data["rates"].get(t)
    if rate is None:
        return _bad(f"no rate for {t}", f"The European Central Bank has no rate for {to_currency}, sir.")
    out = round(amount * float(rate), 2)
    return {"result": json.dumps({"amount": amount, "from": f, "to": t, "rate": rate, "converted": out,
                                  "date": data.get("date", "")}),
            "say": f"{_words(amount, f)} is {out:,.2f} {SPOKEN.get(t, (t, t))[1]}, sir, at the European Central "
                   f"Bank rate of {_when(data.get('date', ''))}."}


def get_exchange_rate(from_currency: str = "EUR", to_currency: str = "RON", **_):
    f, t = code(from_currency), code(to_currency)
    if not f or not t:
        return _bad("unknown currency", "I don't know one of those currencies, sir.")
    try:
        data = _rates(f, t)
    except RateError as e:
        return _bad(str(e), f"I couldn't get the exchange rate, sir: {e}.")
    rate = data["rates"].get(t)
    if rate is None:
        return _bad(f"no rate for {t}", f"There's no rate for {to_currency}, sir.")
    return {"result": json.dumps({"from": f, "to": t, "rate": rate, "date": data.get("date", "")}),
            "say": f"One {SPOKEN.get(f, (f, f))[0]} is {float(rate):.4f} {SPOKEN.get(t, (t, t))[1]}, sir "
                   f"({_when(data.get('date', ''))})."}


def list_rates(base_currency: str = "EUR", **_):
    b = code(base_currency)
    if not b:
        return _bad("unknown currency", f"I don't know the currency {base_currency}, sir.")
    try:
        data = _rates(b)
    except RateError as e:
        return _bad(str(e), f"I couldn't get the exchange rates, sir: {e}.")
    main = [c for c in ("RON", "USD", "GBP", "CHF", "EUR") if c in data["rates"] and c != b]
    say = ", ".join(f"{float(data['rates'][c]):.4f} {c}" for c in main)
    return {"result": json.dumps({"base": b, "rates": data["rates"], "date": data.get("date", "")}),
            "say": f"One {SPOKEN.get(b, (b, b))[0]} is {say}, sir. I have {len(data['rates'])} currencies in total."}


def _schema(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


CUR = {"type": "string", "description": "A currency code or name: EUR, RON (lei), USD (dollars), GBP (pounds)..."}
TOOLS = [
    _schema("convert_currency", "Convert an amount of money from one currency to another with today's rate "
            "('100 euro in lei', 'how much is 50 dollars in euro').",
            {"amount": {"type": "number"}, "from_currency": CUR, "to_currency": CUR},
            ("amount", "from_currency", "to_currency")),
    _schema("get_exchange_rate", "Today's exchange rate between two currencies ('euro to lei rate').",
            {"from_currency": CUR, "to_currency": CUR}, ("from_currency", "to_currency")),
    _schema("list_rates", "Today's rates of one currency against the others ('euro against all currencies').",
            {"base_currency": CUR}, ("base_currency",)),
]
FUNCTIONS = {"convert_currency": convert_currency, "get_exchange_rate": get_exchange_rate, "list_rates": list_rates}
