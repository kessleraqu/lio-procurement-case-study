from pydantic import BaseModel, ConfigDict, Field


class PdfParseResponse(BaseModel):
    text: str


class ExtractionRequest(BaseModel):
    text: str


# camelCase on the wire — matches the AI's JSON output format and what the
# frontend's extraction flow already consumes.
class OrderLineData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position_description: str = Field(default="", alias="positionDescription")
    unit_price: float = Field(default=0, alias="unitPrice")
    amount: float = 0
    unit: str = ""
    total_price: float = Field(default=0, alias="totalPrice")
    # Discount (negative) or surcharge (positive) printed on the line, so the
    # form can keep totalPrice = unitPrice × amount × (1 + percent/100) on edits.
    price_adjustment_percent: float | None = Field(default=None, alias="priceAdjustmentPercent")


class ExtractedVendorData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # None = not found in the document (reported via ``missingFields``).
    title: str | None = None
    vendor_name: str | None = Field(default=None, alias="vendorName")
    vat_id: str | None = Field(default=None, alias="vatId")
    department: str | None = None
    order_lines: list[OrderLineData] = Field(default_factory=list, alias="orderLines")
    # The quote's grand total, as printed.
    total_cost: float | None = Field(default=None, alias="totalCost")
    # Shipping, fees and tax outside the order lines: lines + this = totalCost
    # when the lines were read correctly.
    additional_charges: float = Field(default=0, alias="additionalCharges")
    # Options the vendor offers instead of ordered lines; not part of totalCost.
    alternatives: list[OrderLineData] = Field(default_factory=list)
    commodity_group_id: int | None = Field(default=None, alias="commodityGroupId")
    commodity_group_name: str | None = Field(default=None, alias="commodityGroupName")


class ExtractionWarning(BaseModel):
    code: str
    message: str


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: ExtractedVendorData | None = None
    error: str | None = None
    missing_fields: list[str] | None = Field(default=None, alias="missingFields")
    # Inconsistencies that survived the retry (e.g. totals that don't add up),
    # for the user to double-check before submitting.
    warnings: list[ExtractionWarning] | None = None
