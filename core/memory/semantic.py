"""
CORTEX - Semantic Memory: ChromaDB vector store.
"""

import chromadb
from chromadb.config import Settings
from loguru import logger


class SemanticMemory:
    def __init__(self, persist_dir: str = "./data/chromadb"):
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.knowledge = self._client.get_or_create_collection(name="knowledge")
        self.people = self._client.get_or_create_collection(name="people")
        self.projects = self._client.get_or_create_collection(name="projects")
        logger.info(
            f"CORTEX Semantic: {self.knowledge.count()} knowledge, "
            f"{self.people.count()} people, {self.projects.count()} project entries"
        )

    def store(self, collection_name: str, text: str, metadata: dict | None = None, doc_id: str | None = None) -> str:
        collection = self._client.get_collection(collection_name)
        doc_id = doc_id or f"{collection_name}_{collection.count()}"
        collection.upsert(documents=[text], metadatas=[metadata or {}], ids=[doc_id])
        return doc_id

    def search(self, collection_name: str, query: str, n_results: int = 5) -> list[dict]:
        collection = self._client.get_collection(collection_name)
        if collection.count() == 0:
            return []
        results = collection.query(query_texts=[query], n_results=min(n_results, collection.count()))
        output = []
        for i in range(len(results["ids"][0])):
            output.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
        return output

    def search_all(self, query: str, n_results: int = 5) -> list[dict]:
        all_results = []
        for name in ["knowledge", "people", "projects"]:
            results = self.search(name, query, n_results)
            for r in results:
                r["collection"] = name
            all_results.extend(results)
        all_results.sort(key=lambda x: x.get("distance", 999))
        return all_results[:n_results]