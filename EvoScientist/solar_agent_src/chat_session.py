from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = ROOT / "data" / "processed" / "chat_session.json"
MAX_HISTORY = 15


class ChatSession:
    """Persistent chat session for the unified CLI conversation entry.

    Keeps track of the current dataset, inspection summary, recent chat history,
    and candidate findings that may later be written back to an LLM Wiki by the
    knowledge-management sub-agent.
    """

    def __init__(self, session_path: Path | None = None) -> None:
        self.session_path = session_path or SESSION_PATH
        self._data: dict[str, Any] = self._load_or_default()

    def _load_or_default(self) -> dict[str, Any]:
        if self.session_path.exists():
            try:
                return json.loads(self.session_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return self._default_session()

    def _default_session(self) -> dict[str, Any]:
        return {
            "session_id": f"chat_{uuid.uuid4().hex[:12]}",
            "current_dataset": None,
            "loaded_at": None,
            "inspection_summary": None,
            "uploaded_datasets": [],
            "aligned_dataset": None,
            "llm_recognitions": {},
            "chat_history": [],
            "candidate_findings": [],
        }

    def save(self) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def session_id(self) -> str:
        return str(self._data.get("session_id", ""))

    def get_current_dataset_path(self) -> str | None:
        return self._data.get("current_dataset")

    def get_inspection_summary(self) -> dict[str, Any] | None:
        return self._data.get("inspection_summary")

    def set_current_dataset(self, path: str, inspection: dict[str, Any]) -> None:
        summary = self._extract_inspection_summary(inspection)
        self._data.update(
            {
                "current_dataset": path,
                "loaded_at": datetime.now(timezone.utc).isoformat(),
                "inspection_summary": summary,
            }
        )
        self._add_or_update_uploaded_dataset(summary)
        self.save()

    def _add_or_update_uploaded_dataset(self, summary: dict[str, Any]) -> None:
        datasets = self._data.setdefault("uploaded_datasets", [])
        stored_path = summary.get("stored_path")
        if not stored_path:
            return
        for idx, existing in enumerate(datasets):
            if existing.get("stored_path") == stored_path:
                datasets[idx] = summary
                return
        datasets.append(summary)

    def get_uploaded_datasets(self) -> list[dict[str, Any]]:
        return list(self._data.get("uploaded_datasets", []))

    def set_llm_recognition(self, stored_path: str, recognition: dict[str, Any]) -> None:
        self._data.setdefault("llm_recognitions", {})[stored_path] = recognition
        self.save()

    def get_llm_recognition(self, stored_path: str | None) -> dict[str, Any] | None:
        if not stored_path:
            return None
        return self._data.get("llm_recognitions", {}).get(stored_path)

    def clear_uploaded_datasets(self) -> None:
        self._data["uploaded_datasets"] = []
        self._data["aligned_dataset"] = None
        self._data["llm_recognitions"] = {}
        self.save()

    def set_aligned_dataset(self, path: str, report: dict[str, Any]) -> None:
        self._data["aligned_dataset"] = {"path": path, "report": report}
        self._data["current_dataset"] = path
        self._data["loaded_at"] = datetime.now(timezone.utc).isoformat()
        self._data["inspection_summary"] = self._extract_inspection_summary(report)
        self.save()

    def get_aligned_dataset_path(self) -> str | None:
        aligned = self._data.get("aligned_dataset")
        return aligned.get("path") if aligned else None

    def get_aligned_dataset_report(self) -> dict[str, Any] | None:
        aligned = self._data.get("aligned_dataset")
        return aligned.get("report") if aligned else None

    def clear_dataset(self) -> None:
        self._data["current_dataset"] = None
        self._data["loaded_at"] = None
        self._data["inspection_summary"] = None
        self._data["aligned_dataset"] = None
        self.save()

    def clear_all(self) -> None:
        self._data = self._default_session()
        self.save()

    def set_pending_clarification(self, intent: dict[str, Any]) -> None:
        self._data["pending_clarification"] = intent
        self.save()

    def get_pending_clarification(self) -> dict[str, Any] | None:
        return self._data.get("pending_clarification")

    def clear_pending_clarification(self) -> None:
        self._data.pop("pending_clarification", None)
        self.save()

    def _extract_inspection_summary(self, inspection: dict[str, Any]) -> dict[str, Any]:
        source = inspection.get("source_file", {})
        insp = inspection.get("inspection", {})
        time_detection = insp.get("time_detection", {})
        stored_path = source.get("stored_path")
        return {
            "source_name": source.get("name"),
            "stored_path": stored_path,
            "bytes": source.get("bytes"),
            "sha256": source.get("sha256"),
            "report_path": inspection.get("report_path"),
            "dataset_id": self._dataset_id_from_stored_path(stored_path),
            "rows_read": insp.get("rows_read"),
            "column_count": insp.get("column_count"),
            "columns": [field.get("name") for field in insp.get("columns", [])],
            "primary_time_column": time_detection.get("primary_time_column"),
            "primary_time_columns": time_detection.get("primary_time_columns"),
            "encoding": insp.get("encoding"),
            "delimiter": insp.get("delimiter"),
            "warnings": insp.get("warnings", []),
        }

    @staticmethod
    def _dataset_id_from_stored_path(stored_path: str | None) -> str | None:
        if not stored_path:
            return None
        parts = Path(stored_path).parts
        # data/uploads/<id>/source.csv -> <id>
        if len(parts) >= 3 and parts[0] == "data" and parts[1] == "uploads":
            return Path(parts[2]).stem
        # data/processed/uploads/<id>/source.csv -> <id>
        if len(parts) >= 4 and parts[0] == "data" and parts[1] == "processed" and parts[2] == "uploads":
            return Path(parts[3]).stem
        # Project internal files or ad-hoc files: use the table name as dataset_id
        return Path(stored_path).stem

    def append_history(self, role: str, content: str) -> None:
        self._data.setdefault("chat_history", []).append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        # Keep only the most recent MAX_HISTORY rounds (each round = user + assistant)
        max_entries = MAX_HISTORY * 2
        if len(self._data["chat_history"]) > max_entries:
            self._data["chat_history"] = self._data["chat_history"][-max_entries:]
        self.save()

    def get_upload_registry_path(self) -> Path | None:
        """Return the path to the upload-specific feature registry if one exists."""
        dataset_id = self.get_dataset_id()
        if not dataset_id:
            return None
        return ROOT / "data" / "processed" / "uploads" / dataset_id / "upload_feature_registry.json"

    def get_dataset_id(self) -> str | None:
        inspection = self.get_inspection_summary()
        return inspection.get("dataset_id") if inspection else None

    def load_upload_registry(self) -> dict[str, Any] | None:
        path = self.get_upload_registry_path()
        if path and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        # Backward compatibility: older uploads used feature_registry.json.
        legacy_path = path.parent / "feature_registry.json" if path else None
        if legacy_path and legacy_path.exists():
            try:
                return json.loads(legacy_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def save_upload_registry(self, registry: dict[str, Any], path: Path | None = None) -> None:
        path = path or self.get_upload_registry_path()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._data.get("chat_history", []))

    def log_candidate_finding(self, finding: dict[str, Any]) -> None:
        """Log a candidate finding for the knowledge-management sub-agent.

        The finding is not written to an LLM Wiki in this implementation; it is
        stored locally so that a future knowledge-management sub-agent can batch
        promote candidate findings to canonical knowledge entries.
        """
        finding_with_meta = {
            **finding,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "current_dataset": self.get_current_dataset_path(),
        }
        self._data.setdefault("candidate_findings", []).append(finding_with_meta)
        self.save()

    def get_candidate_findings(self) -> list[dict[str, Any]]:
        return list(self._data.get("candidate_findings", []))

    def get_agent_messages(self) -> list[dict[str, Any]]:
        """Return persisted OpenAI-compatible messages for the agent runtime."""
        return list(self._data.get("agent_messages", []))

    def set_agent_messages(self, messages: list[dict[str, Any]]) -> None:
        # Tool traces can make conversations grow quickly. Keep a generous but
        # finite tail; the system prompt is rebuilt for every model call.
        self._data["agent_messages"] = list(messages[-120:])
        self.save()

    def get_activated_skills(self) -> list[str]:
        return list(self._data.get("activated_skills", []))

    def activate_skill(self, name: str) -> None:
        skills = self._data.setdefault("activated_skills", [])
        if name not in skills:
            skills.append(name)
            self.save()

    def append_tool_trace(self, item: dict[str, Any]) -> None:
        trace = self._data.setdefault("agent_tool_trace", [])
        trace.append(item)
        if len(trace) > 200:
            self._data["agent_tool_trace"] = trace[-200:]
        self.save()

    def get_tool_trace(self) -> list[dict[str, Any]]:
        return list(self._data.get("agent_tool_trace", []))

    def set_pending_action(self, action: dict[str, Any]) -> None:
        self._data["agent_pending_action"] = dict(action)
        self.save()

    def get_pending_action(self) -> dict[str, Any] | None:
        value = self._data.get("agent_pending_action")
        return dict(value) if isinstance(value, dict) else None

    def clear_pending_action(self) -> None:
        self._data.pop("agent_pending_action", None)
        self.save()

    def get_agent_state(self, key: str, default: Any = None) -> Any:
        return self._data.get("agent_state", {}).get(key, default)

    def set_agent_state(self, key: str, value: Any) -> None:
        self._data.setdefault("agent_state", {})[key] = value
        self.save()

    def clear_agent_state(self, *keys: str) -> None:
        state = self._data.get("agent_state", {})
        if not keys:
            self._data.pop("agent_state", None)
        else:
            for key in keys:
                state.pop(key, None)
        self.save()

    def get_cleaning_column_overrides(self) -> dict[str, str]:
        return dict(self._data.get("cleaning_column_overrides", {}))

    def set_cleaning_column_override(self, semantic: str, column: str) -> None:
        self._data.setdefault("cleaning_column_overrides", {})[semantic] = column
        self.save()

    def clear_cleaning_column_overrides(self) -> None:
        self._data.pop("cleaning_column_overrides", None)
        self.save()

    def get_cleaning_coverage_overrides(self) -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in self._data.get("cleaning_coverage_overrides", {}).items()}

    def set_cleaning_coverage_override(self, key: str, values: dict[str, str]) -> None:
        self._data.setdefault("cleaning_coverage_overrides", {})[key] = values
        self.save()

    def clear_cleaning_coverage_overrides(self) -> None:
        self._data.pop("cleaning_coverage_overrides", None)
        self.save()

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)
