import logging
import httpx
import boto3
import os

from functools import lru_cache
from typing import Annotated
from botocore import model
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from assistant.banking.bank_adviser import BankAdviser
from assistant.email.auth_service import MicrosoftTokenStore, get_token_store
from assistant.email.email_assessor import EmailAssessor
from assistant.email.email_response_writer import EmailResponseWriter
from assistant.email.model import EmailMessage, EmailSyncResult
from assistant.shared.graph_client import GraphClient
from assistant.shared.memory_client import MemoryClient
from assistant.shared.onedrive_client import OneDriveClient
from assistant.shared.settings import Settings, get_settings

logger = logging.getLogger(__name__)
app = FastAPI()

# These are type aliases that tell FastAPI how to inject dependencies into route
# functions. When a route function declares a parameter with one of these types,
# FastAPI automatically calls the corresponding function (get_settings or
# get_token_store) and passes the result in.
SettingsDep = Annotated[Settings, Depends(get_settings)]
TokenStoreDep = Annotated[MicrosoftTokenStore, Depends(get_token_store)]

# Tracks the Graph API delta bookmark for each folder. None means we haven't
# synced yet, so the next call will fetch all messages.
delta_links: dict[str, str | None] = {"inbox": None, "junkemail": None}


def get_fresh_token() -> str:
    response = httpx.post(
        "http://localhost:8002/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["MCP_MEMORY_REFRESH_TOKEN"],
            "client_id": os.environ["MCP_MEMORY_CLIENT_ID"],
        },
    )
    return response.json()["access_token"]


@lru_cache
def _get_bedrock_client(region: str):
    """Create a boto3 Bedrock client for the given AWS region, cached per region.

    boto3 clients are thread-safe and relatively expensive to create, so we
    cache one per region rather than creating a new one on every request.

    Args:
        region: The AWS region string, e.g. 'eu-central-1'.

    Returns:
        A boto3 `bedrock-runtime` client.
    """
    return boto3.client("bedrock-runtime", region_name=region)


@app.get("/")
def health():
    """Health check endpoint.

    Returns a simple JSON response to confirm the server is running.
    Useful for load balancers or uptime monitors.
    """
    return {"status": "ok"}


@app.get("/auth/microsoft/login")
def login(token_store: TokenStoreDep):
    """Redirect the user to Microsoft's login page to begin the OAuth flow.

    After the user signs in and grants permission, Microsoft sends them back
    to `/auth/microsoft/callback` with a one-time code in the URL.
    """
    return RedirectResponse(token_store.get_authorize_url())


@app.get("/auth/microsoft/callback")
def get_token(code: str, token_store: TokenStoreDep):
    """Complete the OAuth flow by exchanging the login code for a refresh token.

    Microsoft calls this endpoint automatically after the user logs in. The
    `code` query parameter is a one-time value that we exchange for tokens
    by calling `handle_callback`. After this the app is connected and
    `/email/sync` can be called.

    Args:
        code: The short-lived authorization code from Microsoft (comes from the
            URL query string automatically via FastAPI).
    """
    token_store.handle_callback(code)
    return {"message": "connected"}


@app.get("/email/sync")
def sync_email(token_store: TokenStoreDep, settings: SettingsDep):
    """Fetch new emails, triage them with the LLM, and save drafts for those needing a reply.

    This is the core endpoint of the application. It runs the full pipeline:

    1. Fetch new messages from 'inbox' and 'junkemail' via the Graph API delta endpoint.
    2. For each message, gather context:
       a. Query the MCP memory server for personal knowledge about the sender/topic.
       b. Search OneDrive for documents relevant to the email subject.
    3. Ask the LLM whether a personal reply is needed (using the context).
    4. For those that need a reply, ask the LLM to draft one — informed by the
       same context and the list of relevant documents.
    5. Save the draft to Outlook's Drafts folder via the Graph API.

    The delta link for each folder is updated after each sync so the next call
    only fetches emails that arrived after this one.

    Returns:
        An `EmailSyncResult` with counts of how many emails were checked,
        how many needed a response, and how many drafts were created.

    Raises:
        HTTPException (401): If the user hasn't completed the Microsoft login flow yet.
    """
    if not token_store.is_connected:
        raise HTTPException(status_code=401, detail="Microsoft account not connected")

    bedrock_client = _get_bedrock_client(settings.aws_region)
    email_assessor = EmailAssessor(
        bedrock_client=bedrock_client, model_id=settings.bedrock_model_id
    )
    response_writer = EmailResponseWriter(
        bedrock_client=bedrock_client, model_id=settings.bedrock_model_id
    )
    graph_client = GraphClient(token_store=token_store)
    onedrive_client = OneDriveClient(
        bedrock_client=bedrock_client,
        model_id=settings.bedrock_model_id,
        token_store=token_store,
    )
    token = get_fresh_token() if settings.mcp_memory_url else None

    memory_client = (
        MemoryClient(
            bedrock_client=bedrock_client,
            model_id=settings.bedrock_model_id,
            server_url=settings.mcp_memory_url,
            token=token,
        )
        if settings.mcp_memory_url
        else None
    )

    all_messages = []
    for folder in delta_links:
        messages, delta_link = graph_client.fetch_delta(folder, delta_links[folder], 5)
        all_messages.extend(messages)
        delta_links[folder] = delta_link
    result = EmailSyncResult(
        checked_messages=len(all_messages), needs_response=0, drafts_created=0
    )

    for message in all_messages:
        email = EmailMessage(
            id=message.get("id") or "",
            subject=message.get("subject") or "",
            sender=message.get("from", {}).get("emailAddress", {}).get("address", ""),
            body_preview=message.get("bodyPreview") or "",
        )

        memories = memory_client.search(email)

        assessment = email_assessor.assess_email(email, context=memories)
        if assessment.needs_response:
            result.needs_response += 1
            docs = onedrive_client.search(email)
            draft = response_writer.write_response_draft(
                email, context=memories, relevant_docs=docs
            )
            graph_client.save_draft(draft=draft, recipient=email.sender, files=docs)
            result.drafts_created += 1
            logger.info("Draft saved for %s", email.sender)

    return result


@app.post("/banking/analyse")
def analyse_bank_statement(settings: SettingsDep):
    """Run a full financial analysis using the bank statement stored in memory.

    Fetches the bank statement and supplementary financial context (prior analyses,
    savings goals, spending notes) from the MCP memory server, then asks the LLM
    to categorise transactions, track yearly savings progress, propose next-month
    budgets, and surface savings opportunities.

    Returns:
        The raw JSON analysis object produced by the BankAdviser.

    Raises:
        HTTPException (503): If MCP_MEMORY_URL is not configured, since the bank
            statement can only be sourced from memory.
    """
    if not settings.mcp_memory_url:
        raise HTTPException(
            status_code=503,
            detail="MCP_MEMORY_URL is not configured — bank statement cannot be sourced.",
        )

    bedrock_client = _get_bedrock_client(settings.aws_region)
    token = get_fresh_token()
    memory_client = MemoryClient(
        bedrock_client=bedrock_client,
        model_id=settings.bedrock_model_id,
        server_url=settings.mcp_memory_url,
        token=token,
    )

    statement = memory_client.fetch_bank_statement()
    context = memory_client.search_financial_context()

    bank_adviser = BankAdviser(
        bedrock_client=bedrock_client,
        model_id=settings.bedrock_model_id,
    )

    return bank_adviser.analyse(statement=statement, context=context)
