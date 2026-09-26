---
name: lights
description: Control the user's LED strips (colour, brightness, on and off) and set moods that combine lights and music (AURA).
enabled: true
trusted: true
triggers: ["lights", "light", "led", "strip", "mood", "i'm home", "i feel", "dim", "brighter"]
permissions:
  network: ["local network (Tuya devices, port 6668)"]
  filesystem: ["data/lights", "data/moods.json"]
---
lights_set for colour and brightness ("make the lights purple", "lights to 30 percent", "warm white").
lights_power for on and off. set_mood for "I'm home", "I feel red", "set the mood to relax": lights plus
a playlist. create_mood when the user defines a new mood. list_moods to name them.
