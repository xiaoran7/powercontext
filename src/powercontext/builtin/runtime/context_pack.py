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

"""Incremental ContextPack wire protocol, chunking, and delta reconstruction."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENVELOPE_ITEMS_MARKER = 'BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{"trust":"untrusted_history","items":['
_BEGIN_LINE_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n"


class ContextPackError(ValueError):
    """Raised when a ContextPack, ContextPackDelta, or chunk boundary is invalid."""


def _validate_digest_format(value: object, field: str = "digest") -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.match(value):
        raise ContextPackError(f"Invalid {field}: {value!r}")  # noqa: TRY003
    return value


def _encode_b64_items(items: tuple[bytes, ...]) -> list[str]:
    result: list[str] = []
    for item in items:
        if not isinstance(item, bytes):
            raise ContextPackError("Pack items must be bytes")  # noqa: TRY003
        result.append(base64.b64encode(item).decode("ascii"))
    return result


def _decode_b64_items(raw_items: object, field: str = "items") -> tuple[bytes, ...]:
    if not isinstance(raw_items, list):
        raise ContextPackError(f"{field} must be a list of base64 strings")  # noqa: TRY003
    decoded_list: list[bytes] = []
    for entry in raw_items:
        if not isinstance(entry, str):
            raise ContextPackError(f"Each element in {field} must be a base64 string")  # noqa: TRY003
        try:
            raw_bytes = base64.b64decode(entry.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError) as exc:
            raise ContextPackError(f"Invalid base64 encoding in {field}") from exc  # noqa: TRY003
        if base64.b64encode(raw_bytes).decode("ascii") != entry:
            raise ContextPackError(f"Non-canonical base64 encoding in {field}")  # noqa: TRY003
        decoded_list.append(raw_bytes)
    return tuple(decoded_list)


def _compute_pack_digest_and_bytes(items: tuple[bytes, ...]) -> tuple[str, int, bytes]:
    b64_items = _encode_b64_items(items)
    unsigned = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": b64_items,
    }
    canonical_unsigned = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical_unsigned).hexdigest()

    byte_count = 0
    while True:
        payload = {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": b64_items,
            "digest": digest,
            "byte_count": byte_count,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        actual_len = len(serialized)
        if actual_len == byte_count:
            return digest, byte_count, serialized
        byte_count = actual_len


def _chunk_utf8_bytes(data: bytes, chunk_bytes: int) -> list[bytes]:
    if not data:
        return []
    chunks: list[bytes] = []
    pos = 0
    total = len(data)
    while pos < total:
        end = min(total, pos + chunk_bytes)
        if end < total:
            while end > pos and (data[end] & 0xC0) == 0x80:
                end -= 1
        if end == pos:
            raise ContextPackError(
                f"Cannot form a valid UTF-8 chunk within chunk_bytes={chunk_bytes}"
            )  # noqa: TRY003
        chunk = data[pos:end]
        try:
            chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContextPackError("Chunk is not valid UTF-8") from exc  # noqa: TRY003
        chunks.append(chunk)
        pos = end
    return chunks


def _segment_prepared_content(content: str, chunk_bytes: int) -> list[bytes]:
    encoded = content.encode("utf-8")
    if not encoded:
        return []
    if chunk_bytes < 64:
        return _chunk_utf8_bytes(encoded, chunk_bytes)

    segments: list[bytes] = []
    marker_idx = content.find(_ENVELOPE_ITEMS_MARKER)
    if marker_idx != -1:
        split_pos = marker_idx + len(_ENVELOPE_ITEMS_MARKER)
        rev_idx = content.find(',"revision":', split_pos)
        if rev_idx != -1 and len(content[:rev_idx].encode("utf-8")) <= chunk_bytes:
            split_pos = rev_idx
        header_part = content[:split_pos]
        rest_part = content[split_pos:]
        segments.extend(_chunk_utf8_bytes(header_part.encode("utf-8"), chunk_bytes))
        item_parts = rest_part.split(',{"citation":')
        for idx, part in enumerate(item_parts):
            piece = part if idx == 0 else ',{"citation":' + part
            segments.extend(_chunk_utf8_bytes(piece.encode("utf-8"), chunk_bytes))
        return segments

    begin_idx = content.find(_BEGIN_LINE_MARKER)
    if begin_idx != -1:
        split_pos = begin_idx + len(_BEGIN_LINE_MARKER)
        segments.extend(_chunk_utf8_bytes(content[:split_pos].encode("utf-8"), chunk_bytes))
        segments.extend(_chunk_utf8_bytes(content[split_pos:].encode("utf-8"), chunk_bytes))
        return segments

    return _chunk_utf8_bytes(encoded, chunk_bytes)


@dataclass(frozen=True)
class ContextPack:
    """Serialized, digest-verified chunked context representation."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(not isinstance(item, bytes) for item in self.items):
            raise ContextPackError("ContextPack.items must be a tuple of bytes")  # noqa: TRY003
        _validate_digest_format(self.digest, "digest")
        if type(self.byte_count) is not int or self.byte_count < 1:
            raise ContextPackError("ContextPack.byte_count must be a positive integer")  # noqa: TRY003
        expected_digest, expected_byte_count, _ = _compute_pack_digest_and_bytes(self.items)
        if self.digest != expected_digest:
            raise ContextPackError("ContextPack.digest does not match canonical items digest")  # noqa: TRY003
        if self.byte_count != expected_byte_count:
            raise ContextPackError("ContextPack.byte_count does not match serialized byte length")  # noqa: TRY003

    @classmethod
    def build(cls, items: Sequence[bytes]) -> ContextPack:
        if isinstance(items, (bytes, bytearray, str)):
            raise ContextPackError("items must be a sequence of bytes chunks")  # noqa: TRY003
        try:
            items_tuple = tuple(items)
        except TypeError as exc:
            raise ContextPackError("items must be an iterable of bytes") from exc  # noqa: TRY003
        for item in items_tuple:
            if not isinstance(item, bytes):
                raise ContextPackError("Each item in ContextPack must be bytes")  # noqa: TRY003
        digest, byte_count, _ = _compute_pack_digest_and_bytes(items_tuple)
        return cls(items=items_tuple, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        if content is None:
            return cls.build(())
        if not isinstance(content, str):
            raise ContextPackError("content must be a string or None")  # noqa: TRY003
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContextPackError("content must be valid UTF-8") from exc  # noqa: TRY003
        if content == "":
            return cls.build(())
        chunks = _segment_prepared_content(content, chunk_bytes)
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        _, _, serialized = _compute_pack_digest_and_bytes(self.items)
        return serialized

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        if not isinstance(raw, (bytes, bytearray)):
            raise ContextPackError("Wire payload must be bytes")  # noqa: TRY003
        raw_bytes = bytes(raw)
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise ContextPackError("Invalid JSON in ContextPack wire payload") from exc  # noqa: TRY003

        if not isinstance(payload, dict) or set(payload.keys()) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("ContextPack JSON must contain exact fields")  # noqa: TRY003
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError(f"Unsupported ContextPack schema: {payload['schema']!r}")  # noqa: TRY003

        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(raw_bytes):
            raise ContextPackError("ContextPack byte_count does not match wire bytes length")  # noqa: TRY003

        digest = _validate_digest_format(payload["digest"], "digest")
        items = _decode_b64_items(payload["items"], "items")
        pack = cls.build(items)
        if pack.digest != digest or pack.byte_count != byte_count:
            raise ContextPackError("ContextPack digest or byte_count verification failed")  # noqa: TRY003
        return pack


@dataclass(frozen=True)
class ContextPackDelta:
    """Prefix-reuse delta transforming one ContextPack into another."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest_format(self.base_digest, "base_digest")
        _validate_digest_format(self.target_digest, "target_digest")
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        if isinstance(self.appended, (bytes, bytearray, str)):
            raise ContextPackError("appended must be a tuple of bytes")  # noqa: TRY003
        try:
            appended_tuple = tuple(self.appended)
        except TypeError as exc:
            raise ContextPackError("appended must be an iterable of bytes") from exc  # noqa: TRY003
        if any(not isinstance(item, bytes) for item in appended_tuple):
            raise ContextPackError("All appended chunks must be bytes")  # noqa: TRY003
        object.__setattr__(self, "appended", appended_tuple)

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires base and target ContextPack instances")  # noqa: TRY003
        prefix_len = 0
        for base_item, target_item in zip(base.items, target.items):
            if base_item != target_item:
                break
            prefix_len += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_len,
            appended=target.items[prefix_len:],
        )

    def to_bytes(self) -> bytes:
        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_b64_items(self.appended),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        if not isinstance(raw, (bytes, bytearray)):
            raise ContextPackError("Delta wire payload must be bytes")  # noqa: TRY003
        raw_bytes = bytes(raw)
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise ContextPackError("Invalid JSON in ContextPackDelta wire payload") from exc  # noqa: TRY003

        expected_keys = {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        if not isinstance(payload, dict) or set(payload.keys()) != expected_keys:
            raise ContextPackError("ContextPackDelta JSON must contain exact fields")  # noqa: TRY003
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError(f"Unsupported ContextPackDelta schema: {payload['schema']!r}")  # noqa: TRY003

        base_digest = _validate_digest_format(payload["base_digest"], "base_digest")
        target_digest = _validate_digest_format(payload["target_digest"], "target_digest")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        appended = _decode_b64_items(payload["appended"], "appended")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Reconstruct and verify the target ContextPack from ``base`` and ``delta``."""

    if not isinstance(base, ContextPack):
        raise ContextPackError("base must be a ContextPack")  # noqa: TRY003
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta must be a ContextPackDelta")  # noqa: TRY003
    if delta.base_digest != base.digest:
        raise ContextPackError("Delta base_digest does not match base ContextPack digest")  # noqa: TRY003
    if type(delta.prefix_items) is not int or delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("Delta prefix_items is out of bounds for base ContextPack")  # noqa: TRY003

    reconstructed_items = base.items[: delta.prefix_items] + delta.appended
    target = ContextPack.build(reconstructed_items)
    if target.digest != delta.target_digest:
        raise ContextPackError("Reconstructed ContextPack digest does not match delta target_digest")  # noqa: TRY003
    return target


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
