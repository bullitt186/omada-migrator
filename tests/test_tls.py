"""Tests for TLS handling (FR-11)."""

import httpx
import pytest
from unittest.mock import patch, AsyncMock

from omada_migrator.app import get_connection, config_store


class TestTLSVerification:
    @pytest.mark.asyncio
    async def test_insecure_tls_false_means_verify_on(self):
        """FR-11: TLS verification on by default."""
        config_store.profiles = [{
            "name": "Secure",
            "type": "local",
            "url": "https://10.0.0.1:8043",
            "insecure_tls": False,
            "omadac_id": "oid",
            "client_id": "cid",
            "client_secret": "sec",
        }]

        from omada_migrator.app import _connections
        _connections.clear()

        with patch("omada_migrator.app.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=httpx.Response(200, json={
                "errorCode": 0, "result": {"accessToken": "x", "refreshToken": "y", "expiresIn": 7200}
            }))
            MockClient.return_value = mock_instance

            await get_connection("Secure")
            MockClient.assert_called_once_with(verify=True, timeout=30.0)

        _connections.clear()

    @pytest.mark.asyncio
    async def test_insecure_tls_true_means_verify_off(self):
        """FR-11: Self-signed flag disables verification."""
        config_store.profiles = [{
            "name": "Insecure",
            "type": "local",
            "url": "https://10.0.0.1:8043",
            "insecure_tls": True,
            "omadac_id": "oid",
            "client_id": "cid",
            "client_secret": "sec",
        }]

        from omada_migrator.app import _connections
        _connections.clear()

        with patch("omada_migrator.app.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = AsyncMock(return_value=httpx.Response(200, json={
                "errorCode": 0, "result": {"accessToken": "x", "refreshToken": "y", "expiresIn": 7200}
            }))
            MockClient.return_value = mock_instance

            await get_connection("Insecure")
            MockClient.assert_called_once_with(verify=False, timeout=30.0)

        _connections.clear()
