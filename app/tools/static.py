"""
Static typed LangChain tool definitions for FastPrint ERP pilot tools.
Binds centralized Pydantic schemas from app.schemas.tools with ERPClient execution.
"""
from typing import Any, Dict, List, Optional
from langchain_core.tools import tool

from app.services.erp_client import ERPClient
from app.schemas.tools import (
    AccountBalanceFilterSchema,
    JournalEntriesFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
)


def create_static_tools(client: ERPClient) -> List[Any]:
    """
    Instantiate and return static typed LangChain tools bound to an ERPClient instance.
    """

    # 1. Accounting: Get Account Balance
    @tool("accounting_get_account_balance", args_schema=AccountBalanceFilterSchema)
    def get_account_balance(
        company_id: Optional[int] = None,
        account_id: Optional[int] = None,
        account_code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate total debit, total credit, and net balance for chart of accounts scoped to company.
        Optionally bounded by date_from and date_to (YYYY-MM-DD).
        """
        params = {
            "account_id": account_id,
            "account_code": account_code,
            "date_from": date_from,
            "date_to": date_to,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="accounting",
            tool_name="get_account_balance",
            params=clean_params,
            company_id=company_id,
        )

    # 2. Accounting: Get Journal Entries
    @tool("accounting_get_journal_entries", args_schema=JournalEntriesFilterSchema)
    def get_journal_entries(
        company_id: Optional[int] = None,
        journal_id: Optional[int] = None,
        entry_number: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_id: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Retrieve journal entries (general ledger transactions) and their debit/credit summary.
        Filterable by date range, journal ID, status, or search keywords.
        """
        params = {
            "journal_id": journal_id,
            "entry_number": entry_number,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "account_id": account_id,
            "search": search,
            "limit": limit,
            "offset": offset,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="accounting",
            tool_name="get_journal_entries",
            params=clean_params,
            company_id=company_id,
        )

    # 3. Inventory: Get Stock Level
    @tool("inventory_get_stock_level", args_schema=StockLevelFilterSchema)
    def get_stock_level(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        code: Optional[str] = None,
        location_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Retrieve current on-hand stock level for a product variant within company and optional location.
        Requires either product_id or code (SKU).
        """
        params = {
            "product_id": product_id,
            "code": code,
            "location_id": location_id,
            "limit": limit,
            "offset": offset,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_stock_level",
            params=clean_params,
            company_id=company_id,
        )

    # 4. Inventory: Get Product Info
    @tool("inventory_get_product_info", args_schema=ProductInfoFilterSchema)
    def get_product_info(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        code: Optional[str] = None,
        search: Optional[str] = None,
        include_variants: bool = True,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Retrieve product variant information such as SKU, full name, unit price,
        and sale/purchase capabilities by product ID, SKU code, or keyword search.
        """
        params = {
            "product_id": product_id,
            "code": code,
            "search": search,
            "include_variants": include_variants,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_product_info",
            params=clean_params,
            company_id=company_id,
        )

    return [get_account_balance, get_journal_entries, get_stock_level, get_product_info]
