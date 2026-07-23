"""Bridge pi tool calls into EvoScientist's sandbox backend."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from .. import paths as _paths_mod
from ..backends import (
    CustomSandboxBackend,
    MemoryFilesystemBackend,
    MergedSkillsBackend,
)
from ..config import EvoScientistConfig

logger = logging.getLogger(__name__)

_BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "subagents"


class PiToolBridge:
    """Execute pi-style file/shell tools through EvoScientist backends.

    Mirrors pi's built-in tool surface (read, bash, edit, write, ls, grep, glob)
    so a pi extension can override the native tools and route execution here.

    Additionally exposes EvoScientist-native capabilities:
      - memory observation tools (search/read/record)
      - schedule tools
      - skill manager
    """

    def __init__(
        self,
        workspace_dir: str,
        config: EvoScientistConfig | None = None,
        *,
        backend: CompositeBackend | None = None,
        source_session_id: str | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.config = config
        self._backend = backend or self._build_backend(workspace_dir, config)
        self._memory_dir = Path(_paths_mod.MEMORIES_DIR)
        self._project_id = self._resolve_project_id(workspace_dir)
        self._source_session_id = source_session_id or "pi"

    @staticmethod
    def _build_backend(
        workspace_dir: str, config: EvoScientistConfig | None = None
    ) -> CompositeBackend:
        cfg = config
        dangerous = bool(getattr(cfg, "dangerous_mode", False)) if cfg else False
        timeout = 300
        if cfg:
            try:
                timeout = int(getattr(cfg, "sandbox_execute_timeout", 300) or 300)
            except (TypeError, ValueError):
                timeout = 300

        ws_backend = CustomSandboxBackend(
            root_dir=workspace_dir,
            virtual_mode=True,
            timeout=timeout,
            dangerous=dangerous,
        )
        sk_backend = MergedSkillsBackend(
            primary_dir=str(_paths_mod.USER_SKILLS_DIR),
            global_dir=str(_paths_mod.GLOBAL_SKILLS_DIR),
            secondary_dir=str(_BUILTIN_SKILLS_DIR),
        )
        mem_backend = MemoryFilesystemBackend(
            root_dir=str(_paths_mod.MEMORIES_DIR),
            virtual_mode=True,
        )
        return CompositeBackend(
            default=ws_backend,
            routes={
                "/skills/": sk_backend,
                "/memories/": mem_backend,
            },
        )

    @staticmethod
    def _resolve_project_id(workspace_dir: str) -> str:
        """Best-effort project id for memory/schedule scoping."""
        try:
            from ..memory.project import resolve_project_id

            return resolve_project_id(workspace_dir)
        except Exception as exc:
            logger.debug("could not resolve project id for %s: %s", workspace_dir, exc)
            return Path(workspace_dir).name or "default"

    # -------------------------------------------------------------------------
    # Filesystem tools (backed by CompositeBackend)
    # -------------------------------------------------------------------------

    def read(self, path: str, *, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        """Read file contents through the composite backend."""
        try:
            raw = self._backend.read(path, offset=offset, limit=limit)
            return self._serialize_read_result(path, raw)
        except Exception as exc:
            logger.warning("pi tool bridge read failed: %s", exc, exc_info=True)
            return {"content": f"Error reading {path}: {exc}", "isError": True}

    @staticmethod
    def _serialize_read_result(path: str, raw: Any) -> dict[str, Any]:
        """Normalize a backend read result into a JSON-safe dict.

        The composite backend returns a ``ReadResult`` dataclass; legacy mocks
        or other backends may return a plain string. Accept both shapes.
        """
        if isinstance(raw, ReadResult):
            if raw.error:
                return {
                    "content": f"Error reading {path}: {raw.error}",
                    "isError": True,
                }
            if raw.file_data is None:
                return {"content": "", "isError": False}
            content = raw.file_data.get("content", "")
            return {"content": content, "isError": False}
        if isinstance(raw, str):
            return {"content": raw, "isError": False}
        return {"content": str(raw), "isError": False}

    def bash(self, command: str, *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a shell command through the sandbox backend."""
        try:
            response: ExecuteResponse = self._backend.execute(command, timeout=timeout)
            # Normalize output; pi expects a string content.
            output = response.output or ""
            if response.truncated:
                output += "\n... (truncated)"
            return {
                "content": output,
                "details": {"exit_code": response.exit_code},
                "isError": response.exit_code != 0,
            }
        except Exception as exc:
            logger.warning("pi tool bridge bash failed: %s", exc, exc_info=True)
            return {"content": f"Error executing command: {exc}", "isError": True}

    def write(self, path: str, content: str) -> dict[str, Any]:
        """Write a file through the composite backend."""
        try:
            result: WriteResult = self._backend.write(path, content)
            if result.error:
                return {
                    "content": f"Error writing {path}: {result.error}",
                    "isError": True,
                }
            return {"content": f"Wrote {path}", "isError": False}
        except Exception as exc:
            logger.warning("pi tool bridge write failed: %s", exc, exc_info=True)
            return {"content": f"Error writing {path}: {exc}", "isError": True}

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Edit a file through the composite backend."""
        try:
            result: EditResult = self._backend.edit(
                path, old_string, new_string, replace_all=replace_all
            )
            if result.error:
                return {
                    "content": f"Error editing {path}: {result.error}",
                    "isError": True,
                }
            occurrences = result.occurrences or 0
            return {
                "content": f"Edited {path} ({occurrences} occurrence(s))",
                "isError": False,
            }
        except Exception as exc:
            logger.warning("pi tool bridge edit failed: %s", exc, exc_info=True)
            return {"content": f"Error editing {path}: {exc}", "isError": True}

    def ls(self, path: str) -> dict[str, Any]:
        """List directory contents through the composite backend."""
        try:
            result: LsResult = self._backend.ls(path)
            if result.error:
                return {
                    "content": f"Error listing {path}: {result.error}",
                    "isError": True,
                }
            entries = [dict(e) for e in (result.entries or [])]
            return {
                "content": json.dumps(entries, ensure_ascii=False),
                "isError": False,
            }
        except Exception as exc:
            logger.warning("pi tool bridge ls failed: %s", exc, exc_info=True)
            return {"content": f"Error listing {path}: {exc}", "isError": True}

    def glob(self, pattern: str, *, path: str | None = None) -> dict[str, Any]:
        """Glob files through the composite backend."""
        try:
            result: GlobResult = self._backend.glob(pattern, path=path)
            if result.error:
                return {
                    "content": f"Error globbing {pattern}: {result.error}",
                    "isError": True,
                }
            matches = [dict(m) for m in (result.matches or [])]
            return {
                "content": json.dumps(matches, ensure_ascii=False),
                "isError": False,
            }
        except Exception as exc:
            logger.warning("pi tool bridge glob failed: %s", exc, exc_info=True)
            return {"content": f"Error globbing {pattern}: {exc}", "isError": True}

    def grep(
        self,
        pattern: str,
        *,
        path: str | None = None,
        glob: str | None = None,
    ) -> dict[str, Any]:
        """Grep files through the composite backend."""
        try:
            result: GrepResult = self._backend.grep(pattern, path=path, glob=glob)
            if result.error:
                return {
                    "content": f"Error grepping {pattern}: {result.error}",
                    "isError": True,
                }
            matches = [dict(m) for m in (result.matches or [])]
            return {
                "content": json.dumps(matches, ensure_ascii=False),
                "isError": False,
            }
        except Exception as exc:
            logger.warning("pi tool bridge grep failed: %s", exc, exc_info=True)
            return {"content": f"Error grepping {pattern}: {exc}", "isError": True}

    # -------------------------------------------------------------------------
    # EvoScientist-native capabilities
    # -------------------------------------------------------------------------

    def search_observations(
        self,
        query: str,
        *,
        mode: str = "ranked",
        scope: str | None = None,
        memory_type: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Search EvoMemory observations."""
        try:
            from ..memory import create_search_observations_tool
            from ..memory.types import MemoryScope, MemoryType, ObservationSearchMode

            tool = create_search_observations_tool(
                memory_dir=self._memory_dir,
                project_id=self._project_id,
            )
            search_mode = ObservationSearchMode(mode)
            scope_val = MemoryScope(scope) if scope else None
            type_val = MemoryType(memory_type) if memory_type else None
            result = tool.invoke(
                {
                    "query": query,
                    "mode": search_mode,
                    "scope": scope_val,
                    "memory_type": type_val,
                    "limit": limit,
                }
            )
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("search_observations failed: %s", exc, exc_info=True)
            return {"content": f"Error searching observations: {exc}", "isError": True}

    def read_memory(self, observation_id: str) -> dict[str, Any]:
        """Read a single EvoMemory observation by id."""
        try:
            from ..memory import create_read_memory_tool

            tool = create_read_memory_tool(
                memory_dir=self._memory_dir,
                project_id=self._project_id,
            )
            result = tool.invoke({"observation_id": observation_id})
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("read_memory failed: %s", exc, exc_info=True)
            return {"content": f"Error reading memory: {exc}", "isError": True}

    def record_observation(
        self,
        memory_type: str,
        summary: str,
        observation: str,
        why_it_matters: str,
        *,
        scope: str = "global",
        evidence: str | None = None,
        source_agent: str = "pi",
    ) -> dict[str, Any]:
        """Record a structured EvoMemory observation."""
        try:
            from ..memory import create_record_observation_tool
            from ..memory.types import MemorySourceType

            tool = create_record_observation_tool(
                memory_dir=self._memory_dir,
                project_id=self._project_id,
                source_type=MemorySourceType.TURN,
                source_agent=source_agent,
            )
            runtime = self._make_memory_runtime()
            result = tool.invoke(
                {
                    "memory_type": memory_type,
                    "summary": summary,
                    "observation": observation,
                    "why_it_matters": why_it_matters,
                    "scope": scope,
                    "evidence": evidence,
                    "runtime": runtime,
                }
            )
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("record_observation failed: %s", exc, exc_info=True)
            return {"content": f"Error recording observation: {exc}", "isError": True}

    def _make_memory_runtime(self) -> object:
        """Build a minimal runtime object so memory tools can resolve session id."""

        class _ToolRuntime:
            def __init__(self, session_id: str, project_id: str):
                self.config = {
                    "configurable": {
                        "thread_id": session_id,
                        "evomemory_source_session_id": session_id,
                        "evomemory_project_id": project_id,
                    }
                }

        return _ToolRuntime(self._source_session_id, self._project_id)

    def schedule_task(
        self,
        name: str,
        cron: str,
        prompt: str,
        *,
        timezone: str = "",
    ) -> dict[str, Any]:
        """Create a recurring scheduled task via langgraph dev crons."""
        try:
            from ..middleware.scheduler import schedule_task as _schedule_task

            result = _schedule_task.invoke(
                {"name": name, "cron": cron, "prompt": prompt, "timezone": timezone}
            )
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("schedule_task failed: %s", exc, exc_info=True)
            return {"content": f"Error scheduling task: {exc}", "isError": True}

    def list_scheduled_tasks(self) -> dict[str, Any]:
        """List recurring scheduled tasks."""
        try:
            from ..middleware.scheduler import list_scheduled_tasks as _list

            result = _list.invoke({})
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("list_scheduled_tasks failed: %s", exc, exc_info=True)
            return {"content": f"Error listing scheduled tasks: {exc}", "isError": True}

    def cancel_scheduled_task(self, cron_id: str) -> dict[str, Any]:
        """Cancel a recurring scheduled task."""
        try:
            from ..middleware.scheduler import cancel_scheduled_task as _cancel

            result = _cancel.invoke({"cron_id": cron_id})
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("cancel_scheduled_task failed: %s", exc, exc_info=True)
            return {
                "content": f"Error cancelling scheduled task: {exc}",
                "isError": True,
            }

    def skill_manager(
        self,
        action: str,
        *,
        source: str = "",
        name: str = "",
        tag: str = "",
        include_system: bool = False,
    ) -> dict[str, Any]:
        """Manage EvoScientist skills."""
        try:
            from ..tools.skill_manager import skill_manager

            result = skill_manager.invoke(
                {
                    "action": action,
                    "source": source,
                    "name": name,
                    "tag": tag,
                    "include_system": include_system,
                }
            )
            return {"content": str(result), "isError": False}
        except Exception as exc:
            logger.warning("skill_manager failed: %s", exc, exc_info=True)
            return {"content": f"Error managing skills: {exc}", "isError": True}
