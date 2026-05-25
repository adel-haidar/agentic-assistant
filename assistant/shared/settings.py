from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration values for the application.

    Pydantic reads these automatically from environment variables (or a .env file).
    The variable names map directly to the field names in uppercase, e.g.
    the field `ms_client_id` is read from the environment variable `MS_CLIENT_ID`.

    Fields without a default value are required — the app will refuse to start
    if they are missing.
    """

    ms_client_id: str
    """The client ID of your Microsoft Entra (Azure AD) app registration."""

    ms_client_secret: str
    """The client secret of your Microsoft Entra app registration."""

    ms_redirect_uri: str = "http://localhost:8000/auth/microsoft/callback"
    """The URL Microsoft redirects to after the user logs in. Must match what is
    registered in the Azure portal."""

    ms_tenant: str = "consumers"
    """The Microsoft tenant to authenticate against. 'consumers' covers personal
    Microsoft/Outlook accounts."""

    aws_region: str = "eu-central-1"
    """The AWS region where the Bedrock model is available."""

    bedrock_model_id: str = "eu.amazon.nova-2-lite-v1:0"
    """The Amazon Bedrock model ID used for email triage and drafting."""

    user_name: str = "Adel"
    """The name of the user the assistant is working for. Used in LLM prompts."""

    mcp_memory_url: str | None = None
    """The SSE endpoint of the MCP memory server, e.g. 'http://ec2-ip:3000/sse'.
    When set, the agent queries this server for personal context before each
    email assessment. Leave unset to disable memory lookups."""


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, reading from environment variables once and caching the result.

    The `@lru_cache` decorator means this function only does the actual work on
    the first call. Every subsequent call returns the same cached object instantly,
    so environment variables are only read once at startup.
    """
    return Settings()
