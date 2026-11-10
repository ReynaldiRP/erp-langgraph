"""
Static typed LangChain tool definitions for FastPrint ERP pilot tools.
Binds centralized Pydantic schemas from app.schemas.tools with ERPClient execution.
"""
from typing import Any, Dict, List, Optional
import warnings
from langchain_core.tools import tool

from app.services.erp_client import ERPClient
from app.schemas.tools import (
    AccountBalanceFilterSchema,
    JournalEntriesFilterSchema,
    StockLevelFilterSchema,
    ProductInfoFilterSchema,
    LocationsFilterSchema,
    StockHistoryFilterSchema,
    TransfersFilterSchema,
    LowStockAlertsFilterSchema,
    StockAdjustmentsFilterSchema,
    ScrapRecordsFilterSchema,
    WarehousesFilterSchema,
    ProductCategoriesFilterSchema,
)


def create_static_tools(client: ERPClient) -> List[Any]:
    """
    Instantiate and return static typed LangChain tools bound to an ERPClient instance.
    """
    warnings.warn(
        "create_static_tools is deprecated.",
        DeprecationWarning,
        stacklevel=2,
    )

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

    # 5. Inventory: Get Locations
    @tool("inventory_get_locations", args_schema=LocationsFilterSchema)
    def get_locations(
        company_id: Optional[int] = None,
        search: Optional[str] = None,
        warehouse_id: Optional[int] = None,
        type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Retrieve storage locations, racks, and warehouse hierarchy."""
        params = {
            "search": search,
            "warehouse_id": warehouse_id,
            "type": type,
            "category": category,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_locations",
            params=clean_params,
            company_id=company_id,
        )

    # 6. Inventory: Get Stock History
    @tool("inventory_get_stock_history", args_schema=StockHistoryFilterSchema)
    def get_stock_history(
        company_id: Optional[int] = None,
        code: Optional[str] = None,
        product_id: Optional[int] = None,
        warehouse_id: Optional[int] = None,
        location_id: Optional[int] = None,
        operation_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source_ref: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Retrieve stock ledger and movement history (StockTransfer ledger)."""
        params = {
            "code": code,
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            "location_id": location_id,
            "operation_type": operation_type,
            "date_from": date_from,
            "date_to": date_to,
            "source_ref": source_ref,
            "limit": limit,
            "offset": offset,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_stock_history",
            params=clean_params,
            company_id=company_id,
        )

    # 7. Inventory: Get Transfers
    @tool("inventory_get_transfers", args_schema=TransfersFilterSchema)
    def get_transfers(
        company_id: Optional[int] = None,
        ref: Optional[str] = None,
        source_document: Optional[str] = None,
        status: Optional[str] = None,
        operation_type: Optional[str] = None,
        contact_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        include_lines: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Track transfer documents, vendor receipts, and delivery orders."""
        params = {
            "ref": ref,
            "source_document": source_document,
            "status": status,
            "operation_type": operation_type,
            "contact_id": contact_id,
            "date_from": date_from,
            "date_to": date_to,
            "include_lines": include_lines,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_transfers",
            params=clean_params,
            company_id=company_id,
        )

    # 8. Inventory: Get Low Stock Alerts
    @tool("inventory_get_low_stock_alerts", args_schema=LowStockAlertsFilterSchema)
    def get_low_stock_alerts(
        company_id: Optional[int] = None,
        warehouse_id: Optional[int] = None,
        category_id: Optional[int] = None,
        threshold: float = 0.0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Detect out-of-stock, negative, or low inventory items."""
        params = {
            "warehouse_id": warehouse_id,
            "category_id": category_id,
            "threshold": threshold,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_low_stock_alerts",
            params=clean_params,
            company_id=company_id,
        )

    # 9. Inventory: Get Stock Adjustments
    @tool("inventory_get_stock_adjustments", args_schema=StockAdjustmentsFilterSchema)
    def get_stock_adjustments(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        location_id: Optional[int] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Retrieve stock opname records and physical count adjustments."""
        params = {
            "product_id": product_id,
            "location_id": location_id,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_stock_adjustments",
            params=clean_params,
            company_id=company_id,
        )

    # 10. Inventory: Get Scrap Records
    @tool("inventory_get_scrap_records", args_schema=ScrapRecordsFilterSchema)
    def get_scrap_records(
        company_id: Optional[int] = None,
        product_id: Optional[int] = None,
        source_location_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Retrieve damaged stock and scrap order history."""
        params = {
            "product_id": product_id,
            "source_location_id": source_location_id,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_scrap_records",
            params=clean_params,
            company_id=company_id,
        )

    # 11. Inventory: Get Warehouses
    @tool("inventory_get_warehouses", args_schema=WarehousesFilterSchema)
    def get_warehouses(
        company_id: Optional[int] = None,
        search: Optional[str] = None,
        include_locations: bool = True,
    ) -> Dict[str, Any]:
        """Retrieve warehouse masters and internal locations."""
        params = {
            "search": search,
            "include_locations": include_locations,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_warehouses",
            params=clean_params,
            company_id=company_id,
        )

    # 12. Inventory: Get Product Categories
    @tool("inventory_get_product_categories", args_schema=ProductCategoriesFilterSchema)
    def get_product_categories(
        company_id: Optional[int] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve product categories, standard UoM, and costing methods."""
        params = {
            "search": search,
        }
        clean_params = {k: v for k, v in params.items() if v is not None}
        return client.execute_tool(
            domain="inventory",
            tool_name="get_product_categories",
            params=clean_params,
            company_id=company_id,
        )

    return [get_account_balance, get_journal_entries, get_stock_level, get_product_info]
