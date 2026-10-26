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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class PreparedEvidenceFragment:
    """One literal substring selected from a recalled Memory entry."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedEvidenceFragment, ...] = ()


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        if request.evidence_selection:
            content, origins, omissions, fragments = self._select_evidence_fragments(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )
            if content is None:
                return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions, fragments=())
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > request.max_bytes:
                raise PreparedContextInvariantError("output-budget")
            return PreparedContextBuild(
                context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
                origins=origins,
                omissions=omissions,
                fragments=fragments,
            )

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        if request.evidence_selection:
            content, origins, omissions, _ = self._select_evidence_fragments(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )
        else:
            content, origins, omissions = self._select_entries(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)

    def _select_evidence_fragments(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> tuple[
        str | None,
        tuple[PreparedContextOrigin, ...],
        PreparedContextOmissions,
        tuple[PreparedEvidenceFragment, ...],
    ]:
        grouped_candidates: list[list[_EvidenceDocCandidate]] = []
        for group in memory_candidates:
            if group.hits and group.memory_ref is None:
                raise PreparedContextInvariantError("memory-ref-missing")
            effective_scope = group.scope_id if group.scope_id else (current_scope_id or "")
            seen_in_group: set[tuple[str, str]] = set()
            group_list: list[_EvidenceDocCandidate] = []
            for hit in group.hits:
                if hit.memory_ref != group.memory_ref:
                    raise PreparedContextInvariantError("memory-ref-mismatch")
                key = (hit.entry_id, hit.entry_version_id)
                if key in seen_in_group:
                    continue
                seen_in_group.add(key)
                if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                    continue
                address = MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=effective_scope, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                group_list.append(
                    _EvidenceDocCandidate(
                        doc_index=0,
                        address=address,
                        text=hit.text,
                        hit_score=float(hit.score),
                    )
                )
            if group_list:
                grouped_candidates.append(group_list)

        interleaved = _interleave_groups(grouped_candidates)
        if not interleaved:
            return None, (), PreparedContextOmissions(), ()

        docs = [
            replace(candidate, doc_index=idx)
            for idx, candidate in enumerate(interleaved)
        ]
        fragments, omissions = _pack_evidence_fragments(
            query=request.query,
            docs=docs,
            max_bytes=request.max_bytes,
            entry_limit=self.entry_limit,
        )
        if not fragments:
            return None, (), omissions, ()
        content = _render_evidence_fragments(fragments)
        origins = tuple(fragment.address for fragment in fragments)
        return content, origins, omissions, fragments


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"


_EVIDENCE_WORD_RE = re.compile(r"[a-z]+|\d+")
_EVIDENCE_CJK_RE = re.compile(r"[一-鿿]")
_ABBREVIATIONS = frozenset(
    {
        "al",
        "ca",
        "cf",
        "dr",
        "e.g",
        "eq",
        "etc",
        "fig",
        "figs",
        "i.e",
        "inc",
        "ltd",
        "mr",
        "mrs",
        "ms",
        "no",
        "nos",
        "pp",
        "prof",
        "ref",
        "refs",
        "sp",
        "st",
        "subsp",
        "var",
        "vol",
        "vs",
    }
)
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "may",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "myself",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)
_SUFFIXES = (
    "ational",
    "ization",
    "fulness",
    "ousness",
    "iveness",
    "ations",
    "itions",
    "ments",
    "ities",
    "ances",
    "ences",
    "ation",
    "ition",
    "ingly",
    "edly",
    "ness",
    "ment",
    "able",
    "ible",
    "ance",
    "ence",
    "tive",
    "sive",
    "ally",
    "ical",
    "ular",
    "less",
    "ious",
    "eous",
    "ies",
    "ing",
    "ion",
    "ive",
    "ous",
    "ful",
    "ity",
    "al",
    "ic",
    "er",
    "or",
    "ed",
    "es",
    "ly",
    "s",
)


@dataclass(frozen=True)
class _EvidenceDocCandidate:
    doc_index: int
    address: MemoryEntryAddress
    text: str
    hit_score: float


@dataclass
class _SentenceSpan:
    doc_index: int
    sent_index: int
    start: int
    end: int
    text: str
    score: float
    covered_terms: set[str]
    is_duplicate: bool


def _stem_token(word: str) -> str:
    if word.isdigit() or len(word) <= 3:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            if suffix == "ies":
                return stem + "y"
            if len(stem) >= 4 and stem[-1] == stem[-2]:
                stem = stem[:-1]
            return stem
    return word


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = "".join(_EVIDENCE_WORD_RE.findall(text.lower()))
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _split_into_sentence_spans(text: str) -> list[tuple[int, int]]:
    length = len(text)
    if length == 0 or not text.strip():
        return []

    starts: list[int] = [0]
    i = 0
    while i < length:
        ch = text[i]
        if ch in "\n\r":
            j = i + 1
            while j < length and text[j].isspace():
                j += 1
            if j < length and j != starts[-1]:
                starts.append(j)
            i = j
            continue

        if ch in "。！？":
            j = i + 1
            while j < length and text[j] in "”’\"')）】 \t":
                j += 1
            if j < length and j != starts[-1]:
                starts.append(j)
            i = j
            continue

        if ch in ".!?":
            prev_word_end = i
            prev_word_start = i
            while prev_word_start > 0 and (text[prev_word_start - 1].isalpha() or text[prev_word_start - 1] == "."):
                prev_word_start -= 1
            prev_word = text[prev_word_start:prev_word_end].lower().strip(".")
            is_abbrev = prev_word in _ABBREVIATIONS or (len(prev_word) == 1 and text[prev_word_start].isupper())

            j = i + 1
            while j < length and text[j] in "\"')]}":
                j += 1
            if not is_abbrev and j < length and text[j].isspace():
                while j < length and text[j].isspace():
                    j += 1
                if j < length and (
                    text[j].isupper()
                    or text[j].isdigit()
                    or text[j] in "([\"'<"
                    or "一" <= text[j] <= "鿿"
                ):
                    if j != starts[-1]:
                        starts.append(j)
                    i = j
                    continue
        i += 1

    spans: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else length
        if start < end and text[start:end].strip():
            spans.append((start, end))
    if not spans:
        return [(0, length)]
    return spans


def _render_evidence_fragments(fragments: Sequence[PreparedEvidenceFragment]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [
            {
                "citation": {
                    "scope_id": fragment.address.memory.scope_id,
                    "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                    "entry_id": fragment.address.entry_id,
                    "entry_version_id": fragment.address.entry_version_id,
                },
                "content": fragment.text,
                "start": fragment.start,
                "end": fragment.end,
            }
            for fragment in fragments
        ],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _rendered_fragments_bytes(fragments: Sequence[PreparedEvidenceFragment]) -> int:
    if not fragments:
        return 0
    return len(_render_evidence_fragments(fragments).encode("utf-8"))


def _build_doc_fragments(
    doc: _EvidenceDocCandidate,
    selected_sent_indices: set[int],
    sent_spans: list[_SentenceSpan],
) -> list[PreparedEvidenceFragment]:
    if not selected_sent_indices:
        return []
    ordered = sorted(selected_sent_indices)
    intervals: list[tuple[int, int]] = []
    for idx in ordered:
        span = sent_spans[idx]
        if not intervals:
            intervals.append((span.start, span.end))
        else:
            prev_start, prev_end = intervals[-1]
            if span.start <= prev_end or not doc.text[prev_end : span.start].strip():
                intervals[-1] = (prev_start, max(prev_end, span.end))
            else:
                intervals.append((span.start, span.end))
    return [
        PreparedEvidenceFragment(
            address=doc.address,
            start=start,
            end=end,
            text=doc.text[start:end],
        )
        for start, end in intervals
    ]


def _fit_window_within_span(
    doc: _EvidenceDocCandidate,
    span_start: int,
    span_end: int,
    query_words: list[str],
    query_cjk: list[str],
    existing_fragments: list[PreparedEvidenceFragment],
    max_bytes: int,
) -> PreparedEvidenceFragment | None:
    full_frag = PreparedEvidenceFragment(
        address=doc.address,
        start=span_start,
        end=span_end,
        text=doc.text[span_start:span_end],
    )
    if _rendered_fragments_bytes([*existing_fragments, full_frag]) <= max_bytes:
        return full_frag

    sub_text = doc.text[span_start:span_end]
    sub_lower = sub_text.lower()
    anchor_pos = 0
    for term in query_words + query_cjk:
        pos = sub_lower.find(term)
        if pos != -1:
            anchor_pos = pos
            break

    char_len = span_end - span_start
    if char_len < 1:
        return None

    minimal = PreparedEvidenceFragment(
        address=doc.address,
        start=span_start + anchor_pos,
        end=min(span_end, span_start + anchor_pos + 1),
        text=doc.text[span_start + anchor_pos : min(span_end, span_start + anchor_pos + 1)],
    )
    if _rendered_fragments_bytes([*existing_fragments, minimal]) > max_bytes:
        return None

    low = 1
    high = char_len
    best_frag = minimal
    while low <= high:
        mid = (low + high) // 2
        half = mid // 2
        w_start = max(0, min(char_len - mid, anchor_pos - half))
        w_end = w_start + mid
        abs_start = span_start + w_start
        abs_end = span_start + w_end
        cand = PreparedEvidenceFragment(
            address=doc.address,
            start=abs_start,
            end=abs_end,
            text=doc.text[abs_start:abs_end],
        )
        if _rendered_fragments_bytes([*existing_fragments, cand]) <= max_bytes:
            best_frag = cand
            low = mid + 1
        else:
            high = mid - 1
    return best_frag


def _pack_evidence_fragments(
    *,
    query: str,
    docs: list[_EvidenceDocCandidate],
    max_bytes: int,
    entry_limit: int,
) -> tuple[tuple[PreparedEvidenceFragment, ...], PreparedContextOmissions]:
    q_words_all = _EVIDENCE_WORD_RE.findall(query.lower())
    q_cjk_chars = _EVIDENCE_CJK_RE.findall(query)
    q_cjk_bigrams = [q_cjk_chars[i] + q_cjk_chars[i + 1] for i in range(len(q_cjk_chars) - 1)]
    q_content_words = [w for w in q_words_all if w not in _STOPWORDS and (len(w) >= 2 or w.isdigit())]
    if not q_content_words:
        q_content_words = list(q_words_all)
    q_unique_words = list(dict.fromkeys(q_words_all))
    q_content_stems = [_stem_token(w) for w in q_content_words]
    q_4grams = _char_ngrams(query, 4)

    doc_sentences: dict[int, list[_SentenceSpan]] = {}
    all_spans: list[_SentenceSpan] = []

    for doc in docs:
        raw_spans = _split_into_sentence_spans(doc.text)
        seen_norm: set[str] = set()
        spans_for_doc: list[_SentenceSpan] = []
        for s_idx, (s_start, s_end) in enumerate(raw_spans):
            s_text = doc.text[s_start:s_end]
            norm = " ".join(s_text.split()).lower()
            is_dup = norm in seen_norm
            seen_norm.add(norm)
            span_obj = _SentenceSpan(
                doc_index=doc.doc_index,
                sent_index=s_idx,
                start=s_start,
                end=s_end,
                text=s_text,
                score=0.0,
                covered_terms=set(),
                is_duplicate=is_dup,
            )
            spans_for_doc.append(span_obj)
            all_spans.append(span_obj)
        doc_sentences[doc.doc_index] = spans_for_doc

    total_sents = max(len(all_spans), 1)
    df_word: dict[str, int] = {w: 0 for w in q_unique_words}
    df_stem: dict[str, int] = {_stem_token(w): 0 for w in q_unique_words}
    df_cjk: dict[str, int] = {c: 0 for c in set(q_cjk_chars + q_cjk_bigrams)}

    sent_features: list[tuple[list[str], set[str], set[str], set[str], set[str]]] = []
    for span in all_spans:
        s_words = _EVIDENCE_WORD_RE.findall(span.text.lower())
        s_word_set = set(s_words)
        s_stem_set = {_stem_token(w) for w in s_words}
        s_prefix_set = {w[:5] for w in s_words if len(w) >= 5}
        s_4grams = _char_ngrams(span.text, 4)
        sent_features.append((s_words, s_word_set, s_stem_set, s_prefix_set, s_4grams))
        for w in q_unique_words:
            if w in s_word_set:
                df_word[w] += 1
            st = _stem_token(w)
            if st in s_stem_set:
                df_stem[st] = df_stem.get(st, 0) + 1
        for c in df_cjk:
            if c in span.text:
                df_cjk[c] += 1

    for span, (s_words, s_word_set, s_stem_set, s_prefix_set, s_4grams) in zip(
        all_spans, sent_features, strict=True
    ):
        score = 0.0
        covered: set[str] = set()
        for w in q_unique_words:
            st = _stem_token(w)
            is_stop = w in _STOPWORDS and len(q_content_words) > 0 and w not in q_content_words
            base_weight = 0.15 if is_stop else (1.6 if w.isdigit() else 1.0)
            idf_w = math.log(1.0 + (total_sents + 1.0) / (df_word.get(w, 0) + 0.5))
            idf_st = math.log(1.0 + (total_sents + 1.0) / (df_stem.get(st, 0) + 0.5))
            if w in s_word_set:
                tf = s_words.count(w)
                score += base_weight * idf_w * (1.0 + 0.2 * min(tf - 1, 2))
                if not is_stop:
                    covered.add(st)
            elif st in s_stem_set:
                score += base_weight * 0.82 * idf_st
                if not is_stop:
                    covered.add(st)
            elif len(w) >= 5 and w[:5] in s_prefix_set:
                score += base_weight * 0.65 * idf_st
                if not is_stop:
                    covered.add(st)

        for c in set(q_cjk_chars):
            if c in span.text:
                idf_c = math.log(1.0 + (total_sents + 1.0) / (df_cjk.get(c, 0) + 0.5))
                score += 1.0 * idf_c
                covered.add(c)
        for bg in set(q_cjk_bigrams):
            if bg in span.text:
                idf_bg = math.log(1.0 + (total_sents + 1.0) / (df_cjk.get(bg, 0) + 0.5))
                score += 1.4 * idf_bg
                covered.add(bg)

        for i in range(len(q_content_stems) - 1):
            if q_content_stems[i] in s_stem_set and q_content_stems[i + 1] in s_stem_set:
                score += 0.65

        if q_4grams and s_4grams:
            overlap_4 = len(q_4grams & s_4grams) / len(q_4grams)
            score += 0.45 * overlap_4

        span.score = score
        span.covered_terms = covered

    doc_scores: dict[int, float] = {}
    doc_covered: dict[int, set[str]] = {}
    for doc in docs:
        spans = [s for s in doc_sentences[doc.doc_index] if not s.is_duplicate]
        if not spans:
            spans = doc_sentences[doc.doc_index]
        top_scores = sorted((s.score for s in spans), reverse=True)[:3]
        cov: set[str] = set()
        for s in spans:
            cov.update(s.covered_terms)
        doc_covered[doc.doc_index] = cov
        doc_scores[doc.doc_index] = (
            1.8 * len(cov)
            + sum(top_scores)
            + 0.05 / (1.0 + doc.doc_index)
        )

    ranked_docs = sorted(docs, key=lambda d: (-doc_scores[d.doc_index], d.doc_index))
    selected_by_doc: dict[int, set[int]] = {d.doc_index: set() for d in docs}

    def _current_fragments() -> list[PreparedEvidenceFragment]:
        frags: list[PreparedEvidenceFragment] = []
        for d in ranked_docs:
            sel = selected_by_doc[d.doc_index]
            if sel:
                frags.extend(_build_doc_fragments(d, sel, doc_sentences[d.doc_index]))
        return frags[:entry_limit]

    def _try_select(doc_idx: int, sent_indices: Sequence[int]) -> bool:
        current_set = selected_by_doc[doc_idx]
        added = [idx for idx in sent_indices if idx not in current_set]
        if not added:
            return True
        current_set.update(added)
        frags = _current_fragments()
        if len(frags) <= entry_limit and _rendered_fragments_bytes(frags) <= max_bytes:
            return True
        current_set.difference_update(added)
        return False

    primary_doc = ranked_docs[0]
    primary_spans = doc_sentences[primary_doc.doc_index]
    unique_primary_spans = [s for s in primary_spans if not s.is_duplicate] or primary_spans

    best_primary_span = max(
        unique_primary_spans,
        key=lambda s: (s.score, -s.sent_index),
    )

    if not _try_select(primary_doc.doc_index, (best_primary_span.sent_index,)):
        trimmed = _fit_window_within_span(
            primary_doc,
            best_primary_span.start,
            best_primary_span.end,
            q_content_words,
            q_cjk_chars,
            [],
            max_bytes,
        )
        if trimmed is None:
            return (), PreparedContextOmissions(
                truncated_items=0,
                dropped_items=len(docs),
                dropped_no_fitting_truncation=len(docs),
            )
        return (trimmed,), PreparedContextOmissions(
            truncated_items=1,
            dropped_items=max(0, len(docs) - 1),
            dropped_no_fitting_truncation=max(0, len(docs) - 1),
        )

    global_covered = set(best_primary_span.covered_terms)

    # Select additional high-scoring / complementary sentences in primary_doc
    max_s_score = max(best_primary_span.score, 1e-6)
    for cand_span in sorted(
        unique_primary_spans,
        key=lambda s: (len(s.covered_terms - global_covered), s.score, -abs(s.sent_index - best_primary_span.sent_index)),
        reverse=True,
    ):
        if cand_span.sent_index in selected_by_doc[primary_doc.doc_index]:
            continue
        new_terms = cand_span.covered_terms - global_covered
        if not new_terms and cand_span.score < 0.30 * max_s_score:
            continue
        if _try_select(primary_doc.doc_index, (cand_span.sent_index,)):
            global_covered.update(cand_span.covered_terms)

    # Check if any secondary document covers query terms that primary_doc missed entirely
    for sec_doc in ranked_docs[1:]:
        sec_cov = doc_covered[sec_doc.doc_index]
        missing_terms = sec_cov - doc_covered[primary_doc.doc_index]
        if not missing_terms and doc_scores[sec_doc.doc_index] < 0.80 * doc_scores[primary_doc.doc_index]:
            continue
        sec_spans = [s for s in doc_sentences[sec_doc.doc_index] if not s.is_duplicate and s.score > 0]
        if not sec_spans:
            continue
        best_sec = max(
            sec_spans,
            key=lambda s: (len(s.covered_terms - global_covered), s.score, -s.sent_index),
        )
        if best_sec.covered_terms - global_covered or (
            best_sec.score >= 0.70 * best_primary_span.score and best_sec.score > 0.5
        ):
            if _try_select(sec_doc.doc_index, (best_sec.sent_index,)):
                global_covered.update(best_sec.covered_terms)

    # Bridge small gaps and expand within primary_doc (and any active doc) when prose is non-repetitive
    for target_doc in ranked_docs:
        t_idx = target_doc.doc_index
        if not selected_by_doc[t_idx]:
            continue
        t_spans = doc_sentences[t_idx]
        t_unique = [s for s in t_spans if not s.is_duplicate]
        is_natural_prose = len(t_unique) >= 0.7 * len(t_spans) and len(t_spans) <= 30

        # Bridge gaps between already-selected sentences in target_doc
        changed = True
        while changed:
            changed = False
            sel_sorted = sorted(selected_by_doc[t_idx])
            for idx_pos in range(len(sel_sorted) - 1):
                left_s = sel_sorted[idx_pos]
                right_s = sel_sorted[idx_pos + 1]
                if right_s - left_s <= 1:
                    continue
                gap_indices = list(range(left_s + 1, right_s))
                if any(t_spans[g].is_duplicate for g in gap_indices):
                    continue
                gap_bytes = len(target_doc.text[t_spans[left_s].end : t_spans[right_s].start].encode("utf-8"))
                if (gap_bytes <= 260 or is_natural_prose) and _try_select(t_idx, gap_indices):
                    changed = True
                    break

        if not is_natural_prose:
            # For repetitive synthetic text, only add remaining non-duplicate sentences with positive score
            for cand_span in sorted(
                (s for s in t_unique if s.score > 0 and s.sent_index not in selected_by_doc[t_idx]),
                key=lambda s: (-s.score, s.sent_index),
            ):
                _try_select(t_idx, (cand_span.sent_index,))
            continue

        # For natural prose (e.g., SciFact abstracts), greedily expand contiguous / high-scoring sentences
        anchor_idx = max(
            (t_spans[i] for i in selected_by_doc[t_idx]),
            key=lambda s: (s.score, -s.sent_index),
        ).sent_index
        while True:
            sel_set = selected_by_doc[t_idx]
            remaining = [s for s in t_unique if s.sent_index not in sel_set]
            if not remaining:
                break

            def _expansion_priority(s: _SentenceSpan, current_sel: set[int] = sel_set) -> tuple[float, int]:
                adjacent = (s.sent_index - 1 in current_sel) or (s.sent_index + 1 in current_sel)
                both_sides = (s.sent_index - 1 in current_sel) and (s.sent_index + 1 in current_sel)
                dist = abs(s.sent_index - anchor_idx)
                after_bonus = 0.25 if s.sent_index >= anchor_idx else 0.0
                adj_bonus = 4.0 if both_sides else (2.0 if adjacent else 0.0)
                return (s.score + adj_bonus + after_bonus - 0.05 * dist, -s.sent_index)

            best_added = False
            for cand_span in sorted(remaining, key=_expansion_priority, reverse=True):
                if _try_select(t_idx, (cand_span.sent_index,)):
                    best_added = True
                    break
            if not best_added:
                break

    final_fragments = tuple(_current_fragments())
    return final_fragments, PreparedContextOmissions()
