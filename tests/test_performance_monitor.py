from types import SimpleNamespace

import pytest

from src.performance_monitor import (
    FunctionCallMonitor,
    InstrumentedClient,
    ModelCallMonitor,
    instrument_callable,
)


class FakeResponses:
    def create(self, **_kwargs):
        return SimpleNamespace(
            output_text="完成",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
            ),
        )

    def parse(self, **_kwargs):
        return SimpleNamespace(output_parsed={"ok": True}, usage=None)


def test_monitor_records_calls_prompt_size_latency_and_usage():
    monitor = ModelCallMonitor()
    client = InstrumentedClient(
        SimpleNamespace(responses=FakeResponses()),
        monitor,
    )

    client.responses.create(
        model="test-model",
        input=[{"role": "user", "content": "测试问题"}],
    )
    client.responses.parse(
        model="test-model",
        input=[{"role": "user", "content": "结构化问题"}],
        text_format=dict,
    )

    snapshot = monitor.snapshot()
    assert snapshot["total_calls"] == 2
    assert snapshot["prompt_characters"] >= len("测试问题结构化问题")
    assert snapshot["input_tokens"] == 10
    assert snapshot["total_tokens"] == 12
    assert snapshot["calls"][1]["result_type"] == "dict"


def test_instrumented_client_rejects_invalid_monitor():
    with pytest.raises(TypeError, match="ModelCallMonitor"):
        InstrumentedClient(SimpleNamespace(responses=FakeResponses()), object())


def test_instrumented_callable_records_success_and_failure():
    monitor = FunctionCallMonitor()
    successful = instrument_callable(
        lambda value: value * 2,
        monitor,
        name="embedding",
    )

    assert successful(3) == 6
    snapshot = monitor.snapshot()
    assert snapshot["total_calls"] == 1
    assert snapshot["failed_calls"] == 0
    assert snapshot["calls"][0]["name"] == "embedding"
