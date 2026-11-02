from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.core.deps import get_current_user
from app.schemas.extraction import PdfParseResponse
from app.services.pdf import ScannedPdfError, extract_text
from app.services.uploads import read_pdf_upload

router = APIRouter(prefix="/pdf", tags=["pdf"])


@router.post("/parse", response_model=PdfParseResponse, dependencies=[Depends(get_current_user)])
async def parse_pdf(file: UploadFile) -> PdfParseResponse:
    pdf_bytes = await read_pdf_upload(file)

    try:
        text = extract_text(pdf_bytes)
    except ScannedPdfError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except Exception:  # noqa: BLE001 — mirror the original catch-all
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse PDF",
        )

    return PdfParseResponse(text=text)
