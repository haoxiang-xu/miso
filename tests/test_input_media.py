import base64

from unchain.input import media


def test_from_file_adds_filename_for_pdf_sources(tmp_path):
    pdf_bytes = b"%PDF-1.4\npdf-bytes\n"
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(pdf_bytes)

    block = media.from_file(pdf_path)

    assert block == {
        "type": "pdf",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf_bytes).decode("ascii"),
            "filename": "report.pdf",
        },
    }


def test_from_url_returns_pdf_block_for_pdf_urls():
    block = media.from_url("https://example.com/report.pdf?download=1")

    assert block == {
        "type": "pdf",
        "source": {
            "type": "url",
            "url": "https://example.com/report.pdf?download=1",
            "media_type": "application/pdf",
        },
    }


def test_from_url_returns_pdf_block_for_pdf_media_type_override():
    block = media.from_url("https://example.com/download", media_type="application/pdf")

    assert block == {
        "type": "pdf",
        "source": {
            "type": "url",
            "url": "https://example.com/download",
            "media_type": "application/pdf",
        },
    }
