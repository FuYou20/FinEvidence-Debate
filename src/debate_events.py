"""可选展示事件；只传副本，展示层不持有辩论中的可变状态。"""

from copy import deepcopy


def validate_observer(on_event):
    if on_event is not None and not callable(on_event):
        raise TypeError("on_event 必须是可调用对象或 None")


def emit_event(on_event, name, **data):
    if on_event is not None:
        on_event({"name": name, **deepcopy(data)})
