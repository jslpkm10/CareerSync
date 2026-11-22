"""Regression tests for source preview and local poster extraction."""
import pytest

from app.core.errors import AppError
from app.services import poster_service


def test_preview_requires_company(client, world):
    body = {"source_type": "job_posting", "title": "Developer", "text": "Java Spring 개발"}
    assert client.post("/api/jobs/criteria-preview", json=body).status_code == 401
    assert client.post("/api/jobs/criteria-preview", json=body, headers=world["seeker"]).status_code == 403


def test_project_preview_does_not_modify_job(client, world):
    job = world["job"]
    owner = world["recruiter"]
    before = job.description
    body = {
        "source_type": "project_spec", "title": "Java backend developer",
        "text": "프로젝트 수행\n자격 요건\nJava Spring PostgreSQL API 개발",
    }
    response = client.post("/api/jobs/criteria-preview", json=body, headers=owner)
    assert response.status_code == 200, response.json()
    assert sum(x["weight"] for x in response.json()["analysis"]["criteria"]) == 100
    assert "job_id" not in response.json()
    assert job.description == before


def test_poster_is_reviewable_and_not_stored(client, world, monkeypatch):
    monkeypatch.setattr(poster_service, "extract_text", lambda data, mime: "Java / Spring 개발")
    r = client.post("/api/jobs/poster-text", files={"file": ("poster.png", b"fake", "image/png")}, headers=world["recruiter"])
    assert r.status_code == 200, r.json()
    assert r.json() == {"text": "Java / Spring 개발"}


def test_poster_rejects_invalid_bytes():
    with pytest.raises(AppError):
        poster_service.extract_text(b"not an image", "image/png")


def _make_pdf(text: str = "") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_upload_extracts_text_for_company(client, world):
    data = _make_pdf("Frontend developer: React TypeScript JavaScript REST API and Git.")
    r = client.post(
        "/api/jobs/poster-text",
        files={"file": ("job.pdf", data, "application/pdf")},
        headers=world["recruiter"],
    )
    assert r.status_code == 200, r.json()
    assert "React" in r.json()["text"]
    assert "TypeScript" in r.json()["text"]


def test_pdf_upload_requires_company(client, world):
    data = _make_pdf("Backend developer: Java Spring PostgreSQL and REST APIs.")
    files = {"file": ("job.pdf", data, "application/pdf")}
    assert client.post("/api/jobs/poster-text", files=files).status_code == 401
    assert client.post("/api/jobs/poster-text", files=files, headers=world["seeker"]).status_code == 403


def test_scanned_pdf_page_falls_back_to_local_ocr(monkeypatch):
    calls = []
    def fake_ocr(page):
        calls.append(page.number)
        return "OCR recognized: React TypeScript developer"
    monkeypatch.setattr(poster_service, "_ocr_pdf_page", fake_ocr)
    text = poster_service.extract_text(_make_pdf(), "application/pdf")
    assert text == "OCR recognized: React TypeScript developer"
    assert calls == [0]


def test_pdf_rejects_wrong_mime():
    with pytest.raises(AppError) as err:
        poster_service.extract_text(_make_pdf("PDF text from source"), "image/png")
    assert err.value.code == "POSTER_FORMAT"


def test_pdf_rejects_more_than_five_pages():
    import pymupdf

    doc = pymupdf.open()
    for _ in range(6):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    with pytest.raises(AppError) as err:
        poster_service.extract_text(data, "application/pdf")
    assert err.value.code == "SOURCE_PDF_PAGES"


def test_pdf_rejects_unreadable_bytes():
    with pytest.raises(AppError) as err:
        poster_service.extract_text(b"%PDF-not-a-valid-document", "application/pdf")
    assert err.value.code == "SOURCE_PDF_UNREADABLE"


def test_pdf_upload_does_not_allow_more_than_five_megabytes():
    with pytest.raises(AppError) as err:
        poster_service.extract_text(b"%PDF" + b"a" * (poster_service.MAX_UPLOAD_BYTES + 1), "application/pdf")
    assert err.value.code == "POSTER_SIZE"
