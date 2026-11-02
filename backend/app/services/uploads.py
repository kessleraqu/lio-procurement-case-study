from fastapi import HTTPException, UploadFile, status

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB, matches the upload UI's stated limit

# The PDF spec lets the header appear anywhere in the first 1024 bytes.
_PDF_SIGNATURE = b"%PDF-"
_SIGNATURE_WINDOW = 1024


async def read_pdf_upload(file: UploadFile) -> bytes:
    """Read an uploaded PDF, validating it by its bytes rather than its MIME type.

    Browsers report an empty or generic content type for some files (e.g. a
    vendor's ``QuoteA0492_23.Pdf``), so the declared type can't be trusted
    either way — the file signature can.
    """
    content = await file.read()
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF exceeds the 10MB limit",
        )
    if _PDF_SIGNATURE not in content[:_SIGNATURE_WINDOW]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Please upload a PDF file"
        )
    return content
