import logging

import httpx

from assistant.email.auth_service import MicrosoftTokenStore
from assistant.email.model import EmailDraft

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DELTA_SELECT = "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId"


class GraphClient:
    """Handles all communication with the Microsoft Graph API.

    The Graph API is Microsoft's unified API for accessing Outlook, OneDrive,
    Teams, and more. This client covers only the email-related operations we need:
    fetching new messages and saving draft replies.

    Every request is authenticated using a short-lived access token obtained
    from the `MicrosoftTokenStore`.
    """

    def __init__(self, token_store: MicrosoftTokenStore):
        """Store the token store so we can get a fresh access token on each request.

        Args:
            token_store: The object that holds the user's OAuth refresh token and
                knows how to exchange it for a usable access token.
        """
        self._token_store = token_store

    def _headers(self) -> dict[str, str]:
        """Build the Authorization header required by every Graph API request.

        Calling this fetches a fresh access token each time, which ensures we
        never send an expired token.

        Returns:
            A dict with the single `Authorization` header, ready to pass to httpx.
        """
        return {"Authorization": f"Bearer {self._token_store.get_access_token()}"}

    def fetch_delta(
        self, folder: str, delta_link: str | None
    ) -> tuple[list[dict], str | None]:
        """Fetch only the emails that have arrived since the last sync.

        The Graph API's 'delta' endpoint tracks a bookmark (called a delta link)
        for each folder. On the first call we don't have a bookmark yet, so the
        API returns everything. On subsequent calls we pass back the bookmark and
        the API returns only what changed since then — this avoids re-processing
        the entire inbox every time.

        The API may paginate results across multiple pages linked by
        @odata.nextLink. Only the final page carries @odata.deltaLink, so we
        must follow all next-links before returning.

        Args:
            folder: The name of the Outlook folder to check, e.g. 'inbox' or
                'junkemail'.
            delta_link: The bookmark URL returned by the previous sync. Pass
                `None` on the first ever call.

        Returns:
            A tuple of:
            - A list of raw email dicts as returned by the Graph API.
            - The new delta link to store and pass on the next sync call.
        """
        # On the first call use the base delta URL with $select; on subsequent
        # calls the stored delta_link already encodes all parameters so we must
        # not append $select again (it is unsupported on delta link URLs).
        if delta_link:
            url = delta_link
            params: dict | None = None
        else:
            url = f"{_GRAPH_BASE}/me/mailFolders/{folder}/messages/delta"
            params = {"$select": _DELTA_SELECT}

        all_messages: list[dict] = []
        new_delta_link: str | None = None

        while url:
            response = httpx.get(url, headers=self._headers(), params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            all_messages.extend(data.get("value", []))

            if "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
                break

            # Follow the next page; delta link params must not be re-sent.
            url = data.get("@odata.nextLink")
            params = None

        return all_messages, new_delta_link

    def save_draft(self, draft: EmailDraft, recipient: str) -> None:
        """Save a draft reply to the user's Outlook Drafts folder.

        This creates the message via the Graph API but does NOT send it.
        The user can review and send it manually from Outlook.

        Args:
            draft: The drafted reply, including subject and body text.
            recipient: The email address the draft should be addressed to.
        """
        response = httpx.post(
            f"{_GRAPH_BASE}/me/messages",
            headers=self._headers(),
            json={
                "subject": draft.subject,
                "body": {"contentType": "text", "content": draft.draft_body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            timeout=30.0,
        )
        response.raise_for_status()
