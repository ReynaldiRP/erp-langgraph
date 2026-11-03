"""
HTTP REST Client for FastPrint ERP AI Tools Layer.
Handles authentication, token refresh, tool discovery, and tool execution.
"""
import os
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("erp_client")


class ERPClientError(Exception):
    """Base exception for ERP client errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class ERPClient:
    """
    HTTP Client interacting with FastPrint ERP /ai/tools/ API.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        default_company_id: Optional[int] = None,
        timeout: int = 15,
    ):
        self.base_url = (base_url or os.getenv("ERP_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token or os.getenv("ERP_API_TOKEN")
        self.username = username or os.getenv("ERP_USERNAME")
        self.password = password or os.getenv("ERP_PASSWORD")
        self.default_company_id = default_company_id or int(os.getenv("DEFAULT_COMPANY_ID", "1"))
        self.timeout = timeout
        self.session = requests.Session()

    def _generate_secret_key(self) -> str:
        """Generate time-based MD5 secret key for /external_api/get_token/."""
        now = datetime.now().strftime("%Y-%m-%d-%H:%M")
        raw_string = f"erp_master_nexus-{now}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    def login(self) -> str:
        """
        Authenticate against ERP external_api/get_token/ to obtain JWT access token.
        """
        if not self.username or not self.password:
            raise ERPClientError("ERP credentials missing. Provide ERP_API_TOKEN or ERP_USERNAME and ERP_PASSWORD.")

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
            res = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
            if res.status_code != 200:
                raise ERPClientError(f"Login failed ({res.status_code}): {res.text}", status_code=res.status_code)

            data = res.json()
            access_token = data.get("access")
            if not access_token:
                raise ERPClientError("No access token returned by ERP login endpoint.", details=data)

            self.token = access_token
            logger.info("Successfully acquired new JWT access token from ERP.")
            return self.token
        except requests.RequestException as e:
            raise ERPClientError(f"Network error during ERP login: {str(e)}")

    def _get_headers(self) -> Dict[str, str]:
        """Prepare request headers with Bearer token and source identifier."""
        if not self.token:
            self.login()

        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-AI-Source": "LangGraph",
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        Fetch discovery metadata of all registered domains and tools via GET /ai/tools/.
        """
        url = f"{self.base_url}/ai/tools/"
        headers = self._get_headers()

        res = self.session.get(url, headers=headers, timeout=self.timeout)

        # Retry once on 401 Unauthorized in case token expired
        if res.status_code == 401 and self.username and self.password:
            logger.warning("Token expired or unauthorized. Refreshing token...")
            self.login()
            headers = self._get_headers()
            res = self.session.get(url, headers=headers, timeout=self.timeout)

        if res.status_code != 200:
            raise ERPClientError(f"Failed to list tools ({res.status_code}): {res.text}", status_code=res.status_code)

        response_json = res.json()
        if not response_json.get("success"):
            raise ERPClientError(f"ERP error: {response_json.get('error', {}).get('message')}")

        return response_json.get("data", {}).get("tools", [])

    def execute_tool(
        self,
        domain: str,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        company_id: Optional[int] = None,
        confirm: bool = False,
    ) -> Any:
        """
        Execute domain tool via POST /ai/tools/<domain>/<tool_name>/.
        """
        target_company_id = company_id if company_id is not None else self.default_company_id
        url = f"{self.base_url}/ai/tools/{domain}/{tool_name}/"
        payload = {
            "company_id": target_company_id,
            "params": params or {},
            "confirm": confirm,
        }

        headers = self._get_headers()
        res = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)

        # Retry once on 401 Unauthorized
        if res.status_code == 401 and self.username and self.password:
            logger.warning("Token expired during tool execution. Re-authenticating...")
            self.login()
            headers = self._get_headers()
            res = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)

        try:
            body = res.json()
        except Exception:
            body = {"raw": res.text}

        if res.status_code != 200:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            msg = err.get("message") or f"Tool execution failed with status {res.status_code}."
            raise ERPClientError(msg, status_code=res.status_code, details=err.get("details"))

        if not body.get("success"):
            err = body.get("error", {})
            raise ERPClientError(err.get("message", "Unknown ERP error"), details=err.get("details"))

        return body.get("data")
