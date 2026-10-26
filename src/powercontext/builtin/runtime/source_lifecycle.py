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

"""Source lifecycle dependency graph, withdrawal propagation, and audit resolution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, Text, inspect, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.topic_memory.models import TopicMemorySearchChannels
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.runtime.application import BuiltinRuntime

_LIFECYCLE_METADATA = MetaData()

SOURCE_LIFECYCLE_NODES_TABLE = Table(
    "pc_source_lifecycle_nodes",
    _LIFECYCLE_METADATA,
    Column("scope_id", String(128), primary_key=True),
    Column("node_id", String(255), primary_key=True),
    Column("registration_order", Integer, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("reference_json", Text, nullable=False),
    Column("source_revision", Integer, nullable=True),
    Column("parents_json", Text, nullable=False),
    Column("policy", String(8), nullable=True),
    Column("available", Boolean, nullable=False),
    Column("last_sequence", Integer, nullable=False),
    Column("last_event_available", Boolean, nullable=True),
    Column("last_event_reason", Text, nullable=True),
)


class SourceLifecycleError(ValueError):
    """Raised when source lifecycle registration, event ordering, or resolution fails."""


class SourceLifecycleEvent(BaseModel):
    """Validated lifecycle event targeting a registered Source node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: StrictStr
    revision: StrictInt = Field(ge=1)
    sequence: StrictInt = Field(ge=1)
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def _validate_non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must be a non-empty, non-blank string")  # noqa: TRY003
        return value


@dataclass
class _LifecycleNodeRecord:
    scope_id: str
    node_id: str
    registration_order: int
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    source_revision: int | None
    parents: tuple[str, ...]
    policy: Literal["all", "any"] | None
    available: bool
    last_sequence: int
    last_event_available: bool | None
    last_event_reason: str | None


async def ensure_source_lifecycle_schema(connection: AsyncConnection, /) -> None:
    """Create the source lifecycle table if it does not already exist."""

    await connection.run_sync(lambda sync_conn: SOURCE_LIFECYCLE_NODES_TABLE.create(sync_conn, checkfirst=True))


async def _has_lifecycle_table(connection: AsyncConnection) -> bool:
    return await connection.run_sync(
        lambda sync_conn: inspect(sync_conn).has_table(SOURCE_LIFECYCLE_NODES_TABLE.name)
    )


async def filter_inactive_topic_memory_channels(
    connection: AsyncConnection,
    scope_id: str,
    channels: TopicMemorySearchChannels,
    /,
) -> TopicMemorySearchChannels:
    """Filter withdrawn Topic Memory revisions from search channels before RRF fusion."""

    if not await _has_lifecycle_table(connection):
        return channels

    rows = (
        await connection.execute(
            select(SOURCE_LIFECYCLE_NODES_TABLE.c.reference_json).where(
                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
            )
        )
    ).scalars()

    inactive_refs: set[tuple[str, int]] = set()
    inactive_artifact_ids: set[str] = set()
    for raw_json in rows:
        ref = ArtifactRef.model_validate_json(str(raw_json))
        inactive_refs.add((ref.artifact_id, ref.revision))
        inactive_artifact_ids.add(ref.artifact_id)

    if not inactive_refs:
        return channels

    def _keep(hit: object) -> bool:
        artifact_ref = getattr(hit, "artifact_ref", None)
        if not isinstance(artifact_ref, ArtifactRef):
            return True
        if (artifact_ref.artifact_id, artifact_ref.revision) in inactive_refs:
            return False
        return artifact_ref.artifact_id not in inactive_artifact_ids

    return TopicMemorySearchChannels(
        topic_fts=tuple(hit for hit in channels.topic_fts if _keep(hit)),
        topic_vector=tuple(hit for hit in channels.topic_vector if _keep(hit)),
        detail_fts=tuple(hit for hit in channels.detail_fts if _keep(hit)),
        detail_vector=tuple(hit for hit in channels.detail_vector if _keep(hit)),
        topic_fts_retrieved=channels.topic_fts_retrieved,
        topic_vector_retrieved=channels.topic_vector_retrieved,
        detail_fts_retrieved=channels.detail_fts_retrieved,
        detail_vector_retrieved=channels.detail_vector_retrieved,
    )


def _validate_node_id(node_id: object, field_name: str = "node_id") -> str:
    if not isinstance(node_id, str) or not node_id.strip() or node_id != node_id.strip():
        raise SourceLifecycleError(f"{field_name} must be a non-empty trimmed string")  # noqa: TRY003
    return node_id


def _validate_parents_and_policy(
    node_id: str,
    parents: object,
    policy: object,
) -> tuple[tuple[str, ...], Literal["all", "any"]]:
    if policy not in {"all", "any"}:
        raise SourceLifecycleError(f"Unsupported dependency policy: {policy!r}")  # noqa: TRY003
    if isinstance(parents, (str, bytes, bytearray)):
        raise SourceLifecycleError("parents must be a sequence of node_id strings")  # noqa: TRY003
    try:
        parent_list = list(parents)  # type: ignore[arg-type]
    except TypeError as exc:
        raise SourceLifecycleError("parents must be a sequence of node_id strings") from exc  # noqa: TRY003

    if not parent_list:
        raise SourceLifecycleError("Derived lifecycle node requires at least one parent")  # noqa: TRY003

    validated: list[str] = []
    seen: set[str] = set()
    for parent in parent_list:
        pid = _validate_node_id(parent, "parent")
        if pid == node_id:
            raise SourceLifecycleError(f"Self-dependency is not allowed for node {node_id!r}")  # noqa: TRY003
        if pid in seen:
            raise SourceLifecycleError(f"Duplicate parent {pid!r} is not allowed")  # noqa: TRY003
        seen.add(pid)
        validated.append(pid)

    return tuple(validated), ("all" if policy == "all" else "any")


def _deserialize_reference(
    kind: str,
    reference_json: str,
) -> SourceRef | MemoryCitation | ArtifactRef:
    if kind == "source":
        return SourceRef.model_validate_json(reference_json)
    if kind == "memory":
        return MemoryCitation.model_validate_json(reference_json)
    if kind == "topic":
        return ArtifactRef.model_validate_json(reference_json)
    raise SourceLifecycleError(f"Corrupted lifecycle node kind: {kind!r}")  # noqa: TRY003


def _would_introduce_cycle(
    node_id: str,
    parents: tuple[str, ...],
    existing: dict[str, _LifecycleNodeRecord],
) -> bool:
    stack = list(parents)
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current == node_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        record = existing.get(current)
        if record is not None:
            stack.extend(record.parents)
    return False


def _topological_order(nodes: dict[str, _LifecycleNodeRecord]) -> list[_LifecycleNodeRecord]:
    ordered_by_reg = sorted(nodes.values(), key=lambda item: (item.registration_order, item.node_id))
    visited: set[str] = set()
    result: list[_LifecycleNodeRecord] = []

    def _visit(record: _LifecycleNodeRecord) -> None:
        if record.node_id in visited:
            return
        visited.add(record.node_id)
        for parent_id in record.parents:
            parent_rec = nodes.get(parent_id)
            if parent_rec is not None and parent_rec.node_id not in visited:
                _visit(parent_rec)
        result.append(record)

    for rec in ordered_by_reg:
        _visit(rec)
    return result


class ScopedSourceLifecycleApplication:
    """Manage source lifecycle DAG registrations, propagation, and resolution in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        try:
            self.scope_id = validate_scope_id(scope_id)
        except Exception as exc:
            raise SourceLifecycleError(f"Invalid scope_id: {scope_id!r}") from exc  # noqa: TRY003

    def _database(self) -> AsyncDatabase:
        provider = getattr(self._runtime, "_provider", None)
        database = getattr(provider, "database", None)
        if database is None:
            raise SourceLifecycleError("Runtime does not provide a relational database for source lifecycle")  # noqa: TRY003
        return database

    async def _load_nodes(self, connection: AsyncConnection) -> dict[str, _LifecycleNodeRecord]:
        await ensure_source_lifecycle_schema(connection)
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                .order_by(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.registration_order.asc(),
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id.asc(),
                )
            )
        ).mappings()
        nodes: dict[str, _LifecycleNodeRecord] = {}
        for row in rows:
            kind = str(row["kind"])
            if kind not in {"source", "memory", "topic"}:
                raise SourceLifecycleError(f"Unexpected node kind: {kind!r}")  # noqa: TRY003
            parents_raw = json.loads(str(row["parents_json"]))
            policy_raw = row["policy"]
            nodes[str(row["node_id"])] = _LifecycleNodeRecord(
                scope_id=str(row["scope_id"]),
                node_id=str(row["node_id"]),
                registration_order=int(row["registration_order"]),
                kind=kind,  # type: ignore[arg-type]
                reference=_deserialize_reference(kind, str(row["reference_json"])),
                source_revision=None if row["source_revision"] is None else int(row["source_revision"]),
                parents=tuple(str(item) for item in parents_raw),
                policy=None if policy_raw is None else ("all" if str(policy_raw) == "all" else "any"),
                available=bool(row["available"]),
                last_sequence=int(row["last_sequence"]),
                last_event_available=(
                    None if row["last_event_available"] is None else bool(row["last_event_available"])
                ),
                last_event_reason=None if row["last_event_reason"] is None else str(row["last_event_reason"]),
            )
        return nodes

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        *,
        revision: int,
    ) -> None:
        validated_id = _validate_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")  # noqa: TRY003
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")  # noqa: TRY003

        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type,
                source_ref.source_id,
            )
        except Exception as exc:
            raise SourceLifecycleError(
                f"Source {source_ref} does not exist or is not readable in scope {self.scope_id!r}"
            ) from exc  # noqa: TRY003

        database = self._database()
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            database.transaction() as connection,
        ):
            nodes = await self._load_nodes(connection)
            existing = nodes.get(validated_id)
            if existing is not None:
                if (
                    existing.kind == "source"
                    and existing.reference == source_ref
                    and existing.source_revision == revision
                ):
                    return
                raise SourceLifecycleError(f"Conflicting lifecycle registration for node {validated_id!r}")  # noqa: TRY003

            next_order = max((item.registration_order for item in nodes.values()), default=0) + 1
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=validated_id,
                    registration_order=next_order,
                    kind="source",
                    reference_json=source_ref.model_dump_json(),
                    source_revision=revision,
                    parents_json="[]",
                    policy=None,
                    available=True,
                    last_sequence=0,
                    last_event_available=None,
                    last_event_reason=None,
                )
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: Sequence[str],
        policy: Literal["all", "any"],
    ) -> None:
        validated_id = _validate_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")  # noqa: TRY003
        parents_tuple, validated_policy = _validate_parents_and_policy(validated_id, parents, policy)

        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
            entries_page = await self._runtime.memory.for_scope(self.scope_id).list(include_inactive=True)
            if not any(
                record.entry.entry_id == citation.entry_id
                and record.entry.entry_version_id == citation.entry_version_id
                for record in entries_page.entries
            ):
                raise SourceLifecycleError("Cited memory entry version is not present in scope Memory head")  # noqa: TRY003
        except SourceLifecycleError:
            raise
        except Exception as exc:
            raise SourceLifecycleError(
                f"Memory citation {citation} does not exist or is not readable in scope {self.scope_id!r}"
            ) from exc  # noqa: TRY003

        database = self._database()
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            database.transaction() as connection,
        ):
            nodes = await self._load_nodes(connection)
            existing = nodes.get(validated_id)
            if existing is not None:
                if (
                    existing.kind == "memory"
                    and existing.reference == citation
                    and existing.parents == parents_tuple
                    and existing.policy == validated_policy
                ):
                    desired_nodes = nodes
                else:
                    raise SourceLifecycleError(
                        f"Conflicting lifecycle registration for node {validated_id!r}"
                    )  # noqa: TRY003
            else:
                for parent_id in parents_tuple:
                    if parent_id not in nodes:
                        raise SourceLifecycleError(f"Parent node {parent_id!r} is not registered")  # noqa: TRY003
                if _would_introduce_cycle(validated_id, parents_tuple, nodes):
                    raise SourceLifecycleError(f"Cycle detected when registering node {validated_id!r}")  # noqa: TRY003

                parent_states = [nodes[parent_id].available for parent_id in parents_tuple]
                initial_available = all(parent_states) if validated_policy == "all" else any(parent_states)
                next_order = max((item.registration_order for item in nodes.values()), default=0) + 1
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=validated_id,
                        registration_order=next_order,
                        kind="memory",
                        reference_json=citation.model_dump_json(),
                        source_revision=None,
                        parents_json=json.dumps(list(parents_tuple)),
                        policy=validated_policy,
                        available=initial_available,
                        last_sequence=0,
                        last_event_available=None,
                        last_event_reason=None,
                    )
                )
                nodes[validated_id] = _LifecycleNodeRecord(
                    scope_id=self.scope_id,
                    node_id=validated_id,
                    registration_order=next_order,
                    kind="memory",
                    reference=citation,
                    source_revision=None,
                    parents=parents_tuple,
                    policy=validated_policy,
                    available=initial_available,
                    last_sequence=0,
                    last_event_available=None,
                    last_event_reason=None,
                )
                desired_nodes = nodes

        await self._reconcile_memory_states(desired_nodes, reason="source lifecycle registration")

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: Sequence[str],
        policy: Literal["all", "any"],
    ) -> None:
        validated_id = _validate_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("artifact_ref must be a topic-memory ArtifactRef")  # noqa: TRY003
        parents_tuple, validated_policy = _validate_parents_and_policy(validated_id, parents, policy)

        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family,
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
        except Exception as exc:
            raise SourceLifecycleError(
                f"Topic Memory {artifact_ref} does not exist or is not readable in scope {self.scope_id!r}"
            ) from exc  # noqa: TRY003

        database = self._database()
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            database.transaction() as connection,
        ):
            nodes = await self._load_nodes(connection)
            existing = nodes.get(validated_id)
            if existing is not None:
                if (
                    existing.kind == "topic"
                    and existing.reference == artifact_ref
                    and existing.parents == parents_tuple
                    and existing.policy == validated_policy
                ):
                    return
                raise SourceLifecycleError(f"Conflicting lifecycle registration for node {validated_id!r}")  # noqa: TRY003

            for parent_id in parents_tuple:
                if parent_id not in nodes:
                    raise SourceLifecycleError(f"Parent node {parent_id!r} is not registered")  # noqa: TRY003
            if _would_introduce_cycle(validated_id, parents_tuple, nodes):
                raise SourceLifecycleError(f"Cycle detected when registering node {validated_id!r}")  # noqa: TRY003

            parent_states = [nodes[parent_id].available for parent_id in parents_tuple]
            initial_available = all(parent_states) if validated_policy == "all" else any(parent_states)
            next_order = max((item.registration_order for item in nodes.values()), default=0) + 1
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=validated_id,
                    registration_order=next_order,
                    kind="topic",
                    reference_json=artifact_ref.model_dump_json(),
                    source_revision=None,
                    parents_json=json.dumps(list(parents_tuple)),
                    policy=validated_policy,
                    available=initial_available,
                    last_sequence=0,
                    last_event_available=None,
                    last_event_reason=None,
                )
            )

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")  # noqa: TRY003

        database = self._database()
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            database.transaction() as connection,
        ):
            nodes = await self._load_nodes(connection)
            target = nodes.get(event.node_id)
            if target is None or target.kind != "source":
                raise SourceLifecycleError(f"Unknown source node {event.node_id!r}")  # noqa: TRY003
            if target.source_revision != event.revision:
                raise SourceLifecycleError(
                    f"Source revision mismatch for {event.node_id!r}: "
                    f"expected {target.source_revision}, got {event.revision}"
                )  # noqa: TRY003
            if event.sequence < target.last_sequence:
                raise SourceLifecycleError(
                    f"Stale sequence {event.sequence} for source {event.node_id!r} "
                    f"(last_sequence={target.last_sequence})"
                )  # noqa: TRY003
            if event.sequence == target.last_sequence and (
                target.last_event_available != event.available or target.last_event_reason != event.reason
            ):
                raise SourceLifecycleError(
                    f"Conflicting event at sequence {event.sequence} for source {event.node_id!r}"
                )  # noqa: TRY003

            target.available = event.available
            target.last_sequence = event.sequence
            target.last_event_available = event.available
            target.last_event_reason = event.reason
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == target.node_id,
                )
                .values(
                    available=target.available,
                    last_sequence=target.last_sequence,
                    last_event_available=target.last_event_available,
                    last_event_reason=target.last_event_reason,
                )
            )

            for record in _topological_order(nodes):
                if record.kind == "source":
                    continue
                parent_states = [nodes[parent_id].available for parent_id in record.parents]
                desired = all(parent_states) if record.policy == "all" else any(parent_states)
                if desired != record.available:
                    record.available = desired
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == record.node_id,
                        )
                        .values(available=desired)
                    )

            active_ids = sorted(record.node_id for record in nodes.values() if record.available)

        await self._reconcile_memory_states(nodes, reason=event.reason)
        return active_ids

    async def resolve(
        self,
        node_id: str,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        validated_id = _validate_node_id(node_id)
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError(f"Unsupported resolution purpose: {purpose!r}")  # noqa: TRY003

        database = self._database()
        async with database.transaction() as connection:
            nodes = await self._load_nodes(connection)
            record = nodes.get(validated_id)
            if record is None:
                raise SourceLifecycleError(f"Unknown lifecycle node {validated_id!r}")  # noqa: TRY003
            if purpose == "generation" and not record.available:
                raise SourceLifecycleError(
                    f"Lifecycle node {validated_id!r} is withdrawn and unavailable for generation"
                )  # noqa: TRY003
            return record.reference

    async def _reconcile_memory_states(
        self,
        nodes: dict[str, _LifecycleNodeRecord],
        *,
        reason: str,
    ) -> None:
        memory_nodes = [record for record in _topological_order(nodes) if record.kind == "memory"]
        if not memory_nodes:
            return

        async with (
            self._runtime._context(
                self.scope_id,
                embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
            ) as context,
            self._runtime._locked(self.scope_id),
        ):
            service = context.artifacts.memory
            try:
                current = await service.head(context.artifacts.memory_artifact_id)
            except Exception:
                current = None
            if current is None:
                return

            manifest_by_key = {
                (item.entry_id, item.entry_version_id): item for item in current.content.manifest.entries
            }
            entries_by_key = {
                (entry.entry_id, entry.entry_version_id): entry for entry in await service.entries(current)
            }

            to_forget = []
            to_reactivate = []
            for record in memory_nodes:
                citation = record.reference
                if not isinstance(citation, MemoryCitation):
                    continue
                key = (citation.entry_id, citation.entry_version_id)
                manifest_item = manifest_by_key.get(key)
                entry_version = entries_by_key.get(key)
                if manifest_item is None or entry_version is None:
                    raise SourceLifecycleError(
                        f"Memory entry version {key} for node {record.node_id!r} is missing from current head"
                    )  # noqa: TRY003
                if not record.available and manifest_item.state == "active":
                    to_forget.append(entry_version)
                elif record.available and manifest_item.state == "inactive":
                    to_reactivate.append(entry_version)

            if to_forget:
                current = await service.forget(current, entries=tuple(to_forget), reason=reason)
            if to_reactivate:
                current = await service.reactivate(current, entries=tuple(to_reactivate), reason=reason)


class SourceLifecycleApplication:
    """Select the scoped Source lifecycle application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleApplication:
        return ScopedSourceLifecycleApplication(self._runtime, scope_id)


__all__ = [
    "SOURCE_LIFECYCLE_NODES_TABLE",
    "ScopedSourceLifecycleApplication",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "ensure_source_lifecycle_schema",
    "filter_inactive_topic_memory_channels",
]
