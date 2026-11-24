---
name: currency_converter
description: Converts money between currencies (euro, lei/RON, dollars, pounds and the other European Central Bank currencies) with today's rate. Use when the user asks how much an amount is in another currency or for an exchange rate.
enabled: true
trusted: true
triggers: ["convert euro", "in lei", "in ron", "in dollars", "exchange rate", "how much is", "euros in", "to dollars"]
permissions:
  network: ["api.frankfurter.dev"]
  filesystem: []
---
Use `convert_currency` for "100 euro in lei", "how much is 50 dollars in euro".
Use `get_exchange_rate` for "what's the euro to lei rate".
Use `list_rates` for "euro against all currencies".
Rates are the European Central Bank's reference rates (working days, around 16:00 CET), from frankfurter.dev,
free and without a key. Rewritten by hand on 25 Nov 2026: the FORGE version called api.frankfurter.app, which
moved to api.frankfurter.dev/v1.
