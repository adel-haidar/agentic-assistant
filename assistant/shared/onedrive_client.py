import ast
import logging
import re
import json

import httpx

from assistant.email.auth_service import MicrosoftTokenStore
from assistant.email.model import EmailMessage
from assistant.shared.base_llm_service import BaseLLMService

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MAX_RESULTS = 5


def _sanitize_query(query: str) -> str:
    """Strip characters that break OData string literal parsing in the Graph API path.

    The search(q='...') OData function in the URL path cannot handle colons,
    pipes, angle brackets, and similar punctuation — they cause 400 errors.
    """
    cleaned = re.sub(r"[^\w\s\-\.]", " ", query)
    return " ".join(cleaned.split())[:100]


class OneDriveFile:
    """A lightweight container for a OneDrive file returned by a search."""

    def __init__(self, id: str, name: str, web_url: str, mime_type: str):
        self.id = id
        self.name = name
        self.web_url = web_url
        self.mime_type: str | None = None
        self.last_modified: str | None = None

    def __str__(self) -> str:
        return f"{self.name} ({self.web_url})"


class OneDriveClient(BaseLLMService):
    """Searches the user's OneDrive for documents relevant to an email.

    Uses the same Microsoft Graph API token as the email integration — no
    additional authentication is needed. Search results (file names and links)
    are surfaced to the LLM as context when drafting a reply, so it can
    reference or suggest attaching relevant documents.
    """

    def __init__(self, bedrock_client, model_id: str, token_store: MicrosoftTokenStore):
        super().__init__(bedrock_client=bedrock_client, model_id=model_id)
        self._token_store = token_store

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_store.get_access_token()}"}

    def search(self, email: EmailMessage) -> list[OneDriveFile]:
        """Search OneDrive for files relevant to an incoming email.

        Asks the LLM to generate a list of search queries from the email, then
        runs each query against the Graph API full-text search (which matches
        file names, content, and metadata). Duplicate files across queries are
        de-duplicated by ID, then a second LLM call filters out older copies of
        the same document. Returns up to `_MAX_RESULTS` files per query.

        If the Graph API search fails the exception is caught and an empty list
        is returned so the rest of the pipeline continues normally.

        Args:
            email: The incoming email whose sender, subject, and body preview are
                used to generate search queries.

        Returns:
            A deduplicated list of file dicts (with at least 'id', 'name', and
            'webUrl' keys) representing relevant OneDrive files.
        """

        prompt = f"""
              You are an email analyser (computer program). You read an email and return an array of keywords.
              For each email you receive, we want to search for all relevant information in onedrive.
              please analyse the given email and create the most effective search query that can yield the best results.

              Sender: {email.sender}
              Subject: {email.subject}
              Body:
              {email.body_preview}
              Rules:
                - Some APIs like Microsoft Graph are very error prone to some symbol, try to keep your query simple and don't include symbols that may cause errors.
                - Files are not always exact match of the words mentioned in the email. Examples:
                  If you find the word: Resume, one drive might have a file called CV, Lebenslauf etc.
                                        Certificate: Zeugnisse, Zertificate, Abschlusse etc.
                                        Dokumente: Documents, Unterlagen, Dateien, Files etc.
                                        Bewerbung: Application, Jobbewerbung, Jobapplication.
              Result:
                - The result MUST ONLY be a python array containing all recommended quries. No explination, no reasoning, no other string.
                - If the result contains anything other than pure array in python syntax, the program will fail.

          """
        raw = self._strip_markdown(self._invoke(prompt))
        queries = ast.literal_eval(raw)  # ['CV', 'Lebenslauf', 'Zeugnisse', ...]
        all_files = []
        seen_ids = set()
        try:
            # The OData function syntax requires the query to be part of the URL path.
            for query in queries:
                url = f"{_GRAPH_BASE}/me/drive/root/search(q='{query}')"
                response = httpx.get(
                    url,
                    headers=self._headers(),
                    params={
                        "$select": "id,name,webUrl,file,lastModifiedDateTime",
                        "$top": _MAX_RESULTS,
                    },
                )
                response.raise_for_status()
                items = response.json().get("value", [])
                for item in items:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        all_files.append(item)
            # Only return actual files, not folders
            results = [f for f in all_files if "file" in f]
            logger.debug(
                "OneDrive search: %d queries → %d unique files before dedup",
                len(queries),
                len(results),
            )
            return self.filter_duplicates(results)
        except Exception:
            logger.warning("OneDrive search failed", exc_info=True)
            return []

    def filter_duplicates(self, files: list[OneDriveFile]) -> list[OneDriveFile]:
        """Use the LLM to remove older copies of semantically duplicate files.

        Sends the file list (names + last-modified dates) to the LLM and asks it
        to keep only the newest version when multiple files represent the same
        document in different languages or with different naming conventions
        (e.g. 'CV_2021.pdf', 'Lebenslauf.pdf' → keep the newest).

        Args:
            files: A list of file dicts, each with at least 'name' and optionally
                'lastModifiedDateTime' keys.

        Returns:
            A filtered subset of `files` containing only the files the LLM chose
            to keep. Returns the original list unchanged if the LLM call fails.
        """
        try:
            file_info = "\n".join(
                f"- {f['name']} (modified: {f.get('lastModifiedDateTime', 'unknown')})"
                for f in files
            )

            prompt = f"""You are a file organizer. Given these files, remove older duplicates.
                Files with similar names in different languages are duplicates:
                CV_2021, CV_Old, CV_Adel, Lebenslauf → keep only the newest
                Certificates, Zertifikate → keep only the newest
                Contracts, Verträge → keep only the newest

                Files:
                {file_info}

                Return ONLY a JSON array of file names to keep. Example: ["CV.pdf", "Zeugnisse.pdf"]
                No other text."""

            raw = self._strip_markdown(self._invoke(prompt).strip())
            selected_names = json.loads(raw.replace("'", '"'))
            result = [f for f in files if f["name"] in selected_names]
            logger.debug(
                "filter_duplicates: %d files → %d after dedup", len(files), len(result)
            )
            return result
        except Exception as e:
            logger.warning("Filtering failed: %s", e)
            raise
