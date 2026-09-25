---
name: km_to_miles
description: Converts a distance between kilometres and miles. Use when the user asks to convert km to miles or miles to km.
enabled: true
trusted: true
triggers: ["convert km to miles", "kilometres to miles", "how many miles is", "convert miles to km", "how many km is"]
permissions:
  network: []
  filesystem: ["data/skills/km_to_miles"]
---
Use `km_to_miles` to convert a kilometre value to miles.
Use `miles_to_km` to convert a miles value to kilometres.
Both tools are pure math, no confirmation needed. State the result rounded to 2 decimal places.
