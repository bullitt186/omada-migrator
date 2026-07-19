"""Generic Omada API client with auth retry and pagination (TD-2, FR-3)."""

from typing import Any

import httpx

from .auth import AuthStrategy

# ponytail: error codes that mean "this feature/endpoint doesn't apply here"
UNSUPPORTED_ERROR_CODES = (-1600, -44119, -35500, -35400, -35611, -35623)


class OmadaApiError(Exception):
    def __init__(self, message: str, *, error_code: int | None = None, is_unsupported: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.is_unsupported = is_unsupported


class OmadaClient:
    def __init__(self, *, http_client: httpx.AsyncClient, auth: AuthStrategy, base_url: str, omadac_id: str):
        self._http = http_client
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._omadac_id = omadac_id

    async def request(self, method: str, url: str, *, json: dict | None = None, params: dict | None = None) -> dict[str, Any]:
        await self._auth.ensure_valid_session()

        for attempt in range(2):
            headers: dict[str, str] = {"Content-Type": "application/json"}
            self._auth.decorate_request(headers)

            resp = await self._http.request(method, url, headers=headers, json=json, params=params)

            if resp.status_code == 401:
                if attempt == 0:
                    await self._auth.handle_auth_failure()
                    continue
                raise OmadaApiError(f"HTTP 401 after retry")

            if resp.status_code in (404, 405):
                raise OmadaApiError(f"Endpoint not supported ({resp.status_code})", is_unsupported=True)

            if resp.status_code != 200:
                raise OmadaApiError(f"HTTP {resp.status_code}: {resp.text}", error_code=resp.status_code)

            data = resp.json()
            error_code = data.get("errorCode")

            if error_code in (-44112, -44113):
                if attempt == 0:
                    await self._auth.handle_auth_failure()
                    continue
                raise OmadaApiError(f"Token error persists: {data.get('msg')}", error_code=error_code)

            if error_code in UNSUPPORTED_ERROR_CODES:
                raise OmadaApiError(f"Unsupported: {data.get('msg')}", error_code=error_code, is_unsupported=True)

            if error_code != 0:
                raise OmadaApiError(f"API error {error_code}: {data.get('msg')}", error_code=error_code)

            return data

        raise OmadaApiError("Request failed after retries")

    async def get_sites(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/openapi/v1/{self._omadac_id}/sites"
        data = await self.request("GET", url, params={"pageSize": 100, "page": 1})
        return data["result"]["data"]

    async def get_paginated(self, url: str, page_size: int = 100, extra_params: dict | None = None) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated list endpoint.

        Handles both formats:
        - Paginated: {result: {data: [...], totalRows: N}}
        - Plain list: {result: [...]}
        """
        all_items: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {"pageSize": page_size, "page": page}
            if extra_params:
                params.update(extra_params)
            data = await self.request("GET", url, params=params)
            result = data["result"]

            # Plain list response (not paginated)
            if isinstance(result, list):
                return result

            items = result.get("data", [])
            total = result.get("totalRows", len(items))
            all_items.extend(items)
            if len(all_items) >= total or len(items) < page_size:
                break
            page += 1
        return all_items

    async def get_singleton(self, url: str, extra_params: dict | None = None) -> dict[str, Any] | None:
        """Fetch a singleton resource (non-paginated)."""
        data = await self.request("GET", url, params=extra_params)
        return data.get("result")

    async def create(self, url: str, payload: dict) -> dict[str, Any]:
        data = await self.request("POST", url, json=payload)
        return data.get("result", {})

    async def update(self, url: str, payload: dict) -> dict[str, Any]:
        data = await self.request("PUT", url, json=payload)
        return data.get("result", {})

    async def patch(self, url: str, payload: dict) -> dict[str, Any]:
        data = await self.request("PATCH", url, json=payload)
        return data.get("result", {})

    async def delete(self, url: str) -> dict[str, Any]:
        data = await self.request("DELETE", url)
        return data.get("result", {})
