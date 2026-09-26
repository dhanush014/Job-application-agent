"""GroqLLM against a fake Groq client (no network)."""

from types import SimpleNamespace

import groq
import httpx
from pydantic import BaseModel

from jobagent.config import LLMConfig
from jobagent.llm import GroqLLM, extract_json


class Out(BaseModel):
    score: int


def reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(total_tokens=10))


class FakeCompletions:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item)


def client(script):
    comp = FakeCompletions(script)
    return SimpleNamespace(chat=SimpleNamespace(completions=comp)), comp


def json_mode_error():
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return groq.BadRequestError(
        "Error code: 400 - {'error': {'message': 'Failed to generate JSON.', 'code': 'json_validate_failed'}}",
        response=httpx.Response(400, request=req), body=None,
    )


def test_falls_back_when_model_fails_json_mode():
    c, comp = client([json_mode_error(), 'Sure! Here it is:\n```json\n{"score": 88}\n```'])
    llm = GroqLLM(LLMConfig(), client=c)
    assert llm.json("fast", "sys", "user", Out).score == 88
    assert "response_format" in comp.calls[0] and "response_format" not in comp.calls[1]
    # and it remembers: the next call skips JSON mode straight away
    comp.script.append('{"score": 5}')
    assert llm.json("fast", "sys", "user", Out).score == 5
    assert "response_format" not in comp.calls[2]


def test_other_bad_requests_still_raise():
    req = httpx.Request("POST", "https://x")
    err = groq.BadRequestError("model_decommissioned", response=httpx.Response(400, request=req), body=None)
    c, _ = client([err])
    try:
        GroqLLM(LLMConfig(), client=c).json("fast", "s", "u", Out)
        raise AssertionError("expected an error")
    except groq.BadRequestError:
        pass


def test_extract_json():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('text before {"a": {"b": 2}} after') == '{"a": {"b": 2}}'
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
