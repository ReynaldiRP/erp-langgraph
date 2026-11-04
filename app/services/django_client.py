"""
Asynchronous HTTP Client for FastPrint ERP Django Core (AI Tools Layer).
Provides non-blocking async execution using httpx.AsyncClient with user token forwarding,
structured error handling, and support for FastPrint ERP tools.
"""
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
import httpx

from app.core.config import settings
from app.schemas.tools import (
    AccountBalanceFilterSchema,
    JournalEntriesFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
)

logger = logging.getLogger("django_client")


# ==============================================================================
# Structured Exception Hierarchy
# ==============================================================================

class DjangoCoreClientError(Exception):
    """Base exception for all Django Core HTTP client errors."""
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(status_code={self.status_code}, message='{self.message}')"


class DjangoAuthError(DjangoCoreClientError):
    """Raised when authentication fails or token is expired/invalid (HTTP 401)."""
    def __init__(self, message: str = "Authentication failed or token expired.", status_code: int = 401, details: Any = None):
        super().__init__(message=message, status_code=status_code, details=details)


class DjangoPermissionError(DjangoCoreClientError):
    """Raised when user lacks permission to access tool, company, or resource (HTTP 403)."""
    def __init__(self, message: str = "Permission denied for requested resource or company.", status_code: int = 403, details: Any = None):
        super().__init__(message=message, status_code=status_code, details=details)


class DjangoNotFoundError(DjangoCoreClientError):
    """Raised when the requested endpoint, tool, or resource does not exist (HTTP 404)."""
    def __init__(self, message: str = "Requested endpoint, tool, or resource not found.", status_code: int = 404, details: Any = None):
        super().__init__(message=message, status_code=status_code, details=details)


class DjangoServerError(DjangoCoreClientError):
    """Raised when Django Core backend encounters an internal server error (HTTP 500..599)."""
    def __init__(self, message: str = "Django Core internal server error.", status_code: int = 500, details: Any = None):
        super().__init__(message=message, status_code=status_code, details=details)


# ==============================================================================
# Asynchronous Django Core Client
# ==============================================================================

def _clean_params(params: Any) -> Dict[str, Any]:
    """Helper to convert Pydantic schema or dictionary into cleaned dict without None."""
    if params is None:
        return {}
    if hasattr(params, "model_dump"):
        raw = params.model_dump()
    elif isinstance(params, dict):
        raw = params
    else:
        raw = dict(params)
    return {k: v for k, v in raw.items() if v is not None}


class DjangoCoreClient:
    """
    Asynchronous client for interacting with FastPrint ERP Django Core API via HTTPX.
    Supports user token forwarding, service keys, and structured error handling.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        default_company_id: Optional[int] = None,
        timeout: Optional[int] = None,
        service_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = (base_url or settings.ERP_BASE_URL).rstrip("/")
        self.token = token or settings.ERP_API_TOKEN
        self.username = username or settings.ERP_USERNAME
        self.password = password or settings.ERP_PASSWORD
        self.default_company_id = default_company_id or settings.DEFAULT_COMPANY_ID
        self.timeout = timeout if timeout is not None else settings.REQUEST_TIMEOUT_SEC
        self.service_key = service_key or settings.ERP_API_TOKEN
        self._external_client = client is not None
        self.client = client or httpx.AsyncClient(timeout=self.timeout)

    async def __aenter__(self) -> "DjangoCoreClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close underlying httpx.AsyncClient if managed internally."""
        if not self._external_client and not self.client.is_closed:
            await self.client.aclose()

    def _generate_secret_key(self) -> str:
        """Generate time-based MD5 secret key for /external_api/get_token/."""
        now = datetime.now().strftime("%Y-%m-%d-%H:%M")
        raw_string = f"erp_master_nexus-{now}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    async def login(self) -> str:
        """
        Authenticate against ERP external_api/get_token/ to obtain JWT access token.
        """
        if not self.username or not self.password:
            raise DjangoAuthError(
                "ERP credentials missing. Provide valid user_token, ERP_API_TOKEN, or ERP_USERNAME and ERP_PASSWORD."
            )

        url = f"{self.base_url}/external_api/get_token/"
        headers = {
            "key": self._generate_secret_key(),
            "Content-Type": "application/json",
        }
        payload = {
            "username": self.username,
            "password": self.password,
        }

        try:
            resp = await self.client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise DjangoCoreClientError(f"Network error during login to ERP: {exc}")

        if resp.status_code == 401 or resp.status_code == 403:
            raise DjangoAuthError(
                f"Authentication failed during ERP login (status {resp.status_code}): {resp.text}",
                status_code=resp.status_code,
                details=resp.text,
            )
        elif resp.status_code != 200:
            raise DjangoCoreClientError(
                f"Failed to authenticate with ERP (status {resp.status_code}): {resp.text}",
                status_code=resp.status_code,
                details=resp.text,
            )

        data = resp.json()
        token = data.get("access")
        if not token:
            raise DjangoAuthError(f"No access token returned from ERP login response: {data}")

        self.token = token
        logger.info("Successfully acquired new JWT token from ERP via async login.")
        return token

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        user_token: Optional[str] = None,
        retries: int = 1,
    ) -> Dict[str, Any]:
        """
        Internal async request helper with user token forwarding, service key,
        structured status code error mapping, and envelope unwrapping.
        """
        active_token = user_token or self.token
        if not active_token:
            active_token = await self.login()

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        req_headers = {
            "Authorization": f"Bearer {active_token}",
            "Content-Type": "application/json",
            "X-AI-Source": "LangGraph",
        }
        if self.service_key:
            req_headers["X-Internal-Service-Key"] = self.service_key
        if headers:
            req_headers.update(headers)

        try:
            resp = await self.client.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=req_headers,
            )
        except httpx.RequestError as exc:
            raise DjangoCoreClientError(f"Network error calling Django Core endpoint {endpoint}: {exc}")

        # HTTP Status Error Handling (DoD: 401, 403, 404, 500)
        if resp.status_code == 401:
            if not user_token and retries > 0:
                logger.warning("System JWT token expired (401). Attempting re-login...")
                await self.login()
                return await self._request(
                    method=method,
                    endpoint=endpoint,
                    params=params,
                    json=json,
                    headers=headers,
                    user_token=user_token,
                    retries=retries - 1,
                )
            raise DjangoAuthError(
                f"Unauthorized (401): {resp.text or 'Authentication failed or token expired.'}",
                status_code=401,
                details=resp.text,
            )

        if resp.status_code == 403:
            raise DjangoPermissionError(
                f"Forbidden (403): {resp.text or 'Access forbidden. User lacks permission for this tool or company.'}",
                status_code=403,
                details=resp.text,
            )

        if resp.status_code == 404:
            raise DjangoNotFoundError(
                f"Not Found (404): {resp.text or 'Requested tool, endpoint, or resource not found on Django Core.'}",
                status_code=404,
                details=resp.text,
            )

        if 500 <= resp.status_code < 600:
            raise DjangoServerError(
                f"Internal Server Error ({resp.status_code}): {resp.text or 'Internal server error occurred on Django Core backend.'}",
                status_code=resp.status_code,
                details=resp.text,
            )

        if resp.status_code >= 400:
            raise DjangoCoreClientError(
                f"Django Core request failed with status {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                details=resp.text,
            )

        try:
            res_data = resp.json()
        except Exception:
            raise DjangoCoreClientError(f"Invalid JSON returned from Django Core: {resp.text}")

        # Standard ERP envelope validation: {"success": true, "data": ...}
        if isinstance(res_data, dict) and "success" in res_data:
            if not res_data.get("success", False):
                err_msg = res_data.get("error", "Unknown error from Django Core tools API.")
                raise DjangoCoreClientError(f"Django Core Tool Error: {err_msg}", details=res_data)
            return res_data.get("data", res_data)

        return res_data

    async def discover_tools(self, user_token: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Call async GET /ai/tools/ to fetch dynamic schema registry from ERP.
        """
        data = await self._request("GET", "/ai/tools/", user_token=user_token)
        if isinstance(data, dict) and "tools" in data:
            return data["tools"]
        elif isinstance(data, list):
            return data
        return []

    # Backward compatibility alias
    list_tools = discover_tools

    async def execute_tool(
        self,
        domain: str,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        company_id: Optional[int] = None,
        user_token: Optional[str] = None,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute tool asynchronously via POST /ai/tools/<domain>/<tool_name>/
        """
        endpoint = f"/ai/tools/{domain}/{tool_name}/"
        payload = {
            "company_id": company_id if company_id is not None else self.default_company_id,
            "parameters": _clean_params(params),
        }
        if confirm:
            payload["confirm"] = True

        return await self._request("POST", endpoint, json=payload, user_token=user_token)

    # ==========================================================================
    # Helper Methods for 4 Native FastPrint ERP Tools
    # ==========================================================================

    async def fetch_account_balance(
        self,
        params: Union[AccountBalanceFilterSchema, Dict[str, Any]],
        user_token: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call accounting.get_account_balance tool."""
        return await self.execute_tool(
            domain="accounting",
            tool_name="get_account_balance",
            params=_clean_params(params),
            company_id=company_id,
            user_token=user_token,
        )

    async def fetch_journal_entries(
        self,
        params: Union[JournalEntriesFilterSchema, Dict[str, Any]],
        user_token: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call accounting.get_journal_entries tool."""
        return await self.execute_tool(
            domain="accounting",
            tool_name="get_journal_entries",
            params=_clean_params(params),
            company_id=company_id,
            user_token=user_token,
        )

    async def fetch_stock_level(
        self,
        params: Union[StockLevelFilterSchema, Dict[str, Any]],
        user_token: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call inventory.get_stock_level tool."""
        return await self.execute_tool(
            domain="inventory",
            tool_name="get_stock_level",
            params=_clean_params(params),
            company_id=company_id,
            user_token=user_token,
        )

    async def fetch_product_info(
        self,
        params: Union[ProductInfoFilterSchema, Dict[str, Any]],
        user_token: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Call inventory.get_product_info tool."""
        return await self.execute_tool(
            domain="inventory",
            tool_name="get_product_info",
            params=_clean_params(params),
            company_id=company_id,
            user_token=user_token,
        )

    async def fetch_vendor_invoices(
        self,
        params: Any,
        user_token: Optional[str] = None,
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Compatibility alias matching TASK-3.2 specification.
        In FastPrint ERP, vendor bills & invoices are recorded in Journal Entries.
        Delegates directly to fetch_journal_entries.
        """
        return await self.fetch_journal_entries(
            params=params,
            user_token=user_token,
            company_id=company_id,
        )


# Class alias for seamless naming compatibility
AsyncERPClient = DjangoCoreClient
