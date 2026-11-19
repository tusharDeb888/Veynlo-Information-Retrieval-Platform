from __future__ import annotations

import pytest

import app.services.llm_service as llm_module
from app.services.llm_service import LLMService


@pytest.mark.asyncio
async def test_mock_llm_answer_is_grounded_in_supplied_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module, "LLM_PROVIDER", "mock")
    service = LLMService()

    response = await service.generate_answer(
        "What is the retention period?",
        [
            {
                "filename": "retention-policy.md",
                "page_number": None,
                "text": "Retain operational logs for 30 days.",
            }
        ],
    )

    assert response.provider == "mock"
    assert response.citations_used == [1]
    assert "Retain operational logs for 30 days." in response.answer
    assert "[1]" in response.answer


@pytest.mark.asyncio
async def test_empty_context_returns_no_citations() -> None:
    response = await LLMService().generate_answer("Anything?", [])

    assert response.citations_used == []
    assert "could not find" in response.answer.lower()


@pytest.mark.asyncio
async def test_groq_request_uses_openai_compatible_chat_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    sent: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "The policy is 30 days. [1]"}}]}

    class FakeAsyncClient:
        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> FakeResponse:
            sent["url"] = url
            sent.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(llm_module, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(llm_module, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm_module, "GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: FakeAsyncClient())

    response = await LLMService().generate_answer(
        "What is the retention period?",
        [{"filename": "policy.md", "text": "Retain operational logs for 30 days."}],
    )

    assert response.provider == "groq"
    assert response.citations_used == [1]
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["headers"] == {"Authorization": "Bearer test-key"}
    assert sent["json"] == {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": llm_module._SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Context chunks:\n\n[1] Source: policy.md\nRetain operational logs for 30 days."
                    "\n\n---\n\nUser query: What is the retention period?\n\n"
                    "Answer the query using ONLY the context above, citing chunk numbers."
                ),
            },
        ],
        "temperature": llm_module.LLM_TEMPERATURE,
        "max_tokens": llm_module.LLM_MAX_TOKENS,
    }
