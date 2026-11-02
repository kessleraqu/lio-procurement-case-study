import io
import logging
import re

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Fewer non-whitespace characters than this across the whole document means
# there is no usable text layer — almost always a scan or photo.
_MIN_TEXT_CHARS = 20

_WIDE_GAP = re.compile(r" {3,}")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


class ScannedPdfError(ValueError):
    """The PDF has no selectable text (scanned or image-only)."""


def _page_text(page) -> str:
    """Text of one page, keeping each visual row on one line.

    Layout mode keeps a price on the same line as its item and a label next to
    its value; plain mode emits text in drawing order, which on some quotes
    detaches amounts from their line items. Wide column gaps are squeezed to
    three spaces so columns stay visibly separate without padding the prompt.
    """
    try:
        text = page.extract_text(extraction_mode="layout")
    except Exception:  # noqa: BLE001 — fall back rather than lose the page
        logger.warning("Layout extraction failed; falling back to plain text", exc_info=True)
        text = page.extract_text()
    lines = (_WIDE_GAP.sub("   ", line).rstrip() for line in (text or "").splitlines())
    return _EXTRA_BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def extract_text(pdf_bytes: bytes) -> str:
    """Extract the text from a vendor-offer PDF, page by page.

    Pages are marked so totals carried over from earlier pages can be told
    apart from the final grand total. Raises ``ScannedPdfError`` when the PDF
    has no text layer.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    pages = [
        f"--- Page {number} of {page_count} ---\n{_page_text(page)}"
        for number, page in enumerate(reader.pages, start=1)
    ]
    text = "\n\n".join(pages)
    if len(re.sub(r"\s|--- Page \d+ of \d+ ---", "", text)) < _MIN_TEXT_CHARS:
        raise ScannedPdfError(
            "This PDF has no selectable text — it looks like a scanned document. "
            "Please fill in the request manually."
        )
    return text
