"""
Vocabulary correction for speech recognition.

The browser's recognizer has never heard of Radboud, Nijmegen, or your projects,
so it writes "roundabout university" and "Neymar can". This module fixes the
transcript on the server, before the brain sees it, using the Vocabulary
sections in EVA.md and EVA.local.md:

    ## Vocabulary
    - Radboud University: roundabout university, read about university, rad bout university
    - Nijmegen: neymar can, nay megan, nai megan
    - Breda

Two passes, conservative on purpose (a wrong "correction" is worse than none):
  1. Aliases: explicit misheard phrases -> the right term. Whole words only.
  2. Near misses: a word or two-word window that is almost the term (ratio >= 0.86,
     same first letter, similar length), e.g. "Tilbourg" -> "Tilburg".
Only applied to voice input. Typed text is left alone. Every change is logged.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

_ENTRY = re.compile(r"^\s*[-*]\s*([^:]+?)\s*(?::\s*(.+))?\s*$")
_WORD = re.compile(r"[A-Za-zÀ-ÿ0-9']+")


def _squash(s: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", s.lower())
    return re.sub(r"(.)\1+", r"\1", s)                     # "zippzapp" ~ "zipzap"


@lru_cache(maxsize=8)
def parse_vocabulary(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    entries = []
    for line in text.splitlines():
        m = _ENTRY.match(line)
        if not m:
            continue
        term = m.group(1).strip()
        aliases = tuple(a.strip() for a in (m.group(2) or "").split(",") if a.strip())
        if term:
            entries.append((term, aliases))
    return tuple(entries)


def correct(text: str, vocab_text: str, threshold: float = 0.86) -> tuple[str, list[tuple[str, str]]]:
    entries = parse_vocabulary(vocab_text or "")
    if not entries or not text:
        return text, []
    changes: list[tuple[str, str]] = []

    # 1) explicit aliases, longest first so "roundabout university" beats "roundabout"
    pairs = sorted(((a, term) for term, aliases in entries for a in aliases), key=lambda p: -len(p[0]))
    for alias, term in pairs:
        rx = re.compile(r"(?<![\w'])" + r"\s+".join(map(re.escape, alias.split())) + r"(?![\w'])", re.I)
        if rx.search(text):
            for m in rx.finditer(text):
                changes.append((m.group(0), term))
            text = rx.sub(term, text)

    # 2) near misses on single words and two-word windows
    for term, _ in entries:
        key = _squash(term)
        if len(key) < 5:                                    # short words are too risky to fuzz
            continue
        n_words = len(term.split())
        words = list(_WORD.finditer(text))
        for size in {n_words, n_words + 1}:
            for i in range(len(words) - size + 1):
                span = text[words[i].start():words[i + size - 1].end()]
                cand = _squash(span)
                if not cand or term.lower() in span.lower():     # already correct (or contains it)
                    continue
                if cand[0] != key[0] or abs(len(cand) - len(key)) > 2:
                    continue
                if SequenceMatcher(None, cand, key).ratio() >= threshold:
                    changes.append((span, term))
                    text = text.replace(span, term, 1)
                    break
    return text, changes
