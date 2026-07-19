"""Authentication strategies for Omada controllers (§2.1)."""

import time
from abc import ABC, abstractmethod

import httpx

TOKEN_EXPIRY_BUFFER = 300  # 5 min before expiry


class AuthError(Exception):
    pass


class AuthStrategy(ABC):
    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def ensure_valid_session(self) -> None: ...

    @abstractmethod
    def decorate_request(self, headers: dict[str, str]) -> dict[str, str]: ...

    @abstractmethod
    async def handle_auth_failure(self) -> None: ...

    @abstractmethod
    def set_client(self, client: httpx.AsyncClient) -> None: ...


class ClientCredentialsAuth(AuthStrategy):
    """OAuth2 client_credentials for classic local/cloud controllers."""

    def __init__(self, *, base_url: str, omadac_id: str, client_id: str, client_secret: str):
        self._base_url = base_url.rstrip("/")
        self._omadac_id = omadac_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._client: httpx.AsyncClient | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._token_expires_at: float = 0

    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def authenticate(self) -> None:
        await self._get_fresh_tokens()

    async def ensure_valid_session(self) -> None:
        if time.time() >= self._token_expires_at - TOKEN_EXPIRY_BUFFER:
            await self._refresh_access_token()

    def decorate_request(self, headers: dict[str, str]) -> dict[str, str]:
        headers["Authorization"] = f"AccessToken={self.access_token}"
        return headers

    async def handle_auth_failure(self) -> None:
        await self._refresh_access_token()

    async def _refresh_access_token(self) -> None:
        url = f"{self._base_url}/openapi/authorize/token"
        params = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self.refresh_token,
        }
        resp = await self._client.post(url, params=params)
        if resp.status_code == 401:
            await self._get_fresh_tokens()
            return
        data = resp.json()
        error_code = data.get("errorCode")
        if error_code in (-44114, -44111, -44106):
            await self._get_fresh_tokens()
            return
        if error_code != 0:
            raise AuthError(f"Token refresh failed: {data.get('msg')} (code: {error_code})")
        self._apply_token(data["result"])

    async def _get_fresh_tokens(self) -> None:
        url = f"{self._base_url}/openapi/authorize/token"
        resp = await self._client.post(
            url,
            params={"grant_type": "client_credentials"},
            json={"omadacId": self._omadac_id, "client_id": self._client_id, "client_secret": self._client_secret},
        )
        if resp.status_code != 200:
            raise AuthError(f"Auth failed: HTTP {resp.status_code}")
        data = resp.json()
        if data.get("errorCode") != 0:
            raise AuthError(f"Auth failed: {data.get('msg')}")
        self._apply_token(data["result"])

    def _apply_token(self, token_data: dict) -> None:
        self.access_token = token_data["accessToken"]
        self.refresh_token = token_data["refreshToken"]
        self._token_expires_at = time.time() + token_data["expiresIn"]


class WebSessionAuth(AuthStrategy):
    """Fusion gateway web-session authentication."""

    def __init__(self, *, base_url: str, omadac_id: str, username: str, password: str):
        self._base_url = base_url.rstrip("/")
        self._omadac_id = omadac_id
        self._username = username
        self._password = password
        self._client: httpx.AsyncClient | None = None
        self._csrf_token: str | None = None

    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def authenticate(self) -> None:
        url = f"{self._base_url}/{self._omadac_id}/api/v2/login"
        resp = await self._client.post(url, json={"username": self._username, "password": self._password})
        data = resp.json()
        if data.get("errorCode") != 0:
            raise AuthError(f"Login failed: {data.get('msg')}")
        self._csrf_token = data["result"]["token"]

    async def ensure_valid_session(self) -> None:
        if self._csrf_token is None:
            await self.authenticate()

    def decorate_request(self, headers: dict[str, str]) -> dict[str, str]:
        headers["Csrf-Token"] = self._csrf_token or ""
        headers["Omada-Request-Source"] = "web-local"
        return headers

    async def handle_auth_failure(self) -> None:
        self._csrf_token = None
        await self.authenticate()


async def discover_omadac_id(client: httpx.AsyncClient, base_url: str) -> str:
    """Fusion: auto-discover omadacId via GET /api/info."""
    resp = await client.get(f"{base_url.rstrip('/')}/api/info")
    data = resp.json()
    if data.get("errorCode") != 0:
        raise AuthError(f"Failed to discover omadacId: {data.get('msg')}")
    return data["result"]["omadacId"]
