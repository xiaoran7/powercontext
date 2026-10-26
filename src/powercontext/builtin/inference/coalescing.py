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

"""Concurrent request coalescing and bounded LRU caching for EmbeddingModel."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Sequence

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


class _BatchJob:
    """One bounded provider batch shared by concurrent callers."""

    __slots__ = ("future", "sealed", "task", "texts", "usage", "vectors")

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.texts: list[str] = []
        self.sealed: bool = False
        self.future: asyncio.Future[None] = loop.create_future()
        self.vectors: dict[str, EmbeddingVector] = {}
        self.usage: InferenceUsage = InferenceUsage(requests=0)
        self.task: asyncio.Task[None] | None = None


class CoalescingEmbeddingModel:
    """Coalesce concurrent embedding calls and cache completed vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if model is None or not callable(getattr(model, "embed", None)) or not hasattr(model, "profile"):
            raise ValueError("model must implement EmbeddingModel with profile and embed")  # noqa: TRY003
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(coalesce_ms) not in {int, float} or not math.isfinite(coalesce_ms) or coalesce_ms < 0.0:
            raise ValueError("coalesce_ms must be a non-negative finite number")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003

        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_ms = float(coalesce_ms)
        self._cache_size = cache_size

        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._active: dict[str, _BatchJob] = {}
        self._open_jobs: list[_BatchJob] = []

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult:
        """Return ordered vectors for ``texts``, sharing in-flight work and cached results."""

        if isinstance(texts, (str, bytes, bytearray)):
            raise ValueError("texts must be a sequence of strings")  # noqa: TRY003
        try:
            text_tuple = tuple(texts)
        except TypeError as exc:
            raise ValueError("texts must be an iterable of strings") from exc  # noqa: TRY003

        if not text_tuple:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        for item in text_tuple:
            if not isinstance(item, str):
                raise ValueError("each embedding input must be a string")  # noqa: TRY003

        loop = asyncio.get_running_loop()
        resolved: dict[str, EmbeddingVector] = {}
        needed_jobs: dict[str, _BatchJob] = {}
        waited_jobs: list[_BatchJob] = []
        seen_job_ids: set[int] = set()

        unique_texts: list[str] = []
        seen_texts: set[str] = set()
        for item in text_tuple:
            if item not in seen_texts:
                seen_texts.add(item)
                unique_texts.append(item)

        for item in unique_texts:
            if self._cache_size > 0 and item in self._cache:
                self._cache.move_to_end(item)
                resolved[item] = self._cache[item]
                continue

            job = self._active.get(item)
            if job is None:
                job = self._claim_open_job(loop, item)
            needed_jobs[item] = job
            if id(job) not in seen_job_ids:
                seen_job_ids.add(id(job))
                waited_jobs.append(job)

        for job in waited_jobs:
            await asyncio.shield(job.future)

        for item, job in needed_jobs.items():
            resolved[item] = job.vectors[item]

        ordered_vectors = tuple(resolved[item] for item in text_tuple)
        usage = self._combine_usage(waited_jobs)
        return EmbeddingResult(vectors=ordered_vectors, usage=usage)

    def _claim_open_job(self, loop: asyncio.AbstractEventLoop, text: str) -> _BatchJob:
        while self._open_jobs and self._open_jobs[0].sealed:
            self._open_jobs.pop(0)

        if self._open_jobs:
            job = self._open_jobs[0]
        else:
            job = _BatchJob(loop)
            self._open_jobs.append(job)
            job.task = loop.create_task(self._run_batch(job))

        job.texts.append(text)
        self._active[text] = job
        if len(job.texts) >= self._max_batch_size:
            job.sealed = True
            if self._open_jobs and self._open_jobs[0] is job:
                self._open_jobs.pop(0)
        return job

    async def _run_batch(self, job: _BatchJob) -> None:
        try:
            if self._coalesce_ms > 0.0:
                await asyncio.sleep(self._coalesce_ms / 1000.0)
            else:
                await asyncio.sleep(0)

            job.sealed = True
            if job in self._open_jobs:
                self._open_jobs.remove(job)

            batch_texts = tuple(job.texts)
            result = await self._model.embed(batch_texts)
            if not isinstance(result, EmbeddingResult) or len(result.vectors) != len(batch_texts):
                raise ValueError(
                    f"Embedding provider returned {len(getattr(result, 'vectors', ()))}"
                    f" vectors for {len(batch_texts)} texts"
                )  # noqa: TRY003

            for text, raw_vec in zip(batch_texts, result.vectors, strict=True):
                vec = tuple(float(val) for val in raw_vec)
                job.vectors[text] = vec
                if self._cache_size > 0:
                    if text in self._cache:
                        self._cache.move_to_end(text)
                    self._cache[text] = vec
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)

            job.usage = result.usage
        except BaseException as exc:
            for text in job.texts:
                if self._active.get(text) is job:
                    self._active.pop(text, None)
            if not job.future.done():
                job.future.set_exception(exc)
                _ = job.future.exception()
            return
        else:
            for text in job.texts:
                if self._active.get(text) is job:
                    self._active.pop(text, None)
            if not job.future.done():
                job.future.set_result(None)

    @staticmethod
    def _combine_usage(jobs: list[_BatchJob]) -> InferenceUsage:
        if not jobs:
            return InferenceUsage(requests=0)
        requests = sum(job.usage.requests for job in jobs)
        has_input = any(job.usage.input_tokens is not None for job in jobs)
        has_output = any(job.usage.output_tokens is not None for job in jobs)
        input_tokens = sum(job.usage.input_tokens or 0 for job in jobs) if has_input else None
        output_tokens = sum(job.usage.output_tokens or 0 for job in jobs) if has_output else None
        return InferenceUsage(
            requests=requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


__all__ = ["CoalescingEmbeddingModel"]
