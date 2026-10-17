from enum import Enum

from src.models.schema import ThinkingConfig


class SenderRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class KBStatus(str, Enum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"
    STALE = "stale"


class KBSourceType(str, Enum):
    GITHUB = "github"
    PDF = "pdf"
    URL = "url"
    NOTES = "notes"


class ThinkingLevel(Enum):
    HIGH = ThinkingConfig(
        tool_run_limit=25,
        tool_thread_limit=80,
        model_run_limit=25,
        model_thread_limit=80,
        token_limit=48000,
        keep_message_limit=30,
        message_limit=60,
    )
    MEDIUM = ThinkingConfig(
        tool_run_limit=15,
        tool_thread_limit=60,
        model_run_limit=15,
        model_thread_limit=60,
        token_limit=32000,
        keep_message_limit=20,
        message_limit=40,
    )
    LOW = ThinkingConfig(
        tool_run_limit=10,
        tool_thread_limit=50,
        model_run_limit=10,
        model_thread_limit=50,
        token_limit=20000,
        keep_message_limit=15,
        message_limit=25,
    )
