from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


META_KEY_SOURCE_AGENT = "source_agent"
META_KEY_SUB_TASKS = "sub_tasks"
META_KEY_REMOTE_TASK_ID = "remote_task_id"

CONV_TASK_KEY = "session:{}:a2a_task_id"


@dataclass(frozen=True)
class InputRequiredState:
    task_id: str
    call_context: Any
    remote_task_id: str = ""
    workflow_id: Optional[str] = None
    sub_tasks: Optional[List[str]] = None


class TaskStateManager:
    def __init__(self, task_store: Any = None):
        self._task_store = task_store
        self._tasks: Dict[str, Dict[str, Any]] = {}

    async def get_task(self, task_id: str, call_context: Any = None) -> Optional[Dict[str, Any]]:
        if self._task_store is not None:
            task = await self._task_store.get(task_id, call_context)
            if task is None:
                return None
            return self._task_to_dict(task)
        return self._tasks.get(task_id)

    async def save_task(self, task_id: str, task_data: Dict[str, Any], call_context: Any = None) -> None:
        if self._task_store is not None:
            task_obj = self._dict_to_task(task_id, task_data)
            await self._task_store.save(task_obj, call_context)
        self._tasks[task_id] = task_data

    async def create_task(
        self,
        task_id: str,
        conv_id: str,
        status_state: str = "WORKING",
        call_context: Any = None,
        source_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        task_data: Dict[str, Any] = {
            "id": task_id,
            "status_state": status_state,
            "metadata": {},
        }
        if conv_id:
            task_data["context_id"] = conv_id
        if source_agent:
            task_data["metadata"][META_KEY_SOURCE_AGENT] = source_agent
        await self.save_task(task_id, task_data, call_context)
        return task_data

    async def update_task_status(
        self,
        task_id: str,
        status_state: str,
        call_context: Any = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        task = await self.get_task(task_id, call_context)
        if task is None:
            return
        task["status_state"] = status_state
        if metadata_updates:
            if "metadata" not in task:
                task["metadata"] = {}
            task["metadata"].update(metadata_updates)
        await self.save_task(task_id, task, call_context)

    async def save_input_required(self, state: InputRequiredState) -> None:
        """保存 INPUT_REQUIRED 状态。

        注意：
        - status_state 只设置状态，不设置 source_agent（source_agent 只在 create_task 时设置）
        - remote_task_id 和 workflow_id 保留用于兼容旧版本
        """
        task_id = state.task_id
        call_context = state.call_context
        remote_task_id = state.remote_task_id
        workflow_id = state.workflow_id
        sub_tasks = state.sub_tasks

        task = await self.get_task(task_id, call_context)
        if task:
            task["status_state"] = "INPUT_REQUIRED"
            if "metadata" not in task:
                task["metadata"] = {}
            # 保留现有的 sub_tasks，不覆盖
            existing_sub_tasks = task["metadata"].get(META_KEY_SUB_TASKS, [])
            if sub_tasks:
                existing_sub_tasks.extend(sub_tasks)
            metadata_updates: Dict[str, Any] = {
                META_KEY_REMOTE_TASK_ID: remote_task_id or "",
                "workflow_id": workflow_id or "",
                META_KEY_SUB_TASKS: existing_sub_tasks,
            }
            # 不再设置 source_agent，它只应该在 create_task 时设置
            task["metadata"].update(metadata_updates)
            await self.save_task(task_id, task, call_context)

    async def add_sub_task(
        self, parent_task_id: str, sub_task_id: str, call_context: Any = None
    ) -> None:
        task = await self.get_task(parent_task_id, call_context)
        if task:
            if "metadata" not in task:
                task["metadata"] = {}
            sub_tasks = list(task["metadata"].get(META_KEY_SUB_TASKS, []))
            if sub_task_id not in sub_tasks:
                sub_tasks.append(sub_task_id)
            task["metadata"][META_KEY_SUB_TASKS] = sub_tasks
            await self.save_task(parent_task_id, task, call_context)

    async def finalize_completed(self, task_id: str, call_context: Any = None) -> None:
        task = await self.get_task(task_id, call_context)
        if task and task.get("status_state") != "COMPLETED":
            task["status_state"] = "COMPLETED"
            await self.save_task(task_id, task, call_context)
            logger.debug(f"[Executor] Task 标记 COMPLETED：task={task_id}")

    async def finalize_failed(
        self, task_id: str, call_context: Any = None, error_text: str = ""
    ) -> None:
        task = await self.get_task(task_id, call_context)
        if task:
            if "metadata" not in task:
                task["metadata"] = {}
            task["metadata"][META_KEY_REMOTE_TASK_ID] = ""
            task["metadata"]["va_task_id"] = ""
            task["status_state"] = "FAILED"
            task["error"] = error_text
            await self.save_task(task_id, task, call_context)

    @staticmethod
    def _task_to_dict(task: Any) -> Dict[str, Any]:
        if isinstance(task, dict):
            return task
        result: Dict[str, Any] = {
            "id": getattr(task, "id", ""),
            "status_state": "",
            "metadata": {},
        }
        status = getattr(task, "status", None)
        if status is not None:
            state = getattr(status, "state", None)
            if state is not None:
                if hasattr(state, "name"):
                    state_name = state.name
                elif isinstance(state, int):
                    int_state_map = {
                        0: "UNSPECIFIED",
                        1: "SUBMITTED",
                        2: "WORKING",
                        3: "COMPLETED",
                        4: "FAILED",
                        5: "CANCELED",
                        6: "INPUT_REQUIRED",
                        7: "REJECTED",
                        8: "AUTH_REQUIRED",
                    }
                    state_name = int_state_map.get(state, str(state))
                else:
                    state_name = str(state)
                result["status_state"] = state_name
        metadata = getattr(task, "metadata", None)
        if metadata is not None:
            if isinstance(metadata, dict):
                result["metadata"] = dict(metadata)
            else:
                try:
                    from google.protobuf.json_format import MessageToDict
                    result["metadata"] = MessageToDict(metadata)
                except Exception:
                    result["metadata"] = {}
        context_id = getattr(task, "context_id", None)
        if context_id:
            result["context_id"] = context_id
        return result

    @staticmethod
    def _dict_to_task(task_id: str, task_data: Dict[str, Any]) -> Any:
        try:
            from a2a.types.a2a_pb2 import (
                Task, TaskStatus,
                TASK_STATE_WORKING, TASK_STATE_INPUT_REQUIRED,
                TASK_STATE_AUTH_REQUIRED, TASK_STATE_COMPLETED,
                TASK_STATE_FAILED, TASK_STATE_SUBMITTED,
                TASK_STATE_CANCELED,
            )
            from google.protobuf.struct_pb2 import Struct

            task = Task(id=task_id)
            state_str = task_data.get("status_state", "WORKING")
            state_map = {
                "WORKING": TASK_STATE_WORKING,
                "INPUT_REQUIRED": TASK_STATE_INPUT_REQUIRED,
                "AUTH_REQUIRED": TASK_STATE_AUTH_REQUIRED,
                "COMPLETED": TASK_STATE_COMPLETED,
                "FAILED": TASK_STATE_FAILED,
                "SUBMITTED": TASK_STATE_SUBMITTED,
                "CANCELED": TASK_STATE_CANCELED,
            }
            state_val = state_map.get(state_str, TASK_STATE_WORKING)
            task.status.CopyFrom(TaskStatus(state=state_val))
            if "context_id" in task_data:
                task.context_id = task_data["context_id"]
            metadata = task_data.get("metadata")
            if metadata and isinstance(metadata, dict):
                struct = Struct()
                struct.update(metadata)
                task.metadata.CopyFrom(struct)
            return task
        except ImportError:
            return task_data
