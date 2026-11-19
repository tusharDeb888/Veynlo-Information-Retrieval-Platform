"""
LLM grounded-answer synthesis service.

Constructs a strict prompt from retrieved & reranked chunks and calls a
configurable LLM provider to produce a grounded answer **citing only the
provided context**.  Never fabricates information outside the chunks.

Supported providers (selected via ``LLM_PROVIDER`` env var):
- ``mock``   – deterministic echo for tests (default / fallback)
- ``groq``   – Groq Chat Completions API (requires ``GROQ_API_KEY``)

References
----------
- Groq API: https://console.groq.com/docs/api-reference
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_settings = get_settings()
LLM_PROVIDER: str = _settings.LLM_PROVIDER
GROQ_API_KEY: str = _settings.GROQ_API_KEY
GROQ_MODEL: str = _settings.GROQ_MODEL
GROQ_ENDPOINT: str = "https://api.groq.com/openai/v1/chat/completions"
LLM_MAX_TOKENS: int = _settings.LLM_MAX_TOKENS
LLM_TEMPERATURE: float = _settings.LLM_TEMPERATURE

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a knowledge-assurance assistant.  Your ONLY source of truth is the
set of numbered context chunks provided below.  Follow these rules strictly:

1. Answer the user's query using ONLY information found in the context chunks.
2. Cite every claim with the chunk number in square brackets, e.g. [1], [3].
3. If the context chunks do not contain enough information to answer the
   query, say so explicitly – do NOT fabricate or speculate.
4. Keep the answer concise, factual, and well-structured.
5. Never reveal internal chunk metadata (file_id, scores, etc.) to the user.
"""


def _build_context_block(chunks: list[dict[str, Any] | Any]) -> str:
    """Format reranked chunks into a numbered context block for the prompt."""
    lines: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        # Accept both RankedChunk dataclass instances and plain dicts
        if hasattr(chunk, "text"):
            text = chunk.text
            source = getattr(chunk, "filename", "unknown")
            page = getattr(chunk, "page_number", None)
        else:
            text = chunk.get("text", "")
            source = chunk.get("filename", "unknown")
            page = chunk.get("page_number")

        header = f"[{idx}] Source: {source}"
        if page is not None:
            header += f", Page {page}"
        lines.append(f"{header}\n{text}")
    return "\n\n---\n\n".join(lines)


# ---------------------------------------------------------------------------
# Response data class
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    """Structured answer returned by :meth:`LLMService.generate_answer`."""

    answer: str
    citations_used: list[int]
    model: str
    provider: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class LLMService:
    """Singleton LLM synthesis service."""

    _instance: LLMService | None = None

    def __new__(cls) -> LLMService:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def model_name(self) -> str:
        """Return the model identifier advertised in API responses."""
        return GROQ_MODEL if LLM_PROVIDER == "groq" else "mock"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def generate_answer(
        self,
        query: str,
        chunks: list[Any],
    ) -> GroundedAnswer:
        """Generate a grounded answer from *query* and *chunks*.

        Parameters
        ----------
        query:
            The user's natural-language question.
        chunks:
            Reranked chunks (``RankedChunk`` or plain dicts with ``text``,
            ``filename``, ``page_number``).

        Returns
        -------
        GroundedAnswer
            Contains the answer text, citation indices used, model name,
            and provider identifier.
        """
        if not chunks:
            return GroundedAnswer(
                answer="I could not find any relevant information in the knowledge base to answer your query.",
                citations_used=[],
                model="none",
                provider="none",
            )

        context_block = _build_context_block(chunks)
        user_prompt = (
            f"Context chunks:\n\n{context_block}\n\n---\n\n"
            f"User query: {query}\n\n"
            "Answer the query using ONLY the context above, citing chunk numbers."
        )

        if LLM_PROVIDER == "groq":
            if not GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY must be set when LLM_PROVIDER=groq")
            return await self._call_groq(user_prompt, len(chunks))
        return self._mock_answer(query, chunks)

    # ------------------------------------------------------------------
    # Groq provider
    # ------------------------------------------------------------------
    async def _call_groq(
        self,
        user_prompt: str,
        num_chunks: int,
    ) -> GroundedAnswer:
        """Call Groq's OpenAI-compatible Chat Completions endpoint."""
        import httpx

        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": LLM_TEMPERATURE,
            "max_tokens": LLM_MAX_TOKENS,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GROQ_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract text from an OpenAI-compatible chat completion response.
        answer_text = ""
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                candidates = data.get("choices", [])
            if candidates:
                answer_text = str(candidates[0].get("message", {}).get("content", ""))
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Failed to parse Groq response: %s", exc)
            answer_text = "An error occurred while generating the answer. Please try again."

        # Extract citation indices mentioned in the answer
        citations = self._extract_citations(answer_text, num_chunks)

        logger.info(
            "Groq answer generated – model=%s citations=%s",
            GROQ_MODEL,
            citations,
        )
        return GroundedAnswer(
            answer=answer_text,
            citations_used=citations,
            model=GROQ_MODEL,
            provider="groq",
        )

    # ------------------------------------------------------------------
    # Mock / local fallback provider
    # ------------------------------------------------------------------
    def _mock_answer(
        self,
        query: str,
        chunks: list[Any],
    ) -> GroundedAnswer:
        """Produce a deterministic grounded answer for testing.

        Concatenates excerpt from each chunk with citations.
        """
        answer_parts: list[str] = []
        citations: list[int] = []

        for idx, chunk in enumerate(chunks, start=1):
            text = chunk.text if hasattr(chunk, "text") else chunk.get("text", "")
            # Take first 200 chars as excerpt
            excerpt = text[:200].strip()
            if excerpt:
                answer_parts.append(f"{excerpt} [{idx}]")
                citations.append(idx)

        if not answer_parts:
            answer_text = "The retrieved context chunks do not contain sufficient information to answer this query."
        else:
            answer_text = (
                "Based on the available knowledge base, here is what was found "
                "regarding your query:\n\n" + "\n\n".join(answer_parts)
            )

        logger.info("Mock answer generated with %d citations.", len(citations))
        return GroundedAnswer(
            answer=answer_text,
            citations_used=citations,
            model="mock",
            provider="mock",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_citations(text: str, max_idx: int) -> list[int]:
        """Parse ``[N]`` citation markers from generated text."""
        import re

        pattern = re.compile(r"\[(\d+)\]")
        raw = {int(m.group(1)) for m in pattern.finditer(text)}
        # Only keep valid citation indices
        return sorted(c for c in raw if 1 <= c <= max_idx)


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------
def get_llm_service() -> LLMService:
    """Return the global :class:`LLMService` singleton."""
    return LLMService()
