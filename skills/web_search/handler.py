"""
Web Search Skill - Gives E.V.A. access to the internet.
Uses DuckDuckGo (no API key needed, no tracking).
"""

from loguru import logger

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False


class WebSearchSkill:
    def __init__(self):
        self.name = "web_search"
        if not SEARCH_AVAILABLE:
            logger.warning("web_search skill: duckduckgo_search not installed")

    async def execute(self, user_input: str, eva) -> str:
        if not SEARCH_AVAILABLE:
            return "I need the duckduckgo-search package installed, sir. Run: pip install duckduckgo-search"

        try:
            # Extract search query from natural language
            query = await self._extract_query(user_input, eva)

            # Search the web
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=5):
                    results.append(r)

            if not results:
                return f"I searched for '{query}' but found no results, sir."

            # Format results for the LLM to summarize
            results_text = "\n\n".join(
                f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}"
                for r in results
            )

            # Have E.V.A. summarize the results in her voice
            summary_prompt = (
                f"{eva.persona['personality']}\n\n"
                f"The user asked: {user_input}\n\n"
                f"Here are the web search results:\n{results_text}\n\n"
                f"Summarize the key findings concisely in your voice. "
                f"Cite sources when relevant. Be brief."
            )
            response = await eva._local_inference(summary_prompt)
            return response

        except Exception as e:
            logger.error(f"web_search error: {e}")
            return f"Search failed, sir: {e}"

    async def _extract_query(self, user_input: str, eva) -> str:
        """Extract a clean search query from natural language."""
        prompt = (
            "Extract a concise web search query from this request. "
            "Return ONLY the search query, nothing else.\n\n"
            f"Request: {user_input}\n"
            "Search query:"
        )
        query = await eva._local_inference(prompt, model=eva.config["inference"]["local"]["router_model"])
        # Clean up the query
        query = query.strip().strip('"').strip("'").strip()
        if len(query) > 100:
            query = query[:100]
        if not query:
            query = user_input
        logger.info(f"web_search: query = '{query}'")
        return query