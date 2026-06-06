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
        self,
        folder: str,
        delta_link: str | None,
        max_messages: int | None = None,
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
            max_messages: If set, stop collecting results after this many messages.
                Useful for rate-limiting how much is processed per sync.

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
            if max_messages:
                params["$top"] = str(max_messages)

        all_messages: list[dict] = []
        new_delta_link: str | None = None

        while url:
            response = httpx.get(
                url, headers=self._headers(), params=params, timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            all_messages.extend(data.get("value", []))

            if max_messages and len(all_messages) >= max_messages:
                all_messages = all_messages[:max_messages]
                break

            if "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
                break

            # Follow the next page; delta link params must not be re-sent.
            url = data.get("@odata.nextLink")
            params = None

        logger.debug("Fetched %d messages from folder %r", len(all_messages), folder)
        return all_messages, new_delta_link

    def save_draft(
        self, draft: EmailDraft, recipient: str, files: list | None = None
    ) -> str:
        """Create a draft reply in Outlook and optionally attach OneDrive files.

        Posts the draft to the Graph API `/me/messages` endpoint (which creates
        it in the Drafts folder). If `files` is provided, calls `attach_files`
        to download each file from OneDrive and upload it as an attachment.

        Args:
            draft: The composed reply containing subject and body.
            recipient: The email address the draft will be addressed to.
            files: Optional list of OneDrive file dicts (must have 'id' and
                'name' keys) to attach to the draft.

        Returns:
            The Graph API message ID of the newly created draft.
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
        draft_id = response.json()["id"]
        logger.info("Draft created in Outlook: draft_id=%s recipient=%r", draft_id, recipient)

        if files:
            self.attach_files(draft_id, files)

        return draft_id

    def send_mail(self, subject: str, body: str, recipient: str) -> None:
        """Send an email immediately (not a draft) via the Graph API sendMail endpoint."""
        httpx.post(
            f"{_GRAPH_BASE}/me/sendMail",
            headers=self._headers(),
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": recipient}}],
                },
                "saveToSentItems": "true",
            },
            timeout=30.0,
        ).raise_for_status()
        logger.info("Email sent to %r: %r", recipient, subject)

    def attach_files(self, draft_id: str, files: list) -> None:
        """Download files from OneDrive and attach them to a draft message.

        Downloads each file's binary content via the Graph API, base64-encodes
        it, and uploads it as a `fileAttachment`. Files that fail to download or
        attach are skipped with a warning so a single bad file does not block the
        rest of the draft.

        Args:
            draft_id: The Graph API message ID of the draft to attach files to.
            files: A list of dicts, each with at least 'id' (OneDrive item ID)
                and 'name' (file name) keys.
        """
        import base64

        for file in files:
            try:
                content_response = httpx.get(
                    f"{_GRAPH_BASE}/me/drive/items/{file['id']}/content",
                    headers=self._headers(),
                    timeout=60.0,
                    follow_redirects=True,
                )
                content_response.raise_for_status()
                encoded = base64.b64encode(content_response.content).decode()
                httpx.post(
                    f"{_GRAPH_BASE}/me/messages/{draft_id}/attachments",
                    headers=self._headers(),
                    json={
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": file["name"],
                        "contentBytes": encoded,
                    },
                    timeout=60.0,
                )
                logger.debug("Attached file %r to draft %s", file["name"], draft_id)
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Skipping attachment %r: HTTP %s", file["name"], e.response.status_code
                )
                continue
