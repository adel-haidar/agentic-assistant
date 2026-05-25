import asyncio
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


class MemoryClient:
    """Queries the MCP memory server for context relevant to a given topic.

    The memory server stores knowledge about the user gathered from past AI
    conversations (Claude, Codex, Antigravity). Before assessing or drafting a
    reply to an email, we search the memory server using the sender and subject
    as the query so the LLM has personal context about who is writing and why.

    Connects over the MCP streamable-HTTP transport, which uses a single HTTP
    endpoint (typically /mcp) for all JSON-RPC messages.
    """

    def __init__(self, server_url: str):
        """Configure the client with the streamable-HTTP endpoint of the memory server.

        Args:
            server_url: The full URL of the MCP memory server endpoint,
                e.g. 'http://ec2-ip:8000/mcp'.
        """
        self._server_url = server_url
        if not server_url.endswith("/mcp"):
            logger.warning(
                "MCP_MEMORY_URL %r does not end with '/mcp' — "
                "memory searches will likely fail with 404. "
                "Set MCP_MEMORY_URL to the streamable-HTTP endpoint, e.g. 'http://host:8000/mcp'.",
                server_url,
            )

    def search(self, query: str) -> str:
        """Search the memory server and return relevant context as plain text.

        Opens a connection, calls the `search_nodes` tool on the MCP server,
        and joins all returned text fragments into a single string. If the
        server is unreachable or returns nothing, an empty string is returned
        so the rest of the pipeline is not interrupted.

        The MCP SDK is async-only, so we use `asyncio.run()` to run it from
        this synchronous method. This is safe here because FastAPI runs sync
        route handlers in a thread pool that has no active event loop.

        Args:
            query: A natural-language search string, typically the sender's
                email address combined with the email subject.

        Returns:
            A plain-text block of relevant memories, or an empty string if
            nothing was found or the server could not be reached.
        """
        try:
            return asyncio.run(self._search(query))
        except Exception:
            logger.warning("Memory search failed for query %r", query, exc_info=True)
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
