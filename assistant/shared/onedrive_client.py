import logging
from urllib.parse import quote

import httpx

from assistant.email.auth_service import MicrosoftTokenStore

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MAX_RESULTS = 5


class OneDriveFile:
    """A lightweight container for a OneDrive file returned by a search."""

    def __init__(self, id: str, name: str, web_url: str, mime_type: str):
        self.id = id
        self.name = name
        self.web_url = web_url
        self.mime_type = mime_type

    def __str__(self) -> str:
        return f"{self.name} ({self.web_url})"


class OneDriveClient:
    """Searches the user's OneDrive for documents relevant to an email.

    Uses the same Microsoft Graph API token as the email integration — no
    additional authentication is needed. Search results (file names and links)
    are surfaced to the LLM as context when drafting a reply, so it can
    reference or suggest attaching relevant documents.
    """

    def __init__(self, token_store: MicrosoftTokenStore):
        """Configure the client with the shared OAuth token store.

        Args:
            token_store: The object that provides fresh Graph API access tokens.
        """
        self._token_store = token_store

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_store.get_access_token()}"}

    def search(self, query: str) -> list[OneDriveFile]:
        """Search OneDrive for files matching a query string.

        Uses the Graph API's full-text search, which matches against file
        names, content, and metadata. Returns up to 5 results so the context
        passed to the LLM stays concise.

        If the search fails (e.g. token expired mid-call or no results), an
        empty list is returned so the rest of the pipeline continues normally.

        Args:
            query: The search term — typically the email subject or key phrases
                from it.

        Returns:
            A list of `OneDriveFile` objects, each with a name and a direct
            web URL to the file in OneDrive.
        """
        try:
            # The OData function syntax requires the query to be part of the URL path.
            url = f"{_GRAPH_BASE}/me/drive/root/search(q='{quote(query)}')"
            response = httpx.get(
                url,
                headers=self._headers(),
                params={
                    "$select": "id,name,webUrl,file",
                    "$top": _MAX_RESULTS,
                },
            )
            response.raise_for_status()
            items = response.json().get("value", [])
            return [
                OneDriveFile(
                    id=item["id"],
                    name=item["name"],
                    web_url=item.get("webUrl", ""),
                    mime_type=item.get("file", {}).get("mimeType", "unknown"),
                )
                for item in items
                if "file" in item  # filter out folders
            ]
        except Exception:
            logger.warning("OneDrive search failed for query %r", query, exc_info=True)
            return []
