"""Multi-format document loader — txt, pdf, docx, xlsx, csv, image (OCR)."""
from __future__ import annotations
from pathlib import Path
from typing import Any


def load_document(file_path: str) -> list[dict[str, Any]]:
    """Return list of {"text": str, "metadata": dict} dicts from any supported file."""
    path = Path(file_path)
    ext  = path.suffix.lower()
    _loaders = {
        ".txt":  _txt,
        ".pdf":  _pdf,
        ".docx": _docx,
        ".xlsx": _xlsx,
        ".csv":  _csv,
        ".png":  _image,
        ".jpg":  _image,
        ".jpeg": _image,
        ".webp": _image,
    }
    loader = _loaders.get(ext)
    if not loader:
        raise ValueError(f"Unsupported file type: {ext}")

    docs = loader(str(path))
    base_meta = {"source": path.name, "file_type": ext.lstrip(".")}
    for d in docs:
        d["metadata"] = {**base_meta, **d.get("metadata", {})}
    return docs


# ── Format handlers ────────────────────────────────────────────────────────────

def _txt(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return [{"text": f.read(), "metadata": {"page": 1}}]


def _pdf(path: str) -> list[dict]:
    import fitz  # PyMuPDF
    docs, pdf = [], fitz.open(path)
    for i, page in enumerate(pdf, 1):
        text = page.get_text().strip()
        if text:
            docs.append({"text": text, "metadata": {"page": i}})
    pdf.close()
    return docs or [{"text": "", "metadata": {"page": 1}}]


def _docx(path: str) -> list[dict]:
    from docx import Document
    doc   = Document(path)
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    # group into sections of 10 paragraphs
    docs  = []
    for i in range(0, max(len(paras), 1), 10):
        docs.append({"text": "\n".join(paras[i:i + 10]),
                     "metadata": {"section": i // 10 + 1}})
    return docs


def _xlsx(path: str) -> list[dict]:
    import pandas as pd
    docs = []
    for sheet in pd.ExcelFile(path).sheet_names:
        df = pd.read_excel(path, sheet_name=sheet)
        docs.append({"text": f"Sheet: {sheet}\n{df.to_string(index=False)}",
                     "metadata": {"sheet": sheet}})
    return docs or [{"text": "", "metadata": {"sheet": "Sheet1"}}]


def _csv(path: str) -> list[dict]:
    import pandas as pd
    df, docs, n = pd.read_csv(path), [], 100
    for i in range(0, max(len(df), 1), n):
        chunk = df.iloc[i:i + n]
        docs.append({"text": chunk.to_string(index=False),
                     "metadata": {"rows": f"{i+1}-{min(i+n, len(df))}"}})
    return docs


def _image(path: str) -> list[dict]:
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(path))
        return [{"text": text, "metadata": {"type": "ocr"}}]
    except Exception as e:
        return [{"text": f"[OCR failed: {e}]", "metadata": {"type": "ocr_failed"}}]
