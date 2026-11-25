"""J8-A2：Prometheus 指标的纯函数面（P5-J8）。

多进程聚合（PROMETHEUS_MULTIPROC_DIR）由容器探针验证；单测只覆盖单进程路径，
用唯一标签值避免用例间串味。
"""

from prometheus_client import REGISTRY

from app import metrics


def test_render_latest_contains_core_metrics():
    payload = metrics.render_latest().decode()
    for name in (
        "marda_http_requests_total",
        "marda_llm_calls_total",
        "marda_llm_slot_wait_seconds",
        "marda_interviews_started_total",
    ):
        assert name in payload


def test_observe_http_increments_counter_and_histogram():
    metrics.observe_http("GET", "/test-metrics/{interview_id}", 200, 0.012)
    assert REGISTRY.get_sample_value(
        "marda_http_requests_total",
        {"method": "GET", "path": "/test-metrics/{interview_id}", "status": "200"},
    ) == 1
    count = REGISTRY.get_sample_value(
        "marda_http_request_duration_seconds_count",
        {"method": "GET", "path": "/test-metrics/{interview_id}"},
    )
    assert count == 1


def test_llm_wait_and_call_outcomes_recorded():
    metrics.record_llm_slot_wait("test-flash", 0.25)
    metrics.record_llm_call("test-flash", "judge", "ok")
    metrics.record_llm_call("test-flash", "judge", "busy")
    assert REGISTRY.get_sample_value(
        "marda_llm_calls_total",
        {"tier": "test-flash", "purpose": "judge", "outcome": "ok"},
    ) == 1
    assert REGISTRY.get_sample_value(
        "marda_llm_calls_total",
        {"tier": "test-flash", "purpose": "judge", "outcome": "busy"},
    ) == 1
    assert REGISTRY.get_sample_value(
        "marda_llm_slot_wait_seconds_count", {"tier": "test-flash"}
    ) == 1


def test_breaker_gauge_set():
    metrics.set_breaker_open("test-pro", True)
    assert REGISTRY.get_sample_value("marda_llm_breaker_open", {"tier": "test-pro"}) == 1
    metrics.set_breaker_open("test-pro", False)
    assert REGISTRY.get_sample_value("marda_llm_breaker_open", {"tier": "test-pro"}) == 0


def test_interview_counters_recorded():
    # 唯一标签值：全量套件里其它用例也会打面试计数，断言绝对值会被串味。
    metrics.record_interview_started("test-suite")
    metrics.record_interview_completed("test-suite")
    assert REGISTRY.get_sample_value(
        "marda_interviews_started_total", {"type": "test-suite"}
    ) == 1
    assert REGISTRY.get_sample_value(
        "marda_interviews_completed_total", {"type": "test-suite"}
    ) == 1
