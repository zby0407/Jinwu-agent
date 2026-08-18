"""Knowledge base (LLM Wiki) subsystem — P1 knowledge foundation + P2
literature pipeline and grounding gates.

Pure-standard-library core: SQLite storage (``store``), entry contracts and
the lifecycle state machine (``contracts``), business logic (``service``),
CJK-aware FTS5 preparation (``fts``), markdown export/import (``export``),
and the OpenAlex/arXiv literature pipeline with the distill quote-grounding
contract (``literature``). The LangChain tool wrappers live in
``jw/tools/knowledge_base.py``.
"""

from .contracts import (
    ALLOWED_TRANSITIONS,
    CONFIDENCE_LEVELS,
    CONTENT_FIELDS,
    ENTRY_TYPES,
    SOURCE_TYPES,
    STATUSES,
    ContractError,
    check_status_transition,
    quote_is_grounded,
    validate_content,
    validate_distill_content,
    validate_entry,
)
from .literature import (
    build_literature_task_bundle,
    default_literature_dir,
    distill_literature,
    fetch_literature,
    read_literature_task_bundle,
    record_literature_entry_impact,
    search_literature,
)
from .service import (
    conflicts,
    deprecate,
    grounding_warnings,
    import_markdown,
    promote,
    propose,
    propose_literature_patch,
    read,
    search,
    usage_log,
)
from .store import KnowledgeStore, default_db_path, default_export_dir

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CONFIDENCE_LEVELS",
    "CONTENT_FIELDS",
    "ENTRY_TYPES",
    "SOURCE_TYPES",
    "STATUSES",
    "ContractError",
    "KnowledgeStore",
    "build_literature_task_bundle",
    "check_status_transition",
    "conflicts",
    "default_db_path",
    "default_export_dir",
    "default_literature_dir",
    "deprecate",
    "distill_literature",
    "fetch_literature",
    "grounding_warnings",
    "import_markdown",
    "promote",
    "propose",
    "propose_literature_patch",
    "quote_is_grounded",
    "read",
    "read_literature_task_bundle",
    "record_literature_entry_impact",
    "search",
    "search_literature",
    "usage_log",
    "validate_content",
    "validate_distill_content",
    "validate_entry",
]
