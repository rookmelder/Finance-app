"""PDF -> text tokens for the roster parser.

The only module that imports pypdf (installed from requirements.txt). Returns every text fragment of the
first page as (x, y, text) in PDF coordinates (y grows upward), which roster.parse_roster understands.
"""
import io

MAX_PAGES = 3


class PdfError(Exception):
    pass


def extract_tokens(pdf_bytes):
    """All text fragments of page 1 as [(x, y, text)]. Raises PdfError when the file cannot be read."""
    try:
        import pypdf
    except ImportError as err:
        raise PdfError("The pypdf package is not installed on the server") from err
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not bytes(pdf_bytes[:5]) == b"%PDF-":
        raise PdfError("Not a PDF file")
    tokens = []

    def visit(text, cm, tm, font_dict, font_size):
        text = (text or "").strip()
        if text:
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            tokens.append((round(x, 1), round(y, 1), text))

    try:
        reader = pypdf.PdfReader(io.BytesIO(bytes(pdf_bytes)))
        if reader.is_encrypted:
            raise PdfError("The PDF is password protected")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise PdfError("Unexpected number of pages in the PDF")
        reader.pages[0].extract_text(visitor_text=visit)
    except PdfError:
        raise
    except Exception as err:  # pypdf raises many kinds of errors on damaged files
        raise PdfError("Could not read the PDF (" + type(err).__name__ + ")") from err
    return tokens
