from core.memory.cortex import Cortex
from tests.helpers import FakeEmbedder


def test_turns_and_facts_persist_across_restarts(tmp_path):
    db = tmp_path / "c.db"
    c = Cortex(str(db))
    c.log_turn("s1", "user", "hello")
    c.log_turn("s1", "assistant", "Good evening, sir.")
    c.remember("Prefers jazz while working", "preference")
    c.close()
    c2 = Cortex(str(db))                                  # simulate a server restart
    assert c2.stats()["episodes"] == 2 and c2.stats()["facts"] == 1
    turns = c2.recent_turns(limit=6)
    assert [t["role"] for t in turns] == ["user", "assistant"]


def test_recent_turns_never_start_mid_exchange(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    for role, text in [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")]:
        c.log_turn("s", role, text)
    assert c.recent_turns(limit=3)[0]["role"] == "user"


def test_exact_duplicate_is_not_stored_twice(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    assert c.remember("Lives in Breda")["status"] == "added"
    assert c.remember("lives in breda.")["status"] == "duplicate"
    assert c.stats()["facts"] == 1


def test_similar_fact_replaces_old_one_newest_wins(tmp_path):
    c = Cortex(str(tmp_path / "c.db"), embedder=FakeEmbedder(), dedup_threshold=0.7)
    c.remember("Lives in Breda Netherlands", "place")
    res = c.remember("Lives in Tilburg Netherlands", "place")
    assert res["status"] == "updated" and res["replaced"] == "Lives in Breda Netherlands"
    assert [f["text"] for f in c.all_facts()] == ["Lives in Tilburg Netherlands"]


def test_different_categories_do_not_merge(tmp_path):
    c = Cortex(str(tmp_path / "c.db"), embedder=FakeEmbedder(), dedup_threshold=0.7)
    c.remember("Tom works at Deloitte in Breda", "person")
    c.remember("Tom works at Deloitte in Breda office", "project")
    assert c.stats()["facts"] == 2


def test_semantic_recall_ranks_relevant_fact_first(tmp_path):
    c = Cortex(str(tmp_path / "c.db"), embedder=FakeEmbedder())
    c.remember("Prefers jazz music while working", "preference")
    c.remember("Height is 165 cm", "identity")
    c.remember("Brother lives in Romania", "person")
    top = c.recall("what music do I like while working", k=1, min_score=0.1)
    assert top and "jazz" in top[0]["text"]


def test_keyword_recall_without_embeddings(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))                    # Ollama down -> keyword mode
    c.remember("Prefers jazz music while working")
    c.remember("Height is 165 cm")
    assert "jazz" in c.recall("jazz music", k=1)[0]["text"]


def test_secrets_are_refused(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    assert c.remember("My password is hunter2")["status"] == "refused"
    assert c.remember("card 4111 1111 1111 1111")["status"] == "refused"
    assert c.stats()["facts"] == 0


def test_forget_clear_match_and_ambiguous(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Prefers jazz music while working")
    c.remember("Height is 165 cm")
    assert c.forget("zebra quantum")["status"] in ("not_found", "ambiguous")
    assert c.forget("jazz music while working")["status"] == "forgotten"
    assert c.stats()["facts"] == 1


def test_episode_search(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    c.log_turn("s", "user", "tell me about the Deloitte meeting")
    c.log_turn("s", "user", "what's the weather")
    hits = c.search_episodes("Deloitte")
    assert len(hits) == 1 and "Deloitte" in hits[0]["content"]


def test_forget_needs_a_query(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Height is 165 cm")
    assert c.forget("")["status"] == "need_query" and c.stats()["facts"] == 1


def test_keyword_forget_matches_partial_description(tmp_path):
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Studies at Neymar University")
    c.remember("Height is 165 cm")
    assert c.forget("the one about Neymar")["status"] == "forgotten"
    assert [f["text"] for f in c.all_facts()] == ["Height is 165 cm"]


def test_forget_recent_only_removes_new_facts(tmp_path):
    import time as _t
    c = Cortex(str(tmp_path / "c.db"))
    c.remember("Old fact about Rome")
    c._db.execute("UPDATE facts SET created = ?", (_t.time() - 3600,))
    c._db.commit()
    c.remember("New fact about Paris")
    res = c.forget_recent(15)
    assert res["forgotten"] == ["New fact about Paris"]
    assert [f["text"] for f in c.all_facts()] == ["Old fact about Rome"]


def test_unready_embedder_never_blocks_and_falls_back(tmp_path):
    import threading

    class Slow(FakeEmbedder):
        def __init__(self):
            self.ready = threading.Event()
            self.calls = 0

        def __call__(self, texts):
            if not self.ready.is_set():
                return None
            self.calls += 1
            return super().__call__(texts)
    emb = Slow()
    c = Cortex(str(tmp_path / "c.db"), embedder=emb)
    c.remember("Prefers jazz music while working")
    assert c.stats()["recall"] == "keyword" and "jazz" in c.recall("jazz music")[0]["text"]
    emb.ready.set()
    assert c.stats()["recall"] == "semantic"
