from functools import lru_cache
from urllib.parse import urlencode

import httpx

from assistant.shared.settings import Settings, get_settings

_MS_AUTH_BASE = "https://login.microsoftonline.com"
_MS_SCOPES = "openid profile offline_access https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Files.Read"


class MicrosoftTokenStore:
    """Manages the Microsoft OAuth tokens needed to access the user's mailbox.

    OAuth works in two steps:
    1. The user logs in via their browser and Microsoft gives us a one-time `code`.
    2. We exchange that code for tokens: an `access_token` (valid ~1 hour) and a
       `refresh_token` (long-lived). We store the refresh token and use it to get
       a fresh access token whenever we need to call the Graph API.

    Tokens are kept in memory only — they are lost when the app restarts.
    Replace with a database for a production deployment.
    """

    def __init__(self, settings: Settings):
        """Set up the store with no token yet (the user hasn't logged in).

        Args:
            settings: Application settings that hold the Azure app credentials.
        """
        self._settings = settings
        self._refresh_token: str | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if the user has completed the login flow and we have a token.

        This is checked before trying to call the Graph API — if False, the user
        needs to visit /auth/microsoft/login first.
        """
        return self._refresh_token is not None

    def get_authorize_url(self) -> str:
        """Build the Microsoft login URL to redirect the user to.

        When the user visits this URL they are taken to Microsoft's sign-in page.
        After they approve access, Microsoft redirects them back to our
        `/auth/microsoft/callback` endpoint with a one-time `code` in the URL.

        Returns:
            The full URL string, ready to use as a redirect destination.
        """
        s = self._settings
        params = {
            "client_id": s.ms_client_id,
            "response_type": "code",
            "redirect_uri": s.ms_redirect_uri,
            "response_mode": "query",
            "scope": _MS_SCOPES,
        }
        return (
            f"{_MS_AUTH_BASE}/{s.ms_tenant}/oauth2/v2.0/authorize?{urlencode(params)}"
        )

    def handle_callback(self, code: str) -> None:
        """Exchange the one-time login code for a refresh token and store it.

        This is called once, right after the user completes the Microsoft login.
        The `code` from the URL is sent to Microsoft in exchange for a
        `refresh_token`, which we keep so we can get access tokens later.

        Args:
            code: The short-lived authorization code from the callback URL query string.
        """
        s = self._settings
        response = httpx.post(
            f"{_MS_AUTH_BASE}/{s.ms_tenant}/oauth2/v2.0/token",
            data={
                "client_id": s.ms_client_id,
                "client_secret": s.ms_client_secret,
                "code": code,
                "redirect_uri": s.ms_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        self._refresh_token = response.json()["refresh_token"]

    def get_access_token(self) -> str:
        """Get a fresh, short-lived access token using the stored refresh token.

        Access tokens expire after about an hour. Rather than asking the user to
        log in again, we use the refresh token to silently obtain a new access
        token. If Microsoft rotates the refresh token (returns a new one), we
        update our stored copy automatically.

        Returns:
            A valid access token string to include in Graph API request headers.
        """
        s = self._settings
        response = httpx.post(
            f"{_MS_AUTH_BASE}/{s.ms_tenant}/oauth2/v2.0/token",
            data={
                "client_id": s.ms_client_id,
                "client_secret": s.ms_client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
                "scope": "openid profile offline_access Mail.ReadWrite Files.Read",
            },
        )
        response.raise_for_status()
        token_data = response.json()
        if "refresh_token" in token_data:
            self._refresh_token = token_data["refresh_token"]
        return token_data["access_token"]


@lru_cache
def get_token_store() -> MicrosoftTokenStore:
    """Return the single shared token store instance for the application.

    The `@lru_cache` decorator ensures only one `MicrosoftTokenStore` object is
    ever created. FastAPI's dependency injection calls this function on every
    request, but thanks to the cache it always gets back the same object — which
    is important because that object holds the user's login state in memory.
    """
    return MicrosoftTokenStore(settings=get_settings())
