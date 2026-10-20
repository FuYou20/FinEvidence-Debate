from copy import deepcopy
from functools import wraps
from threading import Lock
from time import perf_counter


def _prompt_characters(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_prompt_characters(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_prompt_characters(item) for item in value)
    return 0


def _read_usage(response, name):
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(name)
    return getattr(usage, name, None)


class ModelCallMonitor:
    """记录模型请求次数、耗时、Prompt 字符数和 SDK usage。"""

    def __init__(self):
        self._calls = []
        self._lock = Lock()

    def call(self, *, method, target, args, kwargs):
        started = perf_counter()
        error_type = None
        response = None
        try:
            response = target(*args, **kwargs)
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            text_format = kwargs.get("text_format")
            record = {
                "sequence": 0,
                "method": method,
                "result_type": getattr(text_format, "__name__", "text"),
                "duration_seconds": round(perf_counter() - started, 6),
                "prompt_characters": _prompt_characters(kwargs.get("input")),
                "input_tokens": _read_usage(response, "input_tokens"),
                "output_tokens": _read_usage(response, "output_tokens"),
                "total_tokens": _read_usage(response, "total_tokens"),
                "error_type": error_type,
            }
            with self._lock:
                record["sequence"] = len(self._calls) + 1
                self._calls.append(record)

    def snapshot(self):
        with self._lock:
            calls = deepcopy(self._calls)
        numeric_fields = (
            "duration_seconds",
            "prompt_characters",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        )
        totals = {}
        for field in numeric_fields:
            values = [call[field] for call in calls if call[field] is not None]
            totals[field] = round(sum(values), 6) if values else None
        return {
            "total_calls": len(calls),
            **totals,
            "calls": calls,
        }


class FunctionCallMonitor:
    """记录 Embedding、搜索等普通依赖调用的次数、耗时与错误。"""

    def __init__(self):
        self._calls = []
        self._lock = Lock()

    def call(self, *, name, target, args, kwargs):
        started = perf_counter()
        error_type = None
        try:
            return target(*args, **kwargs)
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            record = {
                "sequence": 0,
                "name": name,
                "duration_seconds": round(perf_counter() - started, 6),
                "error_type": error_type,
            }
            with self._lock:
                record["sequence"] = len(self._calls) + 1
                self._calls.append(record)

    def snapshot(self):
        with self._lock:
            calls = deepcopy(self._calls)
        return {
            "total_calls": len(calls),
            "duration_seconds": round(
                sum(call["duration_seconds"] for call in calls),
                6,
            ),
            "failed_calls": sum(
                call["error_type"] is not None for call in calls
            ),
            "calls": calls,
        }


def instrument_callable(target, monitor, *, name):
    if not callable(target):
        raise TypeError("target 必须是可调用对象")
    if not isinstance(monitor, FunctionCallMonitor):
        raise TypeError("monitor 必须是 FunctionCallMonitor")

    @wraps(target)
    def wrapped(*args, **kwargs):
        return monitor.call(
            name=name,
            target=target,
            args=args,
            kwargs=kwargs,
        )

    return wrapped


class _InstrumentedResponses:
    def __init__(self, responses, monitor):
        self._responses = responses
        self._monitor = monitor

    def create(self, *args, **kwargs):
        return self._monitor.call(
            method="create",
            target=self._responses.create,
            args=args,
            kwargs=kwargs,
        )

    def parse(self, *args, **kwargs):
        return self._monitor.call(
            method="parse",
            target=self._responses.parse,
            args=args,
            kwargs=kwargs,
        )


class InstrumentedClient:
    """保留原客户端接口，只包装 ``responses.create/parse``。"""

    def __init__(self, client, monitor):
        if not isinstance(monitor, ModelCallMonitor):
            raise TypeError("monitor 必须是 ModelCallMonitor")
        self._client = client
        self.responses = _InstrumentedResponses(client.responses, monitor)

    def __getattr__(self, name):
        return getattr(self._client, name)
