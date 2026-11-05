import json
import subprocess
import urllib.error

import pytest

from agent.llm.client import LLMClient, LLMError
from agent.tools.sandbox import run_isolated


def test_transport_retry_counts_each_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = LLMClient()
    calls = []
    reservations = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("time.sleep", lambda delay: None)
    client.before_request = lambda: reservations.append(1)
    with pytest.raises(LLMError):
        client.complete("system", "user")
    assert len(calls) == len(reservations) == client.request_count == 2


def test_docker_failure_has_no_subprocess_fallback(monkeypatch, tmp_path):
    source = tmp_path / "in.csv"
    source.write_text("a\n1\n")
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "daemon unavailable")

    monkeypatch.setattr("subprocess.run", docker)
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        run_isolated("result = df", source, tmp_path / "out.csv", 5, backend="docker")
    assert len(calls) == 1
    argv = calls[0]
    assert "--network=none" in argv and "--read-only" in argv
    assert "--cap-drop=ALL" in argv and "--memory=512m" in argv
    assert not any("API_KEY" in value for value in argv)


def test_model_request_uses_remaining_deadline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = LLMClient()
    client.before_request = lambda: 0.25
    observed = []

    def fail(*args, **kwargs):
        observed.append(kwargs["timeout"])
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    monkeypatch.setattr("time.sleep", lambda delay: None)
    with pytest.raises(LLMError):
        client.complete("system", "user")
    assert observed == [0.25, 0.25]
