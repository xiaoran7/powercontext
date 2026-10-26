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

"""HTTP cross-encoder reranker adapter for Memory search candidates."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


def _validate_non_empty_str(field: str, value: object) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003
    return value


class HTTPMemoryReranker:
    """Batch and globally rerank Memory candidates using an HTTP scoring endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        self.endpoint = _validate_non_empty_str("endpoint", endpoint)
        if not isinstance(client, httpx.AsyncClient):
            raise ValueError("client must be an httpx.AsyncClient instance")  # noqa: TRY003
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = _validate_non_empty_str("policy_id", policy_id)
        self.batch_size = batch_size
        self._client = client

    async def rerank(
        self,
        query: str,
        candidates: Sequence[MemoryHit],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score candidates in batches and select the top ``limit`` global ranks."""

        _validate_non_empty_str("query", query)
        candidate_tuple = tuple(candidates)
        candidate_count = len(candidate_tuple)
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or limit < 1 or limit > candidate_count:
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        indexed_candidates: list[tuple[int, str]] = []
        for rank, candidate in enumerate(candidate_tuple, start=1):
            if not isinstance(candidate, MemoryHit) or not isinstance(candidate.text, str) or not candidate.text:
                raise ValueError("each candidate must be a MemoryHit with non-empty text")  # noqa: TRY003
            indexed_candidates.append((rank, candidate.text))

        global_scores: dict[int, float] = {}
        request_count = 0

        for start in range(0, candidate_count, self.batch_size):
            batch = indexed_candidates[start : start + self.batch_size]
            expected_ranks = {rank for rank, _ in batch}
            request_payload = {
                "query": query,
                "candidates": [{"rank": rank, "text": text} for rank, text in batch],
            }
            response = await self._client.post(self.endpoint, json=request_payload)
            response.raise_for_status()
            request_count += 1

            try:
                body = json.loads(response.content)
            except Exception as exc:
                raise ValueError("HTTP reranker response is not valid JSON") from exc  # noqa: TRY003

            if not isinstance(body, dict) or set(body.keys()) != {"scores"}:
                raise ValueError("HTTP reranker response must contain only 'scores'")  # noqa: TRY003

            raw_scores = body["scores"]
            if not isinstance(raw_scores, list) or len(raw_scores) != len(batch):
                raise ValueError("HTTP reranker 'scores' must be a list matching the batch size")  # noqa: TRY003

            batch_scores: dict[int, float] = {}
            for item in raw_scores:
                if not isinstance(item, dict) or set(item.keys()) != {"rank", "score"}:
                    raise ValueError("Each score entry must contain exactly 'rank' and 'score'")  # noqa: TRY003
                rank = item["rank"]
                score = item["score"]
                if type(rank) is not int:
                    raise ValueError("Score rank must be an integer")  # noqa: TRY003
                if type(score) not in {int, float} or not math.isfinite(score):
                    raise ValueError("Score value must be a finite number")  # noqa: TRY003
                if rank not in expected_ranks or rank in batch_scores:
                    raise ValueError(f"Invalid or duplicate rank {rank!r} in batch response")  # noqa: TRY003
                batch_scores[rank] = float(score)

            if set(batch_scores.keys()) != expected_ranks:
                raise ValueError("Batch response did not cover all expected ranks")  # noqa: TRY003

            global_scores.update(batch_scores)

        ordered_ranks = sorted(
            range(1, candidate_count + 1),
            key=lambda rank: (-global_scores[rank], rank),
        )
        selected_ranks = tuple(ordered_ranks[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=request_count),
            discarded_rank_count=0,
            used_fallback=False,
        )


__all__ = [
    "DEFAULT_HTTP_RERANK_POLICY_ID",
    "HTTPMemoryReranker",
]
