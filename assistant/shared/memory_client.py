import asyncio
import logging
import ast

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

    def __init__(self, bedrock_client, model_id: str, server_url: str):
        """Configure the client with the streamable-HTTP endpoint of the memory server.

        Args:
            server_url: The full URL of the MCP memory server endpoint,
                e.g. 'http://ec2-ip:8000/mcp'.
        """
        super().__init__(bedrock_client=bedrock_client, model_id=model_id)
        self._server_url = server_url
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
                "Memory search: %d queries for email from %r", len(queries), email.sender
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

    async def _search(self, query: str) -> str:
        """Internal async implementation that performs the actual MCP call."""
        async with streamable_http_client(url=self._server_url) as (read, write, _):
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
