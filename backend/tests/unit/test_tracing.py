"""J8-B：追踪开关与空操作的纯逻辑面（不启动 SDK、不联网）。"""

import pytest

from app import tracing


def test_disabled_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    assert tracing.enabled() is False
    with tracing.turn_span("iv-1", "tech") as span:
        assert span is None


def test_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert tracing.enabled() is False


def test_enabled_with_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    assert tracing.enabled() is True


def test_configure_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracing.configure(object()) is False
