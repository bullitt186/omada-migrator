"""Tests for auth strategies — mirrors §2.1 behavior."""

import time

import httpx
import pytest
import respx

from omada_migrator.auth import (
    AuthStrategy,
    ClientCredentialsAuth,
    WebSessionAuth,
)


@pytest.fixture
def base_url():
    return "https://192.168.1.100:8043"


@pytest.fixture
def omadac_id():
    return "abc123omadacid"


class TestClientCredentialsAuth:
    """FR-1/TD-2: classic local/cloud OAuth2 client_credentials."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_authenticate_success(self, base_url, omadac_id):
        token_url = f"{base_url}/openapi/authorize/token"
        respx.post(token_url).mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {
                    "accessToken": "access123",
                    "refreshToken": "refresh456",
                    "expiresIn": 7200,
                }
            })
        )

        auth = ClientCredentialsAuth(
            base_url=base_url,
            omadac_id=omadac_id,
            client_id="cid",
            client_secret="csecret",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()

        assert auth.access_token == "access123"
        assert auth.refresh_token == "refresh456"

    @respx.mock
    @pytest.mark.asyncio
    async def test_decorate_request_adds_accesstoken_header(self, base_url, omadac_id):
        respx.post(f"{base_url}/openapi/authorize/token").mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {
                    "accessToken": "mytoken",
                    "refreshToken": "ref",
                    "expiresIn": 7200,
                }
            })
        )
        auth = ClientCredentialsAuth(
            base_url=base_url,
            omadac_id=omadac_id,
            client_id="cid",
            client_secret="csecret",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()

        headers = auth.decorate_request({})
        assert headers["Authorization"] == "AccessToken=mytoken"

    @respx.mock
    @pytest.mark.asyncio
    async def test_refresh_on_token_expired(self, base_url, omadac_id):
        """Proactive refresh when token is near expiry."""
        token_url = f"{base_url}/openapi/authorize/token"
        call_count = {"n": 0}

        def handler(request):
            call_count["n"] += 1
            return httpx.Response(200, json={
                "errorCode": 0,
                "result": {
                    "accessToken": f"access_{call_count['n']}",
                    "refreshToken": f"refresh_{call_count['n']}",
                    "expiresIn": 7200,
                }
            })

        respx.post(token_url).mock(side_effect=handler)

        auth = ClientCredentialsAuth(
            base_url=base_url,
            omadac_id=omadac_id,
            client_id="cid",
            client_secret="csecret",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()
            # Force token to look expired
            auth._token_expires_at = time.time() - 10
            await auth.ensure_valid_session()

        assert auth.access_token == "access_2"

    @respx.mock
    @pytest.mark.asyncio
    async def test_refresh_fallback_on_error_codes(self, base_url, omadac_id):
        """§2.1: -44114/-44111/-44106 on refresh → full re-auth."""
        token_url = f"{base_url}/openapi/authorize/token"
        calls = []

        def handler(request):
            calls.append(dict(request.url.params))
            if len(calls) == 1:
                # Initial auth
                return httpx.Response(200, json={
                    "errorCode": 0,
                    "result": {"accessToken": "a1", "refreshToken": "r1", "expiresIn": 7200}
                })
            elif len(calls) == 2:
                # Refresh attempt returns -44114
                return httpx.Response(200, json={
                    "errorCode": -44114,
                    "msg": "token invalid",
                })
            else:
                # Fallback client_credentials
                return httpx.Response(200, json={
                    "errorCode": 0,
                    "result": {"accessToken": "a_fresh", "refreshToken": "r_fresh", "expiresIn": 7200}
                })

        respx.post(token_url).mock(side_effect=handler)

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="csecret",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()
            auth._token_expires_at = time.time() - 10
            await auth.ensure_valid_session()

        assert auth.access_token == "a_fresh"


class TestWebSessionAuth:
    """FR-1/TD-2: Fusion gateway web session auth."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_authenticate_gets_csrf_token(self, base_url, omadac_id):
        login_url = f"{base_url}/{omadac_id}/api/v2/login"
        respx.post(login_url).mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {"token": "csrf_tok_123"}
            })
        )

        auth = WebSessionAuth(
            base_url=base_url,
            omadac_id=omadac_id,
            username="admin",
            password="password123",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()

        assert auth._csrf_token == "csrf_tok_123"

    @respx.mock
    @pytest.mark.asyncio
    async def test_decorate_request_adds_csrf_and_source(self, base_url, omadac_id):
        login_url = f"{base_url}/{omadac_id}/api/v2/login"
        respx.post(login_url).mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {"token": "csrf_abc"}
            })
        )

        auth = WebSessionAuth(
            base_url=base_url, omadac_id=omadac_id,
            username="admin", password="pw",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()

        headers = auth.decorate_request({})
        assert headers["Csrf-Token"] == "csrf_abc"
        assert headers["Omada-Request-Source"] == "web-local"

    @respx.mock
    @pytest.mark.asyncio
    async def test_handle_auth_failure_relogins(self, base_url, omadac_id):
        login_url = f"{base_url}/{omadac_id}/api/v2/login"
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={
                "errorCode": 0,
                "result": {"token": f"csrf_{calls['n']}"}
            })

        respx.post(login_url).mock(side_effect=handler)

        auth = WebSessionAuth(
            base_url=base_url, omadac_id=omadac_id,
            username="admin", password="pw",
        )
        async with httpx.AsyncClient() as client:
            auth.set_client(client)
            await auth.authenticate()
            assert auth._csrf_token == "csrf_1"
            await auth.handle_auth_failure()
            assert auth._csrf_token == "csrf_2"


class TestOmadacIdDiscovery:
    """TD-2: Fusion auto-discovers omadacId via GET /api/info."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_discover_omadac_id(self, base_url):
        respx.get(f"{base_url}/api/info").mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {"omadacId": "discovered_id_789"}
            })
        )

        from omada_migrator.auth import discover_omadac_id

        async with httpx.AsyncClient() as client:
            result = await discover_omadac_id(client, base_url)

        assert result == "discovered_id_789"
