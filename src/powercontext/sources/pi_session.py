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

"""Pi v3 session branch parser, captured event Source, and incremental connector."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, model_validator

from powercontext.errors import SourceConflictError
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"


def _validate_non_empty_trimmed(field: str, value: object) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty, non-blank string")  # noqa: TRY003
    if value != value.strip():
        raise ValueError(f"{field} must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the deterministic Source identity for one (session_id, event_id) pair."""

    _validate_non_empty_trimmed("session_id", session_id)
    _validate_non_empty_trimmed("event_id", event_id)
    encoded_session = quote(session_id, safe="")
    encoded_event = quote(event_id, safe="")
    candidate = f"pi:{encoded_session}:{encoded_event}"
    if len(candidate) <= MAX_SOURCE_ID_LENGTH:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return f"pi:{digest}"


def _canonical_json_sha256(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class PiSessionInput(BaseModel):
    """Raw Pi v3 JSONL bytes and optional target branch leaf ID."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event extracted from a selected Pi v3 session branch."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def _validate_event(self) -> PiEventInput:
        _validate_non_empty_trimmed("session_id", self.session_id)
        _validate_non_empty_trimmed("event_id", self.event_id)
        if self.parent_id is not None:
            _validate_non_empty_trimmed("parent_id", self.parent_id)
        _validate_non_empty_trimmed("event_type", self.event_type)
        _validate_non_empty_trimmed("role", self.role)
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be a non-empty string")  # noqa: TRY003
        if not isinstance(self.record, dict):
            raise ValueError("record must be a JSON object")  # noqa: TRY003
        return self


class PiEventSource(Source):
    """Captured Source representing one immutable Pi session event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED

    @model_validator(mode="after")
    def _validate_source(self) -> PiEventSource:
        expected_id = pi_event_source_id(self.session_id, self.event_id)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("PiEventSource name and source_id must match pi_event_source_id")  # noqa: TRY003
        if self.materialization != SourceMaterialization.CAPTURED:
            raise ValueError("PiEventSource materialization must be CAPTURED")  # noqa: TRY003
        return self


def _extract_content_blocks(raw_content: object, *, allow_tool_calls: bool) -> str:
    if isinstance(raw_content, str):
        return raw_content
    if not isinstance(raw_content, list):
        raise ValueError("Message content must be a string or list of content blocks")  # noqa: TRY003

    parts: list[str] = []
    for block in raw_content:
        if not isinstance(block, dict):
            raise ValueError("Content block must be a JSON object")  # noqa: TRY003
        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type.strip():
            raise ValueError("Content block type must be a non-empty string")  # noqa: TRY003
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("Text content block must contain string text")  # noqa: TRY003
            if text != "":
                parts.append(text)
        elif block_type == "toolCall" and allow_tool_calls:
            tool_name = _validate_non_empty_trimmed("toolCall.name", block.get("name"))
            tool_id = _validate_non_empty_trimmed("toolCall.id", block.get("id"))
            if "arguments" not in block:
                raise ValueError("toolCall block must include arguments")  # noqa: TRY003
            args_json = json.dumps(
                block["arguments"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            parts.append(f"[tool_call {tool_name} {tool_id}] {args_json}")
        elif block_type in {"thinking", "image"}:
            continue
        else:
            raise ValueError(f"Unsupported content block type: {block_type!r}")  # noqa: TRY003

    return "\n".join(parts)


def _record_to_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = _validate_non_empty_trimmed("type", record.get("type"))
    event_id = _validate_non_empty_trimmed("id", record.get("id"))
    parent_id = record.get("parentId")
    if parent_id is not None:
        parent_id = _validate_non_empty_trimmed("parentId", parent_id)

    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError("message record must contain a message object")  # noqa: TRY003
        role = _validate_non_empty_trimmed("message.role", message.get("role"))
        if role == "bashExecution":
            command = message.get("command")
            output = message.get("output")
            if not isinstance(command, str) or not isinstance(output, str):
                raise ValueError("bashExecution message requires string command and output")  # noqa: TRY003
            if not command.strip() and not output.strip():
                return None
            body = f"$ {command}\n{output}"
        elif role == "toolResult":
            raw_tool_name = message.get("toolName")
            if raw_tool_name is None or raw_tool_name == "":
                tool_name = "unknown"
            else:
                tool_name = _validate_non_empty_trimmed("toolResult.toolName", raw_tool_name)
            tool_call_id = _validate_non_empty_trimmed("toolResult.toolCallId", message.get("toolCallId"))
            text = _extract_content_blocks(message.get("content"), allow_tool_calls=False)
            if not text.strip():
                return None
            body = f"[tool_result {tool_name} {tool_call_id}] {text}"
        else:
            body = _extract_content_blocks(message.get("content"), allow_tool_calls=True)
            if not body.strip():
                return None
        return PiEventInput(
            session_id=session_id,
            event_id=event_id,
            parent_id=parent_id,
            event_type=event_type,
            role=role,
            content=f"{role}: {body}",
            record=copy.deepcopy(record),
        )

    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str):
            raise ValueError(f"{event_type} record requires string summary")  # noqa: TRY003
        if not summary.strip():
            return None
        role = event_type
        return PiEventInput(
            session_id=session_id,
            event_id=event_id,
            parent_id=parent_id,
            event_type=event_type,
            role=role,
            content=f"{role}: {summary}",
            record=copy.deepcopy(record),
        )

    if event_type == "custom_message":
        raw_content = record.get("content")
        if raw_content is None and isinstance(record.get("message"), dict):
            raw_content = record["message"].get("content")
        body = _extract_content_blocks(raw_content, allow_tool_calls=False)
        if not body.strip():
            return None
        role = "custom"
        return PiEventInput(
            session_id=session_id,
            event_id=event_id,
            parent_id=parent_id,
            event_type=event_type,
            role=role,
            content=f"{role}: {body}",
            record=copy.deepcopy(record),
        )

    return None


def _parse_session_archive(
    value: PiSessionInput,
) -> tuple[str, dict[str, dict[str, JsonValue]], dict[str, PiEventInput | None], tuple[PiEventInput, ...]]:
    if not isinstance(value, PiSessionInput):
        raise ValueError("value must be a PiSessionInput")  # noqa: TRY003
    if not isinstance(value.content, (bytes, bytearray)) or len(value.content) == 0:
        raise ValueError("Pi session content must be non-empty bytes")  # noqa: TRY003

    try:
        decoded = bytes(value.content).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Pi session content must be valid UTF-8") from exc  # noqa: TRY003

    lines = [line for line in decoded.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session archive must not be empty")  # noqa: TRY003

    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in session header: {exc}") from exc  # noqa: TRY003

    if not isinstance(header, dict):
        raise ValueError("Session header must be a JSON object")  # noqa: TRY003
    if header.get("type") != "session":
        raise ValueError("First record must have type='session'")  # noqa: TRY003
    version = header.get("version")
    if type(version) is not int or version != 3:
        raise ValueError("Session header version must be integer 3")  # noqa: TRY003
    session_id = _validate_non_empty_trimmed("session.id", header.get("id"))

    by_id: dict[str, dict[str, JsonValue]] = {}
    parsed_events: dict[str, PiEventInput | None] = {}
    order: list[str] = []

    for line in lines[1:]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON record in session archive: {exc}") from exc  # noqa: TRY003

        if not isinstance(record, dict):
            raise ValueError("Every session record must be a JSON object")  # noqa: TRY003
        record_type = _validate_non_empty_trimmed("record.type", record.get("type"))
        if record_type == "session":
            raise ValueError("Unexpected duplicate session header record")  # noqa: TRY003

        if "id" not in record or "parentId" not in record:
            raise ValueError("Session record must contain id and parentId")  # noqa: TRY003

        event_id = _validate_non_empty_trimmed("record.id", record["id"])
        if event_id in by_id:
            raise ValueError(f"Duplicate event id: {event_id!r}")  # noqa: TRY003

        parent_id = record["parentId"]
        if parent_id is not None:
            parent_id = _validate_non_empty_trimmed("record.parentId", parent_id)
            if parent_id not in by_id:
                raise ValueError(f"Record {event_id!r} references missing or unseen parent {parent_id!r}")  # noqa: TRY003

        event = _record_to_event(session_id, record)
        by_id[event_id] = record
        parsed_events[event_id] = event
        order.append(event_id)

    if value.leaf_id is not None:
        target_leaf = _validate_non_empty_trimmed("leaf_id", value.leaf_id)
        if target_leaf not in by_id:
            raise ValueError(f"Requested leaf_id {target_leaf!r} does not exist in session")  # noqa: TRY003
    else:
        if not order:
            return session_id, by_id, parsed_events, ()
        target_leaf = order[-1]

    chain_ids: list[str] = []
    visited: set[str] = set()
    curr: str | None = target_leaf
    while curr is not None:
        if curr in visited:
            raise ValueError(f"Cycle detected in session graph at {curr!r}")  # noqa: TRY003
        visited.add(curr)
        if curr not in by_id:
            raise ValueError(f"Missing ancestor {curr!r} in session graph")  # noqa: TRY003
        chain_ids.append(curr)
        parent = by_id[curr]["parentId"]
        curr = parent if isinstance(parent, str) else None

    chain_ids.reverse()
    branch_events = tuple(
        parsed_events[node_id]
        for node_id in chain_ids
        if parsed_events[node_id] is not None
    )
    return session_id, by_id, parsed_events, branch_events  # type: ignore[return-value]


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse a Pi v3 session archive and return visible events along the selected root-to-leaf branch."""

    _, _, _, branch_events = _parse_session_archive(value)
    return branch_events


class PiEventSourceAdapter:
    """Resolve and read captured Pi session event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=copy.deepcopy(value.record),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=copy.deepcopy(source.record),
        )


class PiEventTextEvidenceProjection:
    """Project one PiEventSource into canonical TextEvidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version="1",
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Incremental branch-aware connector for Pi v3 session JSONL files."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: Path | str, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Failed to read Pi session file {self.path}: {exc}") from exc  # noqa: TRY003

        session_id, by_id, parsed_events, branch_events = _parse_session_archive(
            PiSessionInput(content=raw, leaf_id=self.leaf_id)
        )

        imported_digests: dict[str, str] = {}
        if session.checkpoint is not None:
            if not isinstance(session.checkpoint, dict):
                raise ValueError("Invalid PiSessionConnector checkpoint format")  # noqa: TRY003
            prev_session_id = session.checkpoint.get("session_id")
            if prev_session_id != session_id:
                raise ValueError("Pi session ID does not match persisted checkpoint")  # noqa: TRY003
            raw_events = session.checkpoint.get("events", {})
            if not isinstance(raw_events, dict):
                raise ValueError("Invalid events map in PiSessionConnector checkpoint")  # noqa: TRY003
            for event_id, expected_digest in raw_events.items():
                if not isinstance(event_id, str) or not isinstance(expected_digest, str):
                    raise ValueError("Invalid event digest entry in checkpoint")  # noqa: TRY003
                if event_id not in by_id:
                    raise ValueError(f"Previously imported event {event_id!r} was removed from session")  # noqa: TRY003
                current_event = parsed_events.get(event_id)
                if current_event is None:
                    raise ValueError(f"Previously imported event {event_id!r} is no longer a visible event")  # noqa: TRY003
                current_digest = _canonical_json_sha256(current_event.model_dump(mode="json"))
                if current_digest != expected_digest:
                    raise ValueError(f"Previously imported event {event_id!r} was modified")  # noqa: TRY003
                imported_digests[event_id] = expected_digest

        for event in branch_events:
            digest = _canonical_json_sha256(event.model_dump(mode="json"))
            if event.event_id in imported_digests:
                if imported_digests[event.event_id] != digest:
                    raise ValueError(f"Previously imported event {event.event_id!r} was modified")  # noqa: TRY003
                continue
            try:
                await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            except SourceConflictError as exc:
                raise ValueError(f"Conflicting persisted event {event.event_id!r}: {exc}") from exc  # noqa: TRY003
            imported_digests[event.event_id] = digest

        next_checkpoint: JsonValue = {
            "session_id": session_id,
            "events": dict(sorted(imported_digests.items())),
        }
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=next_checkpoint,
        )


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
