from __future__ import annotations

from dataclasses import dataclass, field
import secrets
from typing import Any


@dataclass
class Task:
    id: str
    context_id: str
    state: str
    history: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    status_message: dict[str, Any] | None = None

    # Internal state (never serialized to A2A response)
    request_state: str | None = None
    input_key: str | None = None
    alternatives: list[str] = field(default_factory=list)
    original_arguments: dict[str, Any] = field(default_factory=dict)
    traceparent: str | None = None

    def is_terminal(self) -> bool:
        return self.state in ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contextId": self.context_id,
            "status": {
                "state": self.state,
                "message": self.status_message,
            },
            "history": self.history,
            "artifacts": self.artifacts,
        }


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, Task] = {}

    def create_task(self, context_id: str | None = None) -> Task:
        task_id = f"task-{secrets.token_hex(6)}"
        if not context_id:
            context_id = f"ctx-{secrets.token_hex(6)}"
        task = Task(id=task_id, context_id=context_id, state="TASK_STATE_SUBMITTED")
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)
