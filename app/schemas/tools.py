"""
Pydantic Schemas for AI Tools Layer (FastPrint ERP Native Tools).
Provides strict input validation for LLMs and response contract validation for Django Core.
"""
from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T")


def _validate_date_str(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate that string adheres to YYYY-MM-DD date format."""
    if value is None or value == "":
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise ValueError(f"Invalid date format for '{field_name}': '{value}'. Must be YYYY-MM-DD.")


# ==============================================================================
# 1. Input Schemas (LLM Tool Calling Filters)
# ==============================================================================

class AccountBalanceFilterSchema(BaseModel):
    """Input filter schema for accounting.get_account_balance."""
    company_id: Optional[int] = Field(None, description="Company ID (defaults to default company if omitted)")
    account_id: Optional[int] = Field(None, description="Specific chart of account ID")
    account_code: Optional[str] = Field(None, description="Account code string (e.g. '111100')")
    date_from: Optional[str] = Field(None, description="Start date filter in YYYY-MM-DD format")
    date_to: Optional[str] = Field(None, description="End date filter in YYYY-MM-DD format")

    @field_validator("date_from")
    @classmethod
    def validate_date_from(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, "date_from")

    @field_validator("date_to")
    @classmethod
    def validate_date_to(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, "date_to")


class JournalEntriesFilterSchema(BaseModel):
    """Input filter schema for accounting.get_journal_entries."""
    company_id: Optional[int] = Field(None, description="Company ID (defaults to default company)")
    journal_id: Optional[int] = Field(None, description="Filter by specific journal book ID")
    entry_number: Optional[str] = Field(None, description="Exact or partial journal entry number (e.g. 'JE/2026/001')")
    status: Optional[str] = Field(None, description="Status filter: 'draft', 'posted', 'cancel', etc.")
    date_from: Optional[str] = Field(None, description="Filter entries on or after YYYY-MM-DD")
    date_to: Optional[str] = Field(None, description="Filter entries on or before YYYY-MM-DD")
    account_id: Optional[int] = Field(None, description="Filter entries containing items for this account ID")
    search: Optional[str] = Field(None, description="Keyword search matching document number or description")
    limit: int = Field(50, ge=1, le=200, description="Page limit (1-200)")
    offset: int = Field(0, ge=0, description="Pagination offset")

    @field_validator("date_from")
    @classmethod
    def validate_date_from(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, "date_from")

    @field_validator("date_to")
    @classmethod
    def validate_date_to(cls, v: Optional[str]) -> Optional[str]:
        return _validate_date_str(v, "date_to")


class StockLevelFilterSchema(BaseModel):
    """Input filter schema for inventory.get_stock_level."""
    company_id: Optional[int] = Field(None, description="Company ID")
    product_id: Optional[int] = Field(None, description="Product Variant ID (either product_id or code required)")
    code: Optional[str] = Field(None, description="Product SKU code (either product_id or code required)")
    location_id: Optional[int] = Field(None, description="Optional inventory location ID filter")
    limit: int = Field(20, ge=1, le=100, description="Max records to return")
    offset: int = Field(0, ge=0, description="Offset for pagination")

    @model_validator(mode="after")
    def validate_identifier(self) -> "StockLevelFilterSchema":
        if self.product_id is None and not self.code:
            raise ValueError("Either 'product_id' or 'code' (SKU) must be provided for stock check.")
        return self


class ProductInfoFilterSchema(BaseModel):
    """Input filter schema for inventory.get_product_info."""
    company_id: Optional[int] = Field(None, description="Company ID")
    product_id: Optional[int] = Field(None, description="Specific Product ID")
    code: Optional[str] = Field(None, description="Exact product code / SKU")
    search: Optional[str] = Field(None, description="Keyword search matching product code, name, or reference")
    include_variants: bool = Field(True, description="Whether to include variant details")
    limit: int = Field(10, ge=1, le=50, description="Max results (1-50)")


# ==============================================================================
# 2. Output & Response Contract Schemas (Django Core ai_tools/core/response.py)
# ==============================================================================

class ERPErrorDetail(BaseModel):
    """Standard error detail object returned by Django Core."""
    code: str = Field(..., description="Error code identifier")
    message: str = Field(..., description="Human-readable error explanation")
    details: Dict[str, Any] = Field(default_factory=dict, description="Detailed validation/context data")


class ERPResponseEnvelope(BaseModel, Generic[T]):
    """Standard response envelope from FastPrint ERP AI Tools layer."""
    success: bool = Field(..., description="True if execution succeeded, False otherwise")
    data: Optional[T] = Field(None, description="Result payload if success is True")
    error: Optional[ERPErrorDetail] = Field(None, description="Error details if success is False")


# ------------------------------------------------------------------------------
# 2.1 Accounting Tool Data Models
# ------------------------------------------------------------------------------

class AccountBalanceItem(BaseModel):
    account_id: int
    account_code: str
    account_name: Optional[str] = None
    total_debit: float = 0.0
    total_credit: float = 0.0
    net_balance: float = 0.0
    balance_position: Optional[str] = None


class AccountBalanceDataSchema(BaseModel):
    accounts: List[AccountBalanceItem] = Field(default_factory=list)
    total_debit: float = 0.0
    total_credit: float = 0.0
    net_balance: float = 0.0


class JournalEntryItem(BaseModel):
    id: int
    number: str
    date: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    status: Optional[str] = None
    journal_id: Optional[int] = None
    journal_name: Optional[str] = None
    vendor_id: Optional[int] = None
    vendor_name: Optional[str] = None
    total_debit: float = 0.0
    total_credit: float = 0.0


class JournalEntriesDataSchema(BaseModel):
    total_count: int
    limit: int
    offset: int
    entries: List[JournalEntryItem] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# 2.2 Inventory Tool Data Models
# ------------------------------------------------------------------------------

class StockLevelDataSchema(BaseModel):
    product_id: int
    code: str
    name: str
    company_id: int
    company_name: str
    location_id: Optional[int] = None
    qty_on_hand: float = 0.0


class ProductInfoItem(BaseModel):
    id: int
    code: str
    name: str
    full_name: Optional[str] = None
    internal_reference: Optional[str] = None
    barcode: Optional[str] = None
    last_unit_price: float = 0.0
    can_be_sold: bool = True
    can_be_purchased: bool = True
    is_bundling: bool = False


class ProductInfoDataSchema(BaseModel):
    total_found: int
    products: List[ProductInfoItem] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# Concrete Envelope Types for Documentation & Type Checking
# ------------------------------------------------------------------------------

class AccountBalanceResponseSchema(ERPResponseEnvelope[AccountBalanceDataSchema]):
    pass


class JournalEntriesResponseSchema(ERPResponseEnvelope[JournalEntriesDataSchema]):
    pass


class StockLevelResponseSchema(ERPResponseEnvelope[StockLevelDataSchema]):
    pass


class ProductInfoResponseSchema(ERPResponseEnvelope[ProductInfoDataSchema]):
    pass
