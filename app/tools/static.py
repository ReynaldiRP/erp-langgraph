"""
Static typed LangChain tool definitions for FastPrint ERP pilot tools.
Provides full IDE autocomplete and Pydantic validation.
"""
from typing import Any, Dict, List, Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.erp_client import ERPClient


def create_static_tools(client: ERPClient) -> List[Any]:
    """
    Instantiate and return static typed LangChain tools bound to an ERPClient instance.
    """

    # 1. Accounting: Get Account Balance
    class AccountBalanceInput(BaseModel):
        company_id: Optional[int] = Field(None, description="Company ID (defaults to default company if omitted)")
        account_id: Optional[int] = Field(None, description="Specific chart of account ID")
        account_code: Optional[str] = Field(None, description="Account code string (e.g. '111100')")
        date_from: Optional[str] = Field(None, description="Start date filter in YYYY-MM-DD format")
        date_to: Optional[str] = Field(None, description="End date filter in YYYY-MM-DD format")

    @tool("accounting_get_account_balance", args_schema=AccountBalanceInput)
    def get_account_balance(
        company_id: Optional[int] = None,
        account_id: Optional[int] = None,
        account_code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate total debit, total credit, and net balance for chart of accounts scoped to company.
        Optionally bounded by date_from and date_to.
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
    class JournalEntriesInput(BaseModel):
        company_id: Optional[int] = Field(None, description="Company ID (defaults to default company)")
        journal_id: Optional[int] = Field(None, description="Filter by specific journal book ID")
        entry_number: Optional[str] = Field(None, description="Exact or partial journal entry number (e.g. 'JE/2026/001')")
        state: Optional[str] = Field(None, description="Entry state: 'draft', 'posted', or 'cancelled'")
        date_from: Optional[str] = Field(None, description="Filter entries on or after YYYY-MM-DD")
        date_to: Optional[str] = Field(None, description="Filter entries on or before YYYY-MM-DD")
        account_id: Optional[int] = Field(None, description="Filter entries containing items for this account ID")
        limit: Optional[int] = Field(20, description="Page limit (max 100)")
        offset: Optional[int] = Field(0, description="Pagination offset")

    @tool("accounting_get_journal_entries", args_schema=JournalEntriesInput)
    def get_journal_entries(
        company_id: Optional[int] = None,
        journal_id: Optional[int] = None,
        entry_number: Optional[str] = None,
        state: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        account_id: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Retrieve journal entries (general ledger transactions) and their debit/credit line items.
        """
        params = {
            "journal_id": journal_id,
            "entry_number": entry_number,
            "state": state,
            "date_from": date_from,
            "date_to": date_to,
            "account_id": account_id,
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
    class StockLevelInput(BaseModel):
        company_id: Optional[int] = Field(None, description="Company ID")
        product_id: Optional[int] = Field(None, description="Product ID or Product Variant ID")
        warehouse_id: Optional[int] = Field(None, description="Filter stock in specific warehouse ID")
        sku: Optional[str] = Field(None, description="Product SKU code")
        barcode: Optional[str] = Field(None, description="Product barcode")
        limit: Optional[int] = Field(20, description="Max records to return (max 100)")
        offset: Optional[int] = Field(0, description="Offset for pagination")

    @tool("inventory_get_stock_level", args_schema=StockLevelInput)
    def get_stock_level(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        warehouse_id: Optional[int] = None,
        sku: Optional[str] = None,
        barcode: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Check current physical quantity and available stock levels for products across warehouses.
        """
        params = {
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            "sku": sku,
            "barcode": barcode,
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
    class ProductInfoInput(BaseModel):
        company_id: Optional[int] = Field(None, description="Company ID")
        product_id: Optional[int] = Field(None, description="Specific Product ID")
        query: Optional[str] = Field(None, description="Search query string matching product name or description")
        barcode: Optional[str] = Field(None, description="Product barcode")
        sku: Optional[str] = Field(None, description="Product SKU code")
        include_variants: Optional[bool] = Field(True, description="Whether to include variant details")
        limit: Optional[int] = Field(20, description="Max results (max 50)")

    @tool("inventory_get_product_info", args_schema=ProductInfoInput)
    def get_product_info(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        query: Optional[str] = None,
        barcode: Optional[str] = None,
        sku: Optional[str] = None,
        include_variants: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Retrieve product master information, pricing, variants, and general attributes.
        """
        params = {
            "product_id": product_id,
            "query": query,
            "barcode": barcode,
            "sku": sku,
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
