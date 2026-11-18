"""平台抽象层对外接口。"""

from .adapters import (
    ADAPTER_MODULES,
    SUPPORTED_PROTOCOLS,
    PlatformAdapter,
    all_adapters,
    get_adapter,
    is_supported_event,
    resolve_adapter,
)
from .capabilities import Capabilities, degrade_segments
from .event import MessageObj, MessageType, Transport, UnifiedEvent
from .message import Segment, UnifiedMessage

__all__ = [
    "Capabilities",
    "degrade_segments",
    "MessageObj",
    "MessageType",
    "Transport",
    "UnifiedEvent",
    "Segment",
    "UnifiedMessage",
    "PlatformAdapter",
    "get_adapter",
    "all_adapters",
    "resolve_adapter",
    "is_supported_event",
    "ADAPTER_MODULES",
    "SUPPORTED_PROTOCOLS",
]
