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

"""Row-level CSV Source capture and text-evidence projection."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id or not document_id.strip():
        raise ValueError("document_id must be a non-empty, non-blank string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _validate_primary_key_name(primary_key: str) -> str:
    if not isinstance(primary_key, str) or not primary_key or not primary_key.strip():
        raise ValueError("primary_key must be a non-empty, non-blank string")  # noqa: TRY003
    return primary_key


def _validate_primary_key_value(primary_key_value: str) -> str:
    if not isinstance(primary_key_value, str) or not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty, non-blank string")  # noqa: TRY003
    return primary_key_value


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the canonical delimiter-safe Source ID for one CSV row."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    encoded_doc = quote(document_id, safe="")
    encoded_pk = quote(primary_key_value, safe="")
    source_id = f"csv:{encoded_doc}:{encoded_pk}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv row source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def _validate_columns_and_values(
    document_id: str,
    primary_key: str,
    primary_key_value: str,
    columns: tuple[str, ...],
    values: tuple[str, ...],
) -> None:
    _validate_document_id(document_id)
    _validate_primary_key_name(primary_key)
    _validate_primary_key_value(primary_key_value)
    if not columns:
        raise ValueError("columns must not be empty")  # noqa: TRY003
    if any(not isinstance(col, str) or not col or not col.strip() for col in columns):
        raise ValueError("all column names must be non-empty and non-blank")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("column names must be unique")  # noqa: TRY003
    if len(columns) != len(values):
        raise ValueError("values length must match columns length")  # noqa: TRY003
    if primary_key not in columns:
        raise ValueError("primary_key must match one of the columns")  # noqa: TRY003
    pk_idx = columns.index(primary_key)
    if values[pk_idx] != primary_key_value:
        raise ValueError("primary_key_value must match the primary key column value")  # noqa: TRY003
    csv_row_source_id(document_id, primary_key_value)


class CSVDocumentInput(BaseModel):
    """Complete offline CSV payload with document and primary-key metadata."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated row extracted from a CSV document."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_row(self) -> CSVRowInput:
        _validate_columns_and_values(
            self.document_id,
            self.primary_key,
            self.primary_key_value,
            self.columns,
            self.values,
        )
        return self


class CSVRowSource(Source):
    """Captured row-level CSV Source."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def _validate_source(self) -> CSVRowSource:
        _validate_columns_and_values(
            self.document_id,
            self.primary_key,
            self.primary_key_value,
            self.columns,
            self.values,
        )
        expected_id = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("CSVRowSource name and source_id must match csv_row_source_id")  # noqa: TRY003
        if self.materialization != SourceMaterialization.CAPTURED:
            raise ValueError("CSVRowSource materialization must be CAPTURED")  # noqa: TRY003
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse an entire CSV document atomically into row inputs."""

    if not isinstance(value, CSVDocumentInput):
        raise ValueError("value must be a CSVDocumentInput")  # noqa: TRY003
    _validate_document_id(value.document_id)
    _validate_primary_key_name(value.primary_key)
    if not isinstance(value.content, (bytes, bytearray)) or len(value.content) == 0:
        raise ValueError("CSV content must be non-empty bytes")  # noqa: TRY003

    try:
        decoded = bytes(value.content).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV content must be valid UTF-8") from exc  # noqa: TRY003

    if decoded.startswith("﻿"):
        decoded = decoded[1:]
    if not decoded:
        raise ValueError("CSV content must not be empty")  # noqa: TRY003

    reader = csv.reader(
        io.StringIO(decoded, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        strict=True,
    )
    try:
        raw_records = list(reader)
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV content: {exc}") from exc  # noqa: TRY003

    if not raw_records:
        raise ValueError("CSV document must contain a header row")  # noqa: TRY003

    header = raw_records[0]
    if not header:
        raise ValueError("CSV header row must not be empty")  # noqa: TRY003
    for col in header:
        if not col or not col.strip():
            raise ValueError("CSV header columns must be non-empty and non-blank")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header columns must be unique")  # noqa: TRY003
    if value.primary_key not in header:
        raise ValueError("primary_key not found in CSV header")  # noqa: TRY003

    columns = tuple(header)
    pk_index = columns.index(value.primary_key)
    seen_keys: set[str] = set()
    rows: list[CSVRowInput] = []

    for record in raw_records[1:]:
        if record == []:
            continue
        if len(record) != len(columns):
            raise ValueError("CSV row field count does not match header width")  # noqa: TRY003
        pk_value = record[pk_index]
        _validate_primary_key_value(pk_value)
        if pk_value in seen_keys:
            raise ValueError(f"Duplicate primary key value: {pk_value!r}")  # noqa: TRY003
        seen_keys.add(pk_value)
        csv_row_source_id(value.document_id, pk_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=pk_value,
                columns=columns,
                values=tuple(record),
            )
        )

    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read row-level CSV Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project one CSVRowSource into canonical TextEvidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row_dict = dict(zip(source.columns, source.values, strict=True))
        content = json.dumps(row_dict, ensure_ascii=False, separators=(",", ":"))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=content,
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row_dict,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)

__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
