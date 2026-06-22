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


# ── Git LFS pointer detection ───────────────────────────────────────────────────

_GIT_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"

def _is_lfs_pointer(path: str) -> bool:
    """Return True if file is an un-downloaded Git LFS pointer (not real content)."""
    try:
        with open(path, "rb") as f:
            header = f.read(len(_GIT_LFS_HEADER))
        return header == _GIT_LFS_HEADER
    except OSError:
        return False


# ── Noise suppression ───────────────────────────────────────────────────────────
# Silence chatty third-party loggers that emit INFO/WARNING to the root logger.

import logging as _logging

for _noisy_logger in (
    "pikepdf",           # "C++ to Python logger bridge initialized"
    "pikepdf._core",
    "unstructured",      # "No languages specified, defaulting to English."
    "unstructured.partition",
    "unstructured.partition.pdf",
    "unstructured.documents",
    "detectron2",
    "pdfminer",
    "pdfminer.pdfdocument",
    "pdfminer.pdfpage",
    "pdfminer.pdfinterp",
    "pdfminer.converter",
    "huggingface_hub",   # "unauthenticated requests to the HF Hub"
    "transformers",
    "sentence_transformers",
    "pytesseract",
    "PIL",
):
    _logging.getLogger(_noisy_logger).setLevel(_logging.ERROR)


# ── Format handlers ─────────────────────────────────────────────────────────────

def _txt(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return [{"text": f.read(), "metadata": {"page": 1}}]


def _pdf(path: str) -> list[dict]:
    """Extract text from PDF with OCR fallback for scanned documents.

    Strategy:
    1. Detect and reject Git LFS pointer files before trying to open them.
    2. Try fast text extraction with fitz (PyMuPDF) and scan/extract text from inline images using pytesseract.
    3. If that yields no text, use unstructured.partition_pdf with hi_res strategy
       which automatically triggers OCR for scanned PDFs.
    4. If both fail, return an empty doc (never crashes the pipeline).
    """
    log = _logging.getLogger(__name__)

    # Guard: reject Git LFS pointer stubs before PyMuPDF crashes on them
    if _is_lfs_pointer(path):
        raise ValueError(
            f"File '{Path(path).name}' is a Git LFS pointer stub and has not been "
            "downloaded. Run `git lfs pull` in the repository root to fetch the real file."
        )

    import fitz  # PyMuPDF
    import io
    import warnings

    # Suppress MuPDF's own C-level stderr chatter
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            pdf = fitz.open(path)
        except Exception as exc:
            raise ValueError(f"Failed to open PDF '{Path(path).name}': {exc}") from exc

    docs = []
    for i, page in enumerate(pdf, 1):
        # 1. Native text extraction
        text = page.get_text().strip()
        
        # 2. Extract and OCR any inline images on this page
        image_list = page.get_images(full=True)
        ocr_text_parts = []
        
        if image_list:
            try:
                import pytesseract
                from PIL import Image
                
                for img_info in image_list:
                    xref = img_info[0]
                    base_image = pdf.extract_image(xref)
                    image_bytes = base_image["image"]
                    
                    try:
                        image = Image.open(io.BytesIO(image_bytes))
                        ocr_text = pytesseract.image_to_string(image).strip()
                        if ocr_text:
                            ocr_text_parts.append(ocr_text)
                    except Exception:
                        pass  # Skip individual image extraction failures
            except ImportError:
                pass  # pytesseract or PIL not installed — skip inline OCR

        # 3. Combine native text and inline image OCR text
        if ocr_text_parts:
            combined_ocr = "\n\n--- [OCR from Embedded Image] ---\n" + "\n\n".join(ocr_text_parts)
            text = (text + "\n" + combined_ocr).strip()

        if text:
            docs.append({"text": text, "metadata": {"page": i}})
            
    pdf.close()

    # If PyMuPDF text or inline OCR yielded text, return it
    if docs:
        return docs

    # Fallback: Try unstructured with hi_res strategy (includes OCR for fully scanned PDFs)
    try:
        import os
        # Suppress HF Hub auth warning before importing unstructured OCR pipeline
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        from unstructured.partition.pdf import partition_pdf  # type: ignore
        elements = partition_pdf(
            filename=path,
            strategy="hi_res",
            extract_images_in_pdf=False,
            infer_table_structure=True,
            languages=["eng"],          # suppress "No languages specified" warning
        )
        if elements:
            text = "\n\n".join([str(element) for element in elements])
            return [{"text": text, "metadata": {"page": 1, "method": "ocr"}}]
    except ImportError:
        pass  # unstructured not installed — skip OCR fallback silently
    except Exception as e:
        log.warning("OCR fallback for %s failed: %s. Returning empty document.", path, e)

    return [{"text": "", "metadata": {"page": 1}}]


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
        import logging
        logging.getLogger("pytesseract").setLevel(logging.ERROR)
        text = pytesseract.image_to_string(Image.open(path))
        return [{"text": text, "metadata": {"type": "ocr"}}]
    except Exception as e:
        return [{"text": f"[OCR failed: {e}]", "metadata": {"type": "ocr_failed"}}]
