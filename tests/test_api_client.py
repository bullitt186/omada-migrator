"""Tests for the generic Omada API client (TD-2, FR-3)."""

import httpx
import pytest
import respx

from omada_migrator.api_client import OmadaClient, OmadaApiError
from omada_migrator.auth import ClientCredentialsAuth


@pytest.fixture
def base_url():
    return "https://192.168.1.100:8043"


@pytest.fixture
def omadac_id():
    return "omadac123"


@pytest.fixture
def site_id():
    return "site456"


def mock_auth_success(base_url):
    """Set up mock for initial auth."""
    respx.post(f"{base_url}/openapi/authorize/token").mock(
        return_value=httpx.Response(200, json={
            "errorCode": 0,
            "result": {"accessToken": "tok", "refreshToken": "ref", "expiresIn": 7200}
        })
    )


class TestOmadaClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_get_sites(self, base_url, omadac_id):
        mock_auth_success(base_url)
        respx.get(f"{base_url}/openapi/v1/{omadac_id}/sites").mock(
            return_value=httpx.Response(200, json={
                "errorCode": 0,
                "result": {"data": [{"siteId": "s1", "name": "Office"}], "totalRows": 1}
            })
        )

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="cs",
        )
        async with httpx.AsyncClient() as http:
            auth.set_client(http)
            await auth.authenticate()
            client = OmadaClient(http_client=http, auth=auth, base_url=base_url, omadac_id=omadac_id)
            sites = await client.get_sites()

        assert sites == [{"siteId": "s1", "name": "Office"}]

    @respx.mock
    @pytest.mark.asyncio
    async def test_paginated_get_all_pages(self, base_url, omadac_id, site_id):
        mock_auth_success(base_url)
        url = f"{base_url}/openapi/v1/{omadac_id}/sites/{site_id}/setting/lan/networks"
        call_n = {"n": 0}

        def handler(request):
            call_n["n"] += 1
            page = int(request.url.params.get("page", 1))
            if page == 1:
                return httpx.Response(200, json={
                    "errorCode": 0,
                    "result": {"data": [{"id": "1"}, {"id": "2"}], "totalRows": 3}
                })
            else:
                return httpx.Response(200, json={
                    "errorCode": 0,
                    "result": {"data": [{"id": "3"}], "totalRows": 3}
                })

        respx.get(url).mock(side_effect=handler)

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="cs",
        )
        async with httpx.AsyncClient() as http:
            auth.set_client(http)
            await auth.authenticate()
            client = OmadaClient(http_client=http, auth=auth, base_url=base_url, omadac_id=omadac_id)
            items = await client.get_paginated(url, page_size=2)

        assert len(items) == 3
        assert items[2]["id"] == "3"

    @respx.mock
    @pytest.mark.asyncio
    async def test_retry_on_401(self, base_url, omadac_id):
        mock_auth_success(base_url)
        url = f"{base_url}/openapi/v1/{omadac_id}/sites"
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(401)
            return httpx.Response(200, json={
                "errorCode": 0,
                "result": {"data": [], "totalRows": 0}
            })

        respx.get(url).mock(side_effect=handler)

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="cs",
        )
        async with httpx.AsyncClient() as http:
            auth.set_client(http)
            await auth.authenticate()
            client = OmadaClient(http_client=http, auth=auth, base_url=base_url, omadac_id=omadac_id)
            sites = await client.get_sites()

        assert calls["n"] == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises_with_unsupported_flag(self, base_url, omadac_id):
        mock_auth_success(base_url)
        url = f"{base_url}/openapi/v1/{omadac_id}/sites/s1/something"
        respx.get(url).mock(return_value=httpx.Response(404, text="Not Found"))

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="cs",
        )
        async with httpx.AsyncClient() as http:
            auth.set_client(http)
            await auth.authenticate()
            client = OmadaClient(http_client=http, auth=auth, base_url=base_url, omadac_id=omadac_id)
            with pytest.raises(OmadaApiError) as exc_info:
                await client.request("GET", url)

        assert exc_info.value.is_unsupported

    @respx.mock
    @pytest.mark.asyncio
    async def test_error_code_minus_1600_is_unsupported(self, base_url, omadac_id):
        mock_auth_success(base_url)
        url = f"{base_url}/openapi/v1/{omadac_id}/sites/s1/something"
        respx.get(url).mock(return_value=httpx.Response(200, json={
            "errorCode": -1600, "msg": "not supported"
        }))

        auth = ClientCredentialsAuth(
            base_url=base_url, omadac_id=omadac_id,
            client_id="cid", client_secret="cs",
        )
        async with httpx.AsyncClient() as http:
            auth.set_client(http)
            await auth.authenticate()
            client = OmadaClient(http_client=http, auth=auth, base_url=base_url, omadac_id=omadac_id)
            with pytest.raises(OmadaApiError) as exc_info:
                await client.request("GET", url)

        assert exc_info.value.is_unsupported
