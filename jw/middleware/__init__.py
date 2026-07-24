"""Middleware package for JW.

Re-exports middleware classes and factory functions so that existing
``from jw.middleware import X`` imports continue to work.
"""

from .ask_user import (
    AskUserMiddleware,
    AskUserRequest,
    AskUserWidgetResult,
    Choice,
    Question,
)
from .closed_loop_orchestration import (
    ClosedLoopOrchestrationGuardMiddleware,
    closed_loop_receipts,
)
from .code_interpreter import create_code_interpreter_middleware
from .configurable_model import ConfigurableModelMiddleware
from .context_editing import (
    compute_context_editing_trigger,
    create_context_editing_middleware,
)
from .context_overflow import ContextOverflowMapperMiddleware
from .contract_tool_allowlist import (
    CONTRACT_TOOL_ALLOWLISTS,
    ContractToolAllowlistMiddleware,
    contract_tool_allowlist,
)
from .memory import (
    JWMemoryMiddleware,
    create_memory_middleware,
)
from .memory_lifecycle import (
    JWMemoryLifecycleMiddleware,
    create_memory_lifecycle_middleware,
    default_memory_scheduler,
)
from .model_fallback import ModelFallbackMiddleware, load_fallback_chain
from .runtime_context import RuntimeContextMiddleware, create_runtime_context_middleware
from .scheduler import (
    SchedulerMiddleware,
    create_scheduler_middleware,
)
from .task_workspace import TaskWorkspaceMiddleware
from .task_cancellation import TaskCancellationMiddleware
from .tool_error_handler import ToolErrorHandlerMiddleware
from .tool_selector import create_tool_selector_middleware
from .utils import disable_thinking
from .virtual_path_code_guard import VirtualPathCodeGuardMiddleware

__all__ = [
    "AskUserMiddleware",
    "AskUserRequest",
    "AskUserWidgetResult",
    "Choice",
    "ConfigurableModelMiddleware",
    "CONTRACT_TOOL_ALLOWLISTS",
    "ContractToolAllowlistMiddleware",
    "ClosedLoopOrchestrationGuardMiddleware",
    "ContextOverflowMapperMiddleware",
    "JWMemoryLifecycleMiddleware",
    "JWMemoryMiddleware",
    "ModelFallbackMiddleware",
    "Question",
    "RuntimeContextMiddleware",
    "SchedulerMiddleware",
    "TaskWorkspaceMiddleware",
    "TaskCancellationMiddleware",
    "ToolErrorHandlerMiddleware",
    "VirtualPathCodeGuardMiddleware",
    "compute_context_editing_trigger",
    "contract_tool_allowlist",
    "closed_loop_receipts",
    "create_code_interpreter_middleware",
    "create_context_editing_middleware",
    "create_memory_lifecycle_middleware",
    "create_memory_middleware",
    "create_runtime_context_middleware",
    "create_scheduler_middleware",
    "create_tool_selector_middleware",
    "default_memory_scheduler",
    "disable_thinking",
    "load_fallback_chain",
]
