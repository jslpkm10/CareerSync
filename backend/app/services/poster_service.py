"""Extract editable text from hiring posters and PDF job documents without storing uploads."""
import math
import os
import subprocess
import tempfile

import pymupdf

from app.core.errors import AppError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = MAX_UPLOAD_BYTES  # Compatibility with the original poster endpoint.
MAX_OCR_TEXT = 10_000
MAX_PDF_PAGES = 5
MIN_PDF_PAGE_TEXT = 40
MAX_RENDER_PIXELS = 3_000_000


def _ocr_image(data: bytes, suffix: str) -> str:
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            path = tmp.name
            tmp.write(data)
        try:
            result = subprocess.run(
                ["tesseract", path, "stdout", "-l", "kor+eng", "--psm", "3"],
                capture_output=True, text=True, timeout=25, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppError(503, "POSTER_OCR_UNAVAILABLE", "이미지 글자 인식 서비스를 사용할 수 없습니다.") from exc
        if result.returncode:
            raise AppError(422, "POSTER_OCR_FAILED", "이미지에서 글자를 추출하지 못했습니다.")
        return result.stdout.strip()
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _ocr_pdf_page(page: pymupdf.Page) -> str:
    """OCR one image-only PDF page locally; no image or source file is persisted."""
    area = max(page.rect.width * page.rect.height, 1)
    scale = min(2.0, math.sqrt(MAX_RENDER_PIXELS / area))
    if scale < 0.5:
        raise AppError(422, "SOURCE_PDF_PAGE_SIZE", "PDF 페이지 크기가 너무 큽니다.")
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "kor+eng", "--psm", "3"],
            input=pixmap.tobytes("png"), capture_output=True, timeout=25, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AppError(503, "POSTER_OCR_UNAVAILABLE", "PDF 글자 인식 서비스를 사용할 수 없습니다.") from exc
    if result.returncode:
        raise AppError(422, "SOURCE_PDF_OCR_FAILED", "PDF에서 글자를 인식하지 못했습니다.")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _extract_pdf(data: bytes) -> str:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF uses several exception types for damaged PDFs.
        raise AppError(422, "SOURCE_PDF_UNREADABLE", "PDF 파일을 읽을 수 없습니다.") from exc

    with doc:
        if doc.needs_pass:
            raise AppError(422, "SOURCE_PDF_ENCRYPTED", "암호가 걸린 PDF는 업로드할 수 없습니다.")
        if doc.page_count == 0 or doc.page_count > MAX_PDF_PAGES:
            raise AppError(422, "SOURCE_PDF_PAGES", "PDF는 1~5페이지여야 합니다.")
        parts = []
        for page in doc:
            try:
                text = page.get_text("text", sort=True).strip()
                if len(text) < MIN_PDF_PAGE_TEXT:
                    recognized = _ocr_pdf_page(page)
                    if recognized:
                        text = recognized
            except AppError:
                raise
            except Exception as exc:
                raise AppError(422, "SOURCE_PDF_UNREADABLE", "PDF 파일을 읽을 수 없습니다.") from exc
            parts.append(text)
    return "\n".join(part for part in parts if part)


def extract_text(data: bytes, mime: str | None) -> str:
    """Sniff file signatures (not extensions) and extract text, without saving the source."""
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise AppError(422, "POSTER_SIZE", "PNG·JPEG·PDF 파일은 5MB 이하로 업로드해 주세요.")

    if data.startswith(b"%PDF"):
        if mime not in (None, "application/pdf", "application/octet-stream"):
            raise AppError(422, "POSTER_FORMAT", "PDF 파일의 형식이 일치하지 않습니다.")
        text = _extract_pdf(data)
    else:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix, expected = ".png", "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            suffix, expected = ".jpg", "image/jpeg"
        else:
            raise AppError(422, "POSTER_FORMAT", "PNG·JPEG 이미지 또는 PDF 파일만 업로드해 주세요.")
        if mime not in (None, expected, "application/octet-stream"):
            raise AppError(422, "POSTER_FORMAT", "이미지 파일의 형식이 일치하지 않습니다.")
        text = _ocr_image(data, suffix)

    # Match the editable text field limit, and reject empty or excessively long inputs.
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise AppError(422, "POSTER_OCR_EMPTY", "추출한 글자가 없습니다. 직접 입력해 주세요.")
    if len(text) > MAX_OCR_TEXT:
        raise AppError(422, "POSTER_OCR_TOO_LONG", "추출한 텍스트가 너무 깁니다. 파일을 나누거나 직접 입력해 주세요.")
    return text
