import asyncio

from core.events.bus import PulseEventBus


def test_publish_reaches_subscribers_and_wildcard():
    bus = PulseEventBus(redis_host="ignored", redis_port=1234)   # legacy signature still works
    got, everything = [], []
    bus.subscribe("tool.executed", got.append)
    bus.subscribe("*", everything.append)
    bus.publish("tool.executed", {"tool": "get_weather"})
    bus.publish("other", {"x": 1})
    assert [e["tool"] for e in got] == ["get_weather"]
    assert [e["event"] for e in everything] == ["tool.executed", "other"]
    assert len(bus.recent()) == 2


def test_broken_handler_does_not_break_publisher():
    bus = PulseEventBus()
    ok = []
    bus.subscribe("x", lambda d: 1 / 0)
    bus.subscribe("x", ok.append)
    bus.publish("x", {})
    assert len(ok) == 1


def test_async_handler_runs_inside_event_loop():
    bus = PulseEventBus()
    seen = []

    async def handler(d):
        seen.append(d["n"])

    bus.subscribe("x", handler)

    async def main():
        bus.publish("x", {"n": 7})
        await asyncio.sleep(0)
    asyncio.run(main())
    assert seen == [7]


def test_stop_silences_bus():
    bus = PulseEventBus()
    got = []
    bus.subscribe("x", got.append)
    bus.stop()
    bus.publish("x", {})
    assert got == []
