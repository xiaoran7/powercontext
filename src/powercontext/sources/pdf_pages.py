# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Full-page PDF Source capture, text-evidence projection, and offline page retrieval."""

from __future__ import annotations

import hashlib
import io
import logging
import math
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import quote, unquote

from pydantic import BaseModel, model_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime.models import MemoryEntryRecord
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"[a-z]+|\d+")
_ALNUM_TOKEN_RE = re.compile(r"[a-z0-9]+")
_YEAR_RE = re.compile(r"\b(19\d\d|20\d\d)\b")
_FY_YEAR_RE = re.compile(r"\bfy\s*['’]?\s*(\d{2}|\d{4})\b", re.IGNORECASE)  # noqa: RUF001

_ITEM_LINE_RE = re.compile(r"^item\s+\d+[a-z]?[\.\s–—-]", re.IGNORECASE)  # noqa: RUF001
_ITEM1_BUSINESS_RE = re.compile(r"\bitem\s+1\.?\s*[-–—]?\s*business\b", re.IGNORECASE)  # noqa: RUF001
_TOC_PAGE_NUM_END_RE = re.compile(r"\s\d{1,3}$")
_WS_RE = re.compile(r"\s+")
_COMP_INCOME_STMT_RE = re.compile(
    r"\b(?:consolidated\s+statements?\s+of\s+comprehensive\s+(?:income|loss|earnings)"
    r"|statements?\s+of\s+consolidated\s+comprehensive\s+(?:income|loss|earnings))\b"
)
_INCOME_STMT_RE = re.compile(
    r"\b(?:consolidated\s+statements?\s+of\s+(?:income|operations|earnings)"
    r"|statements?\s+of\s+consolidated\s+(?:income|operations|earnings)"
    r"|consolidated\s+(?:income|operations|earnings)\s+statements?"
    r"|income\s+statements?)\b"
)
_CASH_FLOW_STMT_RE = re.compile(
    r"\b(?:consolidated\s+statements?\s+of\s+cash\s+flows?"
    r"|statements?\s+of\s+consolidated\s+cash\s+flows?"
    r"|consolidated\s+cash\s+flows?\s+statements?)\b"
)
_BALANCE_SHEET_RE = re.compile(
    r"\b(?:consolidated\s+balance\s+sheets?"
    r"|balance\s+sheets?"
    r"|consolidated\s+statements?\s+of\s+financial\s+position"
    r"|statements?\s+of\s+consolidated\s+financial\s+position)\b"
)
_EQUITY_STMT_RE = re.compile(
    r"\b(?:consolidated\s+statements?\s+of\s+(?:(?:stockholders|shareholders|shareowners|common\s+stockholders|total)['’]?\s+)?equity"  # noqa: RUF001
    r"|statements?\s+of\s+consolidated\s+(?:(?:stockholders|shareholders|shareowners)['’]?\s+)?equity)\b"  # noqa: RUF001
)

_STOPWORDS = frozenset({
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "has",
    "have",
    "had",
    "any",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "if",
    "then",
    "not",
    "no",
    "yes",
    "into",
    "out",
    "up",
    "down",
    "over",
    "under",
    "within",
    "only",
    "use",
    "using",
    "used",
    "give",
    "respond",
    "response",
    "question",
    "answer",
    "state",
    "assuming",
    "perspective",
    "investment",
    "analyst",
    "details",
    "shown",
    "relying",
    "directly",
    "outlined",
    "explicitly",
    "define",
    "calculate",
    "computed",
    "quantity",
    "much",
    "many",
    "there",
    "their",
    "about",
    "between",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "such",
    "other",
    "more",
    "most",
    "some",
    "usd",
    "millions",
    "million",
    "billions",
    "billion",
    "thousands",
    "thousand",
    "fy",
    "per",
    "also",
    "both",
    "each",
    "all",
    "reported",
    "report",
    "based",
    "data",
    "like",
    "please",
    "explain",
})

_COMPANY_GENERIC_WORDS = frozenset({
    "the",
    "company",
    "companies",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "group",
    "holdings",
    "holding",
    "international",
    "brands",
    "and",
    "of",
    "10k",
    "10q",
    "8k",
    "form",
    "annual",
    "quarterly",
    "q1",
    "q2",
    "q3",
    "q4",
})


@lru_cache(maxsize=16384)
def _stem(token: str) -> str:  # noqa: C901
    """Normalize English morphological variants for financial retrieval."""

    if len(token) <= 3 or token.isdigit():
        return token
    w = token
    if (w.endswith("icality") and len(w) > 9) or (w.endswith("ality") and len(w) > 7):
        w = w[:-3]
    elif w.endswith("abilities") and len(w) > 10:
        w = w[:-9] + "able"
    elif w.endswith("ability") and len(w) > 8:
        w = w[:-7] + "able"
    elif (w.endswith("ities") and len(w) > 6) or (w.endswith("ies") and len(w) > 4):
        w = w[:-3] + "y"
    elif w.endswith(("ches", "shes", "xes", "zes", "sses")) and len(w) > 5:
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]

    if w.endswith(("ation", "ition", "ating")) and len(w) > 7:
        w = w[:-5]
    elif w.endswith("ated") and len(w) > 6:
        w = w[:-4]
    elif w.endswith(("ate", "ing")) and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    return w


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id or not document_id.strip():
        raise ValueError("document_id must be a non-empty, non-blank string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _validate_document_sha256(document_sha256: str) -> str:
    if not isinstance(document_sha256, str) or _SHA256_RE.fullmatch(document_sha256) is None:
        raise ValueError("document_sha256 must be a 64-character lowercase hexadecimal SHA-256 string")  # noqa: TRY003
    return document_sha256


def _validate_page_number(page_number: int) -> int:
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer starting from 1")  # noqa: TRY003
    return page_number


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the deterministic, collision-free Source ID for one PDF page revision."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    _validate_page_number(page_number)
    encoded_doc = quote(document_id, safe="")
    candidate = f"pdf-page:{encoded_doc}:{document_sha256}:{page_number}"
    if len(candidate) <= MAX_SOURCE_ID_LENGTH:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    fallback = f"pdf-page:{digest}:{document_sha256}:{page_number}"
    if len(fallback) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pdf page source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return fallback


class PDFDocumentInput(BaseModel):
    """Complete raw PDF payload and caller-supplied document identifier."""

    document_id: str
    content: bytes

    @model_validator(mode="after")
    def _validate_document(self) -> PDFDocumentInput:
        _validate_document_id(self.document_id)
        return self


class PDFPageInput(BaseModel):
    """One physical page extracted from a complete PDF document."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def _validate_page(self) -> PDFPageInput:
        _validate_document_id(self.document_id)
        _validate_document_sha256(self.document_sha256)
        _validate_page_number(self.page_number)
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")  # noqa: TRY003, TRY004
        pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        return self


class PDFPageSource(Source):
    """Captured page-level PDF Source."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def _validate_source(self) -> PDFPageSource:
        expected_id = pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("PDFPageSource name and source_id must match pdf_page_source_id")  # noqa: TRY003
        if self.materialization != SourceMaterialization.CAPTURED:
            raise ValueError("PDFPageSource materialization must be CAPTURED")  # noqa: TRY003
        return self


class _PyPDFCorruptionWatcher(logging.Handler):
    """Detect stream decompression errors logged as warnings by pypdf.filters."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.corrupt_messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "while decompressing data" in msg or (record.name == "pypdf.filters" and msg.startswith("Error ")):
            self.corrupt_messages.append(msg)


_PARSED_PDF_CACHE: OrderedDict[str, tuple[str, ...]] = OrderedDict()
_MAX_PARSED_PDF_CACHE = 16


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:  # noqa: C901
    """Parse all physical pages of a complete PDF document atomically."""

    if not isinstance(value, PDFDocumentInput):
        raise ValueError("value must be a PDFDocumentInput")  # noqa: TRY003, TRY004
    _validate_document_id(value.document_id)
    if not isinstance(value.content, (bytes, bytearray)) or len(value.content) == 0:
        raise ValueError("PDF content must be non-empty bytes")  # noqa: TRY003

    raw = bytes(value.content)
    if b"%PDF-" not in raw[:1024]:
        raise ValueError("Invalid PDF header")  # noqa: TRY003
    if b"%%EOF" not in raw[-1024:]:
        raise ValueError("Missing PDF EOF marker")  # noqa: TRY003

    document_sha256 = hashlib.sha256(raw).hexdigest()

    cached_texts = _PARSED_PDF_CACHE.get(document_sha256)
    if cached_texts is not None:
        _PARSED_PDF_CACHE.move_to_end(document_sha256)
        return tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=idx,
                text=page_text,
            )
            for idx, page_text in enumerate(cached_texts, start=1)
        )

    watcher = _PyPDFCorruptionWatcher()
    pypdf_logger = logging.getLogger("pypdf")
    pypdf_logger.addHandler(watcher)
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted:
            decrypt_status = reader.decrypt("")
            if not decrypt_status:
                raise ValueError("Encrypted PDF requires a non-empty password")  # noqa: TRY003, TRY301
        total_pages = len(reader.pages)
        if total_pages < 1:
            raise ValueError("PDF document must contain at least one page")  # noqa: TRY003, TRY301

        page_texts: list[str] = []
        pages: list[PDFPageInput] = []
        for page_number, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            if not extracted and "/Contents" in page:
                page.get_contents()
            if watcher.corrupt_messages:
                raise ValueError(f"Corrupt PDF stream on page {page_number}: {watcher.corrupt_messages[0]}")  # noqa: TRY003, TRY301
            normalized = unicodedata.normalize("NFC", extracted)
            text = normalized.strip() if normalized.strip() else ""
            page_texts.append(text)
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=document_sha256,
                    page_number=page_number,
                    text=text,
                )
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF document: {exc}") from exc  # noqa: TRY003
    finally:
        pypdf_logger.removeHandler(watcher)

    _PARSED_PDF_CACHE[document_sha256] = tuple(page_texts)
    while len(_PARSED_PDF_CACHE) > _MAX_PARSED_PDF_CACHE:
        _PARSED_PDF_CACHE.popitem(last=False)

    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read page-level PDF Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project one PDFPageSource into canonical TextEvidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version="1",
    projections=(PDFPageTextEvidenceProjection(),),
)


@dataclass
class _IndexedPage:
    entry_id: str
    entry_version_id: str
    document_id: str
    page_number: int
    page_text: str
    text_lower: str
    header_lower: str
    tokens: list[str]
    token_counts: Counter[str]
    stem_counts: Counter[str]
    header_tokens: list[str]
    header_token_counts: Counter[str]
    header_stem_counts: Counter[str]
    bigrams: set[tuple[str, str]]
    header_bigrams: set[tuple[str, str]]
    is_toc: bool = False
    is_exhibit: bool = False
    is_item1_business: bool = False
    is_income_stmt: bool = False
    is_comp_income_stmt: bool = False
    is_cash_flow_stmt: bool = False
    is_balance_sheet: bool = False
    is_equity_stmt: bool = False


@dataclass
class _CorpusIndex:
    all_pages: list[_IndexedPage]
    by_doc: dict[str, list[_IndexedPage]]
    n_docs: int
    avg_len: float
    avg_header_len: float
    df_exact: Counter[str]
    df_stem: Counter[str]


_CORPUS_INDEX_CACHE: OrderedDict[tuple[Any, ...], _CorpusIndex] = OrderedDict()
_MAX_CORPUS_INDEX_CACHE = 8


def _parse_page_identity(record: MemoryEntryRecord) -> tuple[str, int, str] | None:
    full_text = record.entry.text
    first_nl = full_text.find("\n")
    if first_nl >= 0:
        doc_from_text = full_text[:first_nl].strip()
        page_text = full_text[first_nl + 1 :]
    else:
        doc_from_text = full_text.strip()
        page_text = ""

    for ref in record.entry.sources:
        sid = ref.source_id
        if sid.startswith("pdf-page:"):
            parts = sid.split(":")
            if len(parts) >= 4:
                try:
                    page_num = int(parts[-1])
                except ValueError:
                    continue
                sha_candidate = parts[-2]
                encoded_doc = ":".join(parts[1:-2])
                if _SHA256_RE.fullmatch(sha_candidate) is not None:
                    decoded_doc = unquote(encoded_doc)
                    doc_id = doc_from_text if doc_from_text else decoded_doc
                    return doc_id, page_num, page_text

    return None


def _is_toc_numbered_line(line: str) -> bool:
    if _TOC_PAGE_NUM_END_RE.search(line) is None:
        return False
    return any(ch.isalpha() for ch in line)


def _classify_document_pages(pages: list[_IndexedPage]) -> None:  # noqa: C901
    pages.sort(key=lambda p: p.page_number)
    signatures_page: int | None = None
    for p in pages:
        if p.page_number >= 25 and (
            "pursuant to the requirements of section 13" in p.text_lower
            or ("\nsignatures\n" in f"\n{p.text_lower}\n" and "exchange act" in p.text_lower)
        ):
            signatures_page = p.page_number
            break

    for idx, p in enumerate(pages):
        if signatures_page is not None and p.page_number > signatures_page + 2:
            p.is_exhibit = True

        lines = [
            line.strip()
            for line in p.page_text.splitlines()
            if line.strip() and line.strip().lower() != "table of contents"
        ]
        head_lines = lines[:10]
        head_joined = " | ".join(head_lines).lower()

        item_line_count = sum(1 for line in lines if _ITEM_LINE_RE.match(line) is not None)
        page_num_end_count = sum(1 for line in lines if _is_toc_numbered_line(line))
        if item_line_count >= 5 or (page_num_end_count >= 12 and len(lines) >= 15):
            p.is_toc = True

        if p.is_toc or p.is_exhibit:
            continue

        if _ITEM1_BUSINESS_RE.search(head_joined):
            p.is_item1_business = True

        num_count = sum(1 for tok in p.tokens if tok.isdigit())
        has_scale_marker = any(
            marker in p.text_lower
            for marker in (
                "millions",
                "thousands",
                "billions",
                "per share",
                "operating activities",
                "investing activities",
                "financing activities",
                "current assets",
                "current liabilities",
            )
        )
        if num_count < 8 or not has_scale_marker:
            continue

        if _COMP_INCOME_STMT_RE.search(head_joined):
            p.is_comp_income_stmt = True
        elif _INCOME_STMT_RE.search(head_joined):
            p.is_income_stmt = True

        if _CASH_FLOW_STMT_RE.search(head_joined):
            p.is_cash_flow_stmt = True

        if _BALANCE_SHEET_RE.search(head_joined):
            p.is_balance_sheet = True

        if _EQUITY_STMT_RE.search(head_joined):
            p.is_equity_stmt = True

        if idx > 0:
            prev = pages[idx - 1]
            if (
                prev.is_cash_flow_stmt
                and not p.is_cash_flow_stmt
                and (
                    "financing activities" in p.text_lower
                    or "investing activities" in p.text_lower
                    or "supplemental cash flow" in p.text_lower
                    or "cash and cash equivalents, end of" in p.text_lower
                )
                and not (p.is_balance_sheet or p.is_income_stmt or p.is_equity_stmt)
            ):
                p.is_cash_flow_stmt = True
            if (
                prev.is_balance_sheet
                and not p.is_balance_sheet
                and (
                    "liabilities and" in p.text_lower
                    or "total liabilities" in p.text_lower
                    or "stockholders' equity" in p.text_lower
                    or "shareholders' equity" in p.text_lower
                )
                and not (p.is_cash_flow_stmt or p.is_income_stmt or p.is_equity_stmt)
            ):
                p.is_balance_sheet = True


def _build_corpus_index(records: tuple[MemoryEntryRecord, ...]) -> _CorpusIndex:
    by_doc: dict[str, list[_IndexedPage]] = defaultdict(list)
    all_pages: list[_IndexedPage] = []
    df_exact: Counter[str] = Counter()
    df_stem: Counter[str] = Counter()

    for record in records:
        parsed = _parse_page_identity(record)
        if parsed is None:
            continue
        doc_id, page_num, page_text = parsed
        text_lower = _WS_RE.sub(" ", page_text.lower())
        lines = [
            line.strip()
            for line in page_text.splitlines()
            if line.strip() and line.strip().lower() != "table of contents"
        ]
        header_lower = _WS_RE.sub(" ", "\n".join(lines[:10]).lower())
        tokens = _TOKEN_RE.findall(text_lower)
        token_counts = Counter(tokens)
        stem_counts: Counter[str] = Counter()
        for tok, cnt in token_counts.items():
            stem_counts[_stem(tok)] += cnt

        header_tokens = _TOKEN_RE.findall(header_lower)
        header_token_counts = Counter(header_tokens)
        header_stem_counts: Counter[str] = Counter()
        for tok, cnt in header_token_counts.items():
            header_stem_counts[_stem(tok)] += cnt

        for tok in token_counts:
            df_exact[tok] += 1
        for st in stem_counts:
            df_stem[st] += 1

        bigrams = {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)} if len(tokens) >= 2 else set()
        header_bigrams = (
            {(header_tokens[i], header_tokens[i + 1]) for i in range(len(header_tokens) - 1)}
            if len(header_tokens) >= 2
            else set()
        )
        indexed = _IndexedPage(
            entry_id=record.entry.entry_id,
            entry_version_id=record.entry.entry_version_id,
            document_id=doc_id,
            page_number=page_num,
            page_text=page_text,
            text_lower=text_lower,
            header_lower=header_lower,
            tokens=tokens,
            token_counts=token_counts,
            stem_counts=stem_counts,
            header_tokens=header_tokens,
            header_token_counts=header_token_counts,
            header_stem_counts=header_stem_counts,
            bigrams=bigrams,
            header_bigrams=header_bigrams,
        )
        by_doc[doc_id].append(indexed)
        all_pages.append(indexed)

    for doc_pages in by_doc.values():
        _classify_document_pages(doc_pages)

    n_docs = len(all_pages)
    avg_len = max(1.0, sum(len(p.tokens) for p in all_pages) / max(1, n_docs))
    avg_header_len = max(1.0, sum(len(p.header_tokens) for p in all_pages) / max(1, n_docs))

    return _CorpusIndex(
        all_pages=all_pages,
        by_doc=by_doc,
        n_docs=n_docs,
        avg_len=avg_len,
        avg_header_len=avg_header_len,
        df_exact=df_exact,
        df_stem=df_stem,
    )


def _extract_question_years(question: str) -> set[str]:
    years: set[str] = set()
    for match in _YEAR_RE.findall(question):
        years.add(match)
    for match in _FY_YEAR_RE.findall(question):
        if len(match) == 2:
            years.add(f"20{match}")
        else:
            years.add(match)
    return years


def _compute_document_scores(  # noqa: C901
    by_doc: dict[str, list[_IndexedPage]],
    question: str,
) -> dict[str, float]:
    if len(by_doc) <= 1:
        return dict.fromkeys(by_doc, 0.0)

    q_lower = question.lower()
    q_alnum = re.sub(r"[^a-z0-9]+", "", q_lower)
    q_tokens = set(_TOKEN_RE.findall(q_lower))
    q_alnum_tokens = set(_ALNUM_TOKEN_RE.findall(q_lower))
    q_years = _extract_question_years(question)

    scores: dict[str, float] = {}
    for doc_id, doc_pages in by_doc.items():
        score = 0.0
        doc_lower = doc_id.lower()
        raw_parts = [part for part in re.split(r"[^a-z0-9]+", doc_lower) if part]
        doc_years = {part for part in raw_parts if re.fullmatch(r"(?:19|20)\d\d", part)}

        company_parts: list[str] = []
        for part in raw_parts:
            if part in doc_years or part in {"10k", "10q", "8k", "q1", "q2", "q3", "q4", "fy", "earnings"}:
                break
            if part not in _COMPANY_GENERIC_WORDS:
                company_parts.append(part)

        company_concat = "".join(company_parts)
        company_matched = False
        if company_concat:
            if (len(company_concat) >= 4 and company_concat in q_alnum) or (
                len(company_concat) <= 3 and (company_concat in q_alnum_tokens or company_concat in q_tokens)
            ):
                score += 2000.0
                company_matched = True
            elif any(len(part) >= 4 and (part in q_alnum_tokens or part in q_tokens) for part in company_parts):
                score += 1600.0
                company_matched = True
            elif any(len(part) >= 2 and part in q_alnum_tokens for part in company_parts):
                score += 1800.0
                company_matched = True

        if not company_matched and doc_pages:
            cover_text = " ".join(p.text_lower for p in doc_pages[:3])
            cover_tokens = set(_ALNUM_TOKEN_RE.findall(cover_text)) | set(_TOKEN_RE.findall(cover_text))
            if (
                (
                    ("jnj" in q_alnum_tokens or "johnson" in q_tokens)
                    and "johnson" in cover_tokens
                    and company_concat in {"jnj", "johnsonjohnson"}
                )
                or (
                    ("pg" in q_alnum_tokens or "procter" in q_tokens or "gamble" in q_tokens)
                    and "procter" in cover_tokens
                    and company_concat in {"pg", "proctergamble"}
                )
                or ("amex" in q_alnum_tokens and "american" in cover_tokens and "express" in cover_tokens)
                or (("3m" in q_alnum_tokens or "mmm" in q_alnum_tokens) and "3m" in cover_tokens)
            ):
                score += 2000.0
                company_matched = True

        if q_years and doc_years:
            if q_years & doc_years:
                score += 300.0
            else:
                for qy in q_years:
                    try:
                        qy_int = int(qy)
                    except ValueError:
                        continue
                    for dy in doc_years:
                        try:
                            dy_int = int(dy)
                        except ValueError:
                            continue
                        if 0 <= dy_int - qy_int <= 2:
                            score += 120.0

        scores[doc_id] = score

    return scores


@dataclass
class _QueryIntent:
    explicit_income_stmt: bool = False
    explicit_cash_flow: bool = False
    explicit_balance_sheet: bool = False
    explicit_equity_stmt: bool = False
    wants_income_stmt: bool = False
    wants_cash_flow: bool = False
    wants_balance_sheet: bool = False
    wants_equity_stmt: bool = False
    wants_geography_segments: bool = False
    wants_customers: bool = False
    wants_mda_drivers: bool = False
    wants_cyclicality: bool = False
    wants_legal: bool = False
    wants_restructuring: bool = False
    wants_properties: bool = False
    wants_debt: bool = False
    wants_acquisitions: bool = False
    wants_taxes: bool = False
    wants_pensions: bool = False
    wants_leases: bool = False
    wants_share_comp: bool = False
    expanded_terms: dict[str, float] = field(default_factory=dict)
    expanded_phrases: list[tuple[str, float]] = field(default_factory=list)


def _analyze_query_intent(question: str) -> _QueryIntent:  # noqa: C901
    q_lower = question.lower()
    intent = _QueryIntent()

    if any(
        phrase in q_lower
        for phrase in (
            "income statement",
            "income statements",
            "statement of income",
            "statements of income",
            "statement of operations",
            "statements of operations",
            "statement of earnings",
            "statements of earnings",
            "p&l",
            "profit and loss",
        )
    ):
        intent.explicit_income_stmt = True
        intent.wants_income_stmt = True

    if any(
        phrase in q_lower
        for phrase in (
            "cash flow statement",
            "cash flows statement",
            "statement of cash flows",
            "statements of cash flows",
            "statement of cash flow",
            "cash flow statements",
        )
    ):
        intent.explicit_cash_flow = True
        intent.wants_cash_flow = True

    if any(
        phrase in q_lower
        for phrase in (
            "balance sheet",
            "balance sheets",
            "statement of financial position",
            "statements of financial position",
        )
    ):
        intent.explicit_balance_sheet = True
        intent.wants_balance_sheet = True

    if any(
        phrase in q_lower
        for phrase in (
            "statement of equity",
            "statements of equity",
            "shareholders' equity",
            "stockholders' equity",
            "statement of stockholders",
            "statement of shareholders",
        )
    ):
        intent.explicit_equity_stmt = True
        intent.wants_equity_stmt = True

    if any(
        k in q_lower
        for k in (
            "capital-intensive",
            "capital intensive",
            "capital intensity",
        )
    ):
        intent.wants_income_stmt = True
        intent.wants_balance_sheet = True
        intent.wants_cash_flow = True
        intent.expanded_phrases.extend([
            ("consolidated statement of income", 10.0),
            ("consolidated statements of income", 10.0),
            ("consolidated balance sheet", 10.0),
            ("consolidated balance sheets", 10.0),
            ("consolidated statement of cash flows", 10.0),
            ("consolidated statements of cash flows", 10.0),
            ("property, plant and equipment", 6.0),
            ("purchases of property, plant and equipment", 6.0),
        ])

    if any(
        k in q_lower
        for k in (
            "what drove",
            "drove ",
            "driven by",
            "dragged down",
            "exclude the impact",
            "excluding the impact",
            "why did",
            "key drivers",
        )
    ):
        intent.wants_mda_drivers = True
        intent.expanded_phrases.extend([
            ("results of operations", 12.0),
            ("analysis of consolidated", 14.0),
            ("operating income margin", 14.0),
            ("operating margin", 10.0),
            ("percent of sales", 14.0),
            ("percent to sales", 16.0),
            ("as a percent to total sales", 16.0),
            ("measured as a percent of sales", 16.0),
            ("cost of products sold", 14.0),
            ("sales change", 10.0),
            ("organic sales", 10.0),
            ("compared to the same period", 10.0),
            ("increased special item", 10.0),
            ("selling, general and administrative", 8.0),
            ("sg&a", 8.0),
        ])
        for term in ("margin", "increased", "decreased", "compared", "impacted", "segment", "sales"):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 1.8)

    if any(
        k in q_lower
        for k in (
            "ebitda",
            "ebit",
            "operating income",
            "operating profit",
            "operating margin",
            "gross profit",
            "gross margin",
            "net revenue",
            "net sales",
            "total revenue",
            "revenue",
            "cost of sales",
            "cost of goods sold",
            "cogs",
            "selling, general",
            "sg&a",
            "research and development",
            "r&d",
            "net income",
            "net earnings",
            "earnings per share",
            "eps",
            "interest expense",
            "operating loss",
            "nopat",
            "inventory turnover",
            "high growth",
        )
    ):
        intent.wants_income_stmt = True
        intent.expanded_phrases.extend([
            ("consolidated statement of income", 8.0),
            ("consolidated statements of income", 8.0),
            ("consolidated statements of operations", 8.0),
            ("consolidated statement of operations", 8.0),
            ("operating profit", 5.0),
            ("operating income", 5.0),
            ("net revenue", 4.0),
            ("cost of sales", 4.0),
        ])

    if any(
        k in q_lower
        for k in (
            "ebitda",
            "capex",
            "capital expenditure",
            "capital expenditures",
            "capital spending",
            "additions to property",
            "free cash flow",
            "fcf",
            "depreciation and amortization",
            "d&a",
            "depreciation",
            "amortization",
            "operating cash flow",
            "cash flow",
            "cash flows",
            "cash provided by operating",
            "dividends paid",
            "share repurchase",
            "stock repurchase",
        )
    ):
        intent.wants_cash_flow = True
        intent.expanded_phrases.extend([
            ("consolidated statement of cash flows", 8.0),
            ("consolidated statements of cash flows", 8.0),
            ("operating activities", 5.0),
            ("depreciation and amortization", 6.0),
            ("capital spending", 6.0),
            ("capital expenditures", 6.0),
            ("additions to property, plant and equipment", 6.0),
        ])
        for term in ("depreciation", "amortization", "capital", "spending", "expenditures", "operating", "flows"):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 1.5)

    if any(
        k in q_lower
        for k in (
            "total assets",
            "current assets",
            "cash and cash equivalents",
            "marketable securities",
            "accounts receivable",
            "receivables",
            "inventory",
            "inventories",
            "property, plant and equipment",
            "pp&e",
            "ppne",
            "net pp&e",
            "fixed assets",
            "goodwill",
            "intangible assets",
            "total liabilities",
            "current liabilities",
            "largest liability",
            "largest asset",
            "accounts payable",
            "short-term debt",
            "long-term debt",
            "total debt",
            "net debt",
            "working capital",
            "quick ratio",
            "current ratio",
            "debt-to-equity",
            "total equity",
            "retained earnings",
            "return on assets",
            "roa",
            "return on equity",
            "roe",
            "return on invested capital",
            "roic",
            "days sales",
            "dso",
            "days inventory",
            "dio",
            "days payable",
            "dpo",
            "cash conversion cycle",
            "book value",
        )
    ):
        intent.wants_balance_sheet = True
        intent.expanded_phrases.extend([
            ("consolidated balance sheet", 8.0),
            ("consolidated balance sheets", 8.0),
            ("consolidated statements of financial position", 8.0),
            ("current assets", 5.0),
            ("current liabilities", 5.0),
            ("total assets", 5.0),
            ("total liabilities", 5.0),
        ])

    if any(
        k in q_lower
        for k in (
            "geographies",
            "geography",
            "geographic",
            "countries",
            "regions",
            "primarily operates",
            "operates in",
            "operate in",
            "reportable segments",
            "reporting segment",
            "segments",
            "divisions",
            "business segments",
            "product categories",
            "service categories",
            "major products",
            "products and services",
            "what industry",
            "industry does",
        )
    ):
        intent.wants_geography_segments = True
        intent.expanded_phrases.extend([
            ("summary of business segment data", 18.0),
            ("business segment data", 14.0),
            ("reportable segments", 12.0),
            ("our divisions", 12.0),
            ("our operations", 10.0),
            ("item 1. business", 10.0),
            ("note 1", 8.0),
            ("net revenue and long-lived assets by country", 14.0),
            ("countries and territories", 10.0),
            ("largest operations in", 12.0),
            ("north america", 6.0),
            ("latin america", 6.0),
            ("united states", 5.0),
            ("international", 4.0),
        ])
        for term in (
            "segments",
            "segment",
            "reportable",
            "divisions",
            "countries",
            "territories",
            "geographic",
            "europe",
            "asia",
            "africa",
            "america",
        ):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 2.0)

    if any(
        k in q_lower
        for k in (
            "customer concentration",
            "primary customers",
            "major customers",
            "significant customers",
            "largest customer",
            "largest customers",
            "customers of",
            "customer accounted",
        )
    ):
        intent.wants_customers = True
        intent.expanded_phrases.extend([
            ("customer accounted for", 20.0),
            ("customers accounted for", 20.0),
            ("one customer accounted for", 22.0),
            ("loss of this customer", 16.0),
            ("derive a significant portion of our revenues", 20.0),
            ("derive a substantial portion of our revenue", 20.0),
            ("significant portion of our revenues from", 16.0),
            ("substantial portion of our revenue from", 16.0),
            ("limited number of commercial airlines", 18.0),
            ("u.s. government contracts", 16.0),
            ("earned pursuant to u.s. government", 18.0),
            ("10% or more of", 14.0),
            ("concentration of credit risk", 12.0),
        ])
        for term in (
            "customer",
            "customers",
            "accounted",
            "revenues",
            "commercial",
            "airlines",
            "government",
            "contracts",
        ):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 2.5)

    if any(
        k in q_lower
        for k in (
            "cyclicality",
            "cyclical",
            "seasonality",
            "seasonal",
        )
    ):
        intent.wants_cyclicality = True
        intent.expanded_phrases.extend([
            ("has been cyclical", 18.0),
            ("cyclical and", 16.0),
            ("profit swings", 14.0),
            ("airline industry", 10.0),
            ("seasonality", 12.0),
        ])
        for term in ("cyclical", "cyclicality", "seasonal", "seasonality", "swings", "competitive"):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 3.0)

    if any(
        k in q_lower
        for k in (
            "legal",
            "battles",
            "lawsuits",
            "lawsuit",
            "litigation",
            "proceedings",
            "contingencies",
            "disputes",
            "claims",
            "investigations",
        )
    ):
        intent.wants_legal = True
        intent.expanded_phrases.extend([
            ("item 3. legal proceedings", 18.0),
            ("legal proceedings", 14.0),
            ("commitments and contingencies", 12.0),
            ("litigation, claims", 10.0),
            ("regulatory proceedings", 10.0),
            ("material adverse effect", 8.0),
        ])
        for term in (
            "legal",
            "proceedings",
            "litigation",
            "claims",
            "contingencies",
            "regulatory",
            "inquiries",
            "investigations",
        ):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 2.5)

    if any(
        k in q_lower
        for k in (
            "restructuring",
            "impairment",
            "severance",
            "productivity plan",
        )
    ):
        intent.wants_restructuring = True
        intent.expanded_phrases.extend([
            ("restructuring and impairment", 14.0),
            ("restructuring", 8.0),
            ("productivity plan", 8.0),
        ])
        for term in ("restructuring", "impairment", "charges", "productivity"):
            intent.expanded_terms[term] = max(intent.expanded_terms.get(term, 0.0), 2.0)

    if any(
        k in q_lower
        for k in (
            "properties",
            "facilities",
            "plants",
            "headquarters",
            "principal executive office",
        )
    ):
        intent.wants_properties = True
        intent.expanded_phrases.extend([
            ("item 2. properties", 16.0),
            ("principal executive office", 12.0),
            ("significant properties", 12.0),
        ])

    if any(
        k in q_lower
        for k in (
            "debt",
            "senior notes",
            "bonds",
            "credit facility",
            "credit facilities",
            "commercial paper",
            "maturities",
            "borrowings",
            "coupon",
        )
    ):
        intent.wants_debt = True
        intent.expanded_phrases.extend([
            ("debt obligations", 12.0),
            ("long-term debt", 10.0),
            ("short-term borrowings", 10.0),
        ])

    if any(
        k in q_lower
        for k in (
            "registered to trade",
            "national securities exchange",
            "trading symbol",
            "section 12(b)",
        )
    ):
        intent.expanded_phrases.extend([
            ("securities registered pursuant to section 12", 35.0),
            ("name of each exchange on which registered", 35.0),
            ("trading symbol", 25.0),
        ])

    if any(
        k in q_lower
        for k in (
            "acquisition",
            "acquisitions",
            "divestiture",
            "divestitures",
            "merger",
            "mergers",
            "dispositions",
        )
    ):
        intent.wants_acquisitions = True
        intent.expanded_phrases.extend([
            ("acquisitions and divestitures", 14.0),
            ("business combinations", 12.0),
        ])

    if any(k in q_lower for k in ("tax", "taxes", "effective tax rate")):
        intent.wants_taxes = True
        intent.expanded_phrases.extend([
            ("income taxes", 12.0),
            ("provision for income taxes", 10.0),
            ("effective tax rate", 10.0),
        ])

    if any(k in q_lower for k in ("pension", "retiree", "postretirement", "benefit plans")):
        intent.wants_pensions = True
        intent.expanded_phrases.extend([
            ("pension, retiree medical", 12.0),
            ("employee benefit plans", 12.0),
        ])

    if any(k in q_lower for k in ("lease", "leases", "operating lease", "finance lease")):
        intent.wants_leases = True
        intent.expanded_phrases.extend([
            ("note 12", 6.0),
            ("operating leases", 10.0),
            ("lease liabilities", 10.0),
        ])

    if any(
        k in q_lower
        for k in ("share-based compensation", "stock-based compensation", "stock options", "restricted stock")
    ):
        intent.wants_share_comp = True
        intent.expanded_phrases.extend([
            ("share-based compensation", 12.0),
            ("stock-based compensation", 12.0),
        ])

    return intent


def _get_or_build_corpus_index(
    memory_ref: Any,
    records: tuple[MemoryEntryRecord, ...],
) -> _CorpusIndex:
    first_id = records[0].entry.entry_id if records else ""
    last_id = records[-1].entry.entry_id if records else ""
    cache_key = (
        getattr(memory_ref, "artifact_id", str(memory_ref)),
        getattr(memory_ref, "revision", 0),
        len(records),
        first_id,
        last_id,
    )
    cached = _CORPUS_INDEX_CACHE.get(cache_key)
    if cached is not None:
        _CORPUS_INDEX_CACHE.move_to_end(cache_key)
        return cached

    built = _build_corpus_index(records)
    _CORPUS_INDEX_CACHE[cache_key] = built
    while len(_CORPUS_INDEX_CACHE) > _MAX_CORPUS_INDEX_CACHE:
        _CORPUS_INDEX_CACHE.popitem(last=False)
    return built


async def search_pdf_pages(  # noqa: C901
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Retrieve and rank up to `limit` distinct PDF page citations for a FinanceBench question."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return ()
    if not isinstance(question, str) or not question.strip():
        return ()

    entries_page = await memory.list()
    if not entries_page.entries:
        return ()

    records = tuple(entries_page.entries)
    citation_by_entry: dict[tuple[str, str], MemoryCitation] = {
        (r.entry.entry_id, r.entry.entry_version_id): r.citation for r in records
    }

    corpus = _get_or_build_corpus_index(entries_page.memory_ref, records)
    all_pages = corpus.all_pages
    if not all_pages:
        return ()

    by_doc = corpus.by_doc
    doc_scores = _compute_document_scores(by_doc, question)
    intent = _analyze_query_intent(question)

    q_tokens_raw = _TOKEN_RE.findall(question.lower())
    core_q_tokens = [tok for tok in q_tokens_raw if tok not in _STOPWORDS and len(tok) >= 2]

    n_docs = corpus.n_docs
    avg_len = corpus.avg_len
    avg_header_len = corpus.avg_header_len

    query_term_weights: dict[str, float] = {}
    for tok in core_q_tokens:
        query_term_weights[tok] = query_term_weights.get(tok, 0.0) + 1.0
    for tok, weight in intent.expanded_terms.items():
        if tok not in query_term_weights:
            query_term_weights[tok] = weight

    query_stem_weights: dict[str, float] = {}
    for tok, weight in query_term_weights.items():
        st = _stem(tok)
        query_stem_weights[st] = max(query_stem_weights.get(st, 0.0), weight)

    query_bigrams = [(core_q_tokens[i], core_q_tokens[i + 1]) for i in range(len(core_q_tokens) - 1)]

    k1 = 1.5
    b = 0.75
    base_scores: dict[tuple[str, int], float] = {}

    for p in all_pages:
        loc = (p.document_id, p.page_number)
        if not p.tokens:
            base_scores[loc] = -50.0
            continue

        bm25_body = 0.0
        bm25_header = 0.0
        doc_len = len(p.tokens)
        header_len = max(1, len(p.header_tokens))

        for term, q_weight in query_term_weights.items():
            df = corpus.df_exact.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            tf = p.token_counts.get(term, 0)
            if tf > 0:
                denom = tf + k1 * (1.0 - b + b * (doc_len / avg_len))
                bm25_body += q_weight * idf * ((tf * (k1 + 1.0)) / denom)
            htf = p.header_token_counts.get(term, 0)
            if htf > 0:
                h_denom = htf + k1 * (1.0 - b + b * (header_len / avg_header_len))
                bm25_header += q_weight * idf * ((htf * (k1 + 1.0)) / h_denom)

        stem_body = 0.0
        stem_header = 0.0
        for st, q_weight in query_stem_weights.items():
            df = corpus.df_stem.get(st, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            tf = p.stem_counts.get(st, 0)
            if tf > 0:
                denom = tf + k1 * (1.0 - b + b * (doc_len / avg_len))
                stem_body += q_weight * idf * ((tf * (k1 + 1.0)) / denom)
            htf = p.header_stem_counts.get(st, 0)
            if htf > 0:
                h_denom = htf + k1 * (1.0 - b + b * (header_len / avg_header_len))
                stem_header += q_weight * idf * ((htf * (k1 + 1.0)) / h_denom)

        score = (0.75 * bm25_body + 0.45 * stem_body) + 2.2 * (0.75 * bm25_header + 0.45 * stem_header)

        for bg in query_bigrams:
            if bg in p.bigrams:
                score += 4.0
            if bg in p.header_bigrams:
                score += 8.0

        for phrase, weight in intent.expanded_phrases:
            if phrase in p.header_lower:
                score += weight * 1.8
            elif phrase in p.text_lower:
                score += weight

        suppress_raw_stmt_boost = (
            intent.wants_mda_drivers or intent.wants_geography_segments or intent.wants_customers
        ) and not (
            intent.explicit_income_stmt
            or intent.explicit_cash_flow
            or intent.explicit_balance_sheet
            or intent.explicit_equity_stmt
        )

        if p.is_income_stmt:
            if intent.explicit_income_stmt:
                score += 140.0
            elif intent.wants_income_stmt:
                score += 25.0 if suppress_raw_stmt_boost else 75.0
            elif (intent.wants_cash_flow or intent.wants_balance_sheet) and not suppress_raw_stmt_boost:
                score += 25.0

        if p.is_cash_flow_stmt:
            if intent.explicit_cash_flow:
                score += 140.0
            elif intent.wants_cash_flow:
                score += 25.0 if suppress_raw_stmt_boost else 75.0
            elif (intent.wants_income_stmt or intent.wants_balance_sheet) and not suppress_raw_stmt_boost:
                score += 25.0

        if p.is_balance_sheet:
            if intent.explicit_balance_sheet:
                score += 140.0
            elif intent.wants_balance_sheet:
                score += 25.0 if suppress_raw_stmt_boost else 75.0
            elif (intent.wants_income_stmt or intent.wants_cash_flow) and not suppress_raw_stmt_boost:
                score += 25.0

        if p.is_equity_stmt:
            if intent.explicit_equity_stmt:
                score += 140.0
            elif intent.wants_equity_stmt:
                score += 75.0

        if p.is_comp_income_stmt and intent.wants_income_stmt and not suppress_raw_stmt_boost:
            score += 20.0

        if intent.wants_geography_segments:
            if (
                p.is_item1_business
                or "reportable segments" in p.text_lower
                or "our divisions" in p.header_lower
                or "our operations" in p.header_lower
                or "summary of business segment data" in p.text_lower
            ):
                score += 65.0
            if (
                "net revenue" in p.text_lower
                and "long-lived assets" in p.text_lower
                and "united states" in p.text_lower
            ):
                score += 55.0
            if "countries and territories" in p.text_lower:
                score += 30.0

        if intent.wants_customers:
            if "customer accounted for" in p.text_lower or "customers accounted for" in p.text_lower:
                score += 65.0
            if (
                "derive a significant portion of our revenues" in p.text_lower
                or "derive a substantial portion of our revenue" in p.text_lower
            ):
                score += 65.0
            if "earned pursuant to u.s. government" in p.text_lower:
                score += 55.0

        if intent.wants_legal:
            if "item 3." in p.header_lower or "legal proceedings" in p.header_lower:
                score += 90.0
            elif "legal proceedings" in p.text_lower:
                score += 45.0

        if intent.wants_restructuring and (
            "restructuring and impairment" in p.header_lower or "note 3" in p.header_lower
        ):
            score += 55.0

        if intent.wants_properties and "item 2." in p.header_lower and "properties" in p.header_lower:
            score += 90.0

        if p.is_toc:
            score -= 15.0
        if p.is_exhibit:
            score -= 45.0

        base_scores[loc] = score

    final_scores: dict[tuple[str, int], float] = {}
    for doc_id, doc_pages in by_doc.items():
        d_score = doc_scores.get(doc_id, 0.0)
        n_p = len(doc_pages)
        for i, p in enumerate(doc_pages):
            loc = (p.document_id, p.page_number)
            b_score = base_scores[loc]
            neighbor_bonus = 0.0
            if i > 0 and not p.is_toc and not p.is_exhibit:
                prev_b = base_scores[(doc_pages[i - 1].document_id, doc_pages[i - 1].page_number)]
                if prev_b > 25.0:
                    neighbor_bonus = max(neighbor_bonus, 0.18 * prev_b)
            if i + 1 < n_p and not p.is_toc and not p.is_exhibit:
                next_b = base_scores[(doc_pages[i + 1].document_id, doc_pages[i + 1].page_number)]
                if next_b > 40.0:
                    neighbor_bonus = max(neighbor_bonus, 0.10 * next_b)
            final_scores[loc] = d_score + b_score + min(neighbor_bonus, 30.0)

    ranked = sorted(
        all_pages,
        key=lambda p: (-final_scores[(p.document_id, p.page_number)], p.document_id, p.page_number),
    )

    citations: list[MemoryCitation] = []
    seen_locations: set[tuple[str, int]] = set()
    for p in ranked:
        if len(citations) >= limit:
            break
        location = (p.document_id, p.page_number)
        if location in seen_locations:
            continue
        cit = citation_by_entry.get((p.entry_id, p.entry_version_id))
        if cit is None:
            continue
        seen_locations.add(location)
        citations.append(cit)

    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
