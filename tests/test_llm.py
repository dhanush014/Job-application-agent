"""GroqLLM against a fake Groq client (no network)."""

from types import SimpleNamespace

import groq
import httpx
from pydantic import BaseModel

from jobagent.config import LLMConfig
from jobagent.llm import GroqLLM, LLMEmptyReply, extract_json


class Out(BaseModel):
    score: int


def reply(text, reasoning=0):
    usage = SimpleNamespace(
        total_tokens=10, completion_tokens=2048,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)


class FakeCompletions:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            return reply(*item)
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


def test_reasoning_effort_and_token_cap_are_sent():
    c, comp = client(['{"score": 1}'])
    GroqLLM(LLMConfig(reasoning_effort="low", max_completion_tokens=8000), client=c).json("fast", "s", "u", Out)
    assert comp.calls[0]["reasoning_effort"] == "low"
    assert comp.calls[0]["max_completion_tokens"] == 8000


def test_reasoning_effort_dropped_when_model_rejects_it():
    req = httpx.Request("POST", "https://x")
    err = groq.BadRequestError("'reasoning_effort' is not supported",
                               response=httpx.Response(400, request=req), body=None)
    c, comp = client([err, '{"score": 7}'])
    assert GroqLLM(LLMConfig(), client=c).json("fast", "s", "u", Out).score == 7
    assert "reasoning_effort" in comp.calls[0] and "reasoning_effort" not in comp.calls[1]


def test_empty_reply_names_the_thinking_budget():
    # the exact failure seen on Groq: all output tokens spent reasoning, no content
    c, _ = client([("", 2046)])
    llm = GroqLLM(LLMConfig(), client=c)
    try:
        llm.json("smart", "s", "u", Out)
        raise AssertionError("expected LLMEmptyReply")
    except LLMEmptyReply as e:
        assert "2046 tokens thinking" in str(e)
        assert "reasoning_effort" in str(e)  # tells you how to fix it


class FakeClock:
    """time.sleep advances the clock instead of really waiting."""

    def __init__(self):
        self.now, self.slept = 1000.0, []

    def install(self, monkeypatch):
        monkeypatch.setattr("jobagent.llm.time.time", lambda: self.now)
        monkeypatch.setattr("jobagent.llm.time.sleep", self._sleep)
        return self

    def _sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_pacing_waits_for_the_token_budget(monkeypatch):
    clock = FakeClock().install(monkeypatch)
    c, _ = client(['{"score": 1}', '{"score": 2}'])
    llm = GroqLLM(LLMConfig(tokens_per_minute=8000), client=c)
    llm.json("fast", "s", "u" * 40000, Out)   # ~10k estimated tokens
    llm.json("fast", "s", "u" * 40000, Out)   # must wait for the minute to roll over
    assert sum(clock.slept) >= 60, "second call should have waited out the minute"


def test_no_pacing_when_limit_is_zero(monkeypatch):
    clock = FakeClock().install(monkeypatch)
    c, _ = client(['{"score": 1}', '{"score": 2}'])
    llm = GroqLLM(LLMConfig(tokens_per_minute=0), client=c)
    llm.json("fast", "s", "u" * 40000, Out)
    llm.json("fast", "s", "u" * 40000, Out)
    assert not clock.slept
