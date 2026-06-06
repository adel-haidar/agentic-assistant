import asyncio
import logging
import ast
import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from assistant.email.model import EmailMessage
from assistant.shared.base_llm_service import BaseLLMService

logger = logging.getLogger(__name__)


class MemoryClient(BaseLLMService):
    """Queries the MCP memory server for context relevant to a given topic.

    The memory server stores knowledge about the user gathered from past AI
    conversations (Claude, Codex, Antigravity). Before assessing or drafting a
    reply to an email, we search the memory server using the sender and subject
    as the query so the LLM has personal context about who is writing and why.

    Connects over the MCP streamable-HTTP transport, which uses a single HTTP
    endpoint (typically /mcp) for all JSON-RPC messages.
    """

    def __init__(
        self, bedrock_client, model_id: str, server_url: str, token: str = None
    ):
        """Configure the client with the streamable-HTTP endpoint of the memory server.

        Args:
            server_url: The full URL of the MCP memory server endpoint,
                e.g. 'http://ec2-ip:8000/mcp'.
        """
        super().__init__(bedrock_client=bedrock_client, model_id=model_id)
        self._server_url = server_url
        self._token = token
        if not server_url.endswith("/mcp"):
            logger.warning(
                "MCP_MEMORY_URL %r does not end with '/mcp' — "
                "memory searches will likely fail with 404. "
                "Set MCP_MEMORY_URL to the streamable-HTTP endpoint, e.g. 'http://host:8000/mcp'.",
                server_url,
            )

    def search(self, email: EmailMessage) -> str:
        """Search the MCP memory server for context relevant to an email.

        Asks the LLM to derive a list of search queries from the email, then
        runs each query against the memory server. Results are concatenated into
        a single plain-text block suitable for inclusion in an LLM prompt.

        Args:
            email: The incoming email whose sender, subject, and body preview are
                used to generate search queries.

        Returns:
            A multi-line string of memory results, or an empty string if the
            memory server is unreachable or the search fails.
        """
        prompt = f"""
              You are an email assistant. You read an email and return an array of keywords.
              These keywords will be used to search an mcp-memory server for all information that could enrich the context be helpful to better respond to this email 
              please analyse the given email and create the most effective search query that can yield the best results.

              Sender: {email.sender}
              Subject: {email.subject}
              Body:
              {email.body_preview}
              Rules:
                - Some APIs are very error prone to some symbol, try to keep your query simple and don't include symbols that may cause errors.
                - Memories are not always exact match of the words mentioned in the email. Examples:
                  If you find the word: Resume, the memory server might have a memory called job application, job, changing jobs etc.
                                        Certificate: Zeugnisse, Zertificate, Abschlusse, exam, etc.
                                        Dokumente: Documents, Unterlagen, Dateien, Files, folder, case etc.
                                        Bewerbung: Application, Jobbewerbung, Jobapplication, job application etc.
              Result:
                - The result MUST ONLY be a python array containing all recommended quries. No explination, no reasoning, no other string.
                - If the result contains anything other than pure array in python syntax, the program will fail.

          """
        try:
            raw = self._strip_markdown(self._invoke(prompt))
            queries = ast.literal_eval(raw)
            logger.debug(
                "Memory search: %d queries for email from %r",
                len(queries),
                email.sender,
            )
            result = "Context that might be relevant"
            for query in queries:
                result = result + "\n" + asyncio.run(self._search(query))
            return result
        except Exception:
            logger.warning(
                "Memory search failed for query %r", email.subject, exc_info=True
            )
            return ""


    _BANK_STATEMENT_QUERY = "Konto Auszug"

    # Semantic queries for supplementary financial context (prior analyses,
    # spending notes, savings goals). The bank statement itself is fetched
    # separately via fetch_bank_statement().
    _FINANCIAL_CONTEXT_QUERIES = [
        "Conversations or notes about personal finances, monthly spending, costs, budget, or savings goals",
        "Previous financial analysis, spending assessment, budget recommendations, or yearly savings progress",
    ]

    def fetch_bank_statement(self) -> str:
        """Fetch the most recent bank statement from the MCP memory server.

        Returns:
            The raw statement text, or an empty string if nothing is found.
        """
        try:
            return asyncio.run(self._search(self._BANK_STATEMENT_QUERY))
        except Exception:
            logger.warning("Failed to fetch bank statement from memory", exc_info=True)
            return ""

    def search_financial_context(self) -> str:
        """Search for supplementary financial context: prior analyses, spending
        conversations, and savings goal notes.

        Returns:
            A multi-line string of memory results, or an empty string if the
            memory server is unreachable or all queries return nothing.
        """
        parts: list[str] = []
        for query in self._FINANCIAL_CONTEXT_QUERIES:
            try:
                result = asyncio.run(self._search(query))
                if result:
                    parts.append(result)
            except Exception:
                logger.warning("Financial memory search failed for query %r", query, exc_info=True)

        return "\n".join(parts)

    async def fetch_bank_statement_for_month(self, month: str) -> str:
        """Fetch a specific month's bank statement from MCP memory.

        Args:
            month: The month to search for, e.g. '2026-01'.
        """
        try:
            return await self._search(f"bank statement {month}")
        except Exception:
            logger.warning("Failed to fetch bank statement for month %r", month, exc_info=True)
            return ""

    async def fetch_financial_context(self) -> str:
        """Async version: search for supplementary financial context."""
        parts: list[str] = []
        for query in self._FINANCIAL_CONTEXT_QUERIES:
            try:
                result = await self._search(query)
                if result:
                    parts.append(result)
            except Exception:
                logger.warning("Financial memory search failed for query %r", query, exc_info=True)
        return "\n".join(parts)

    async def save_job_run(
        self,
        date: str,
        strong_count: int,
        good_count: int,
        top_summary: str,
    ) -> None:
        """Persist a job hunt run summary to the MCP memory server."""
        content = (
            f"Run on {date}. {strong_count} strong matches, {good_count} good matches saved to DB."
            f"{top_summary}"
        )
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        async with httpx.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(
                url=self._server_url, http_client=http_client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.call_tool(
                        "save",
                        {
                            "title": f"Job Hunt Run — {date}",
                            "tags": ["job-search", "run-summary", "switzerland"],
                            "content": content,
                        },
                    )
        logger.debug("Job run summary saved to MCP memory for %s", date)

    async def _search(self, query: str) -> str:
        """Internal async implementation that performs the actual MCP call."""
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        async with httpx.AsyncClient(headers=headers) as http_client:
            async with streamable_http_client(
                url=self._server_url, http_client=http_client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("search", {"query": query})
                    if result.isError or not result.content:
                        return ""
                    return "\n".join(
                        item.text
                        for item in result.content
                        if hasattr(item, "text") and item.text
                    )
