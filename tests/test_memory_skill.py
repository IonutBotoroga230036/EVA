import importlib.util

import pytest

spec = importlib.util.spec_from_file_location("mem_tools", "skills/memory/tools.py")
mem = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mem)


@pytest.mark.parametrize("raw,clean", [
    ("I go to gym on Tuesday", "Goes to gym on Tuesday"),
    ("I'm vegetarian", "Is vegetarian"),
    ("I usually train in the morning", "Usually trains in the morning"),
    ("my brother Andrei studies in Cluj", "Their brother Andrei studies in Cluj"),
    ("I don't drink coffee", "Doesn't drink coffee"),
    ("Tom works at Deloitte", "Tom works at Deloitte"),
])
def test_first_person_becomes_third_person(raw, clean):
    assert mem.to_third_person(raw) == clean
