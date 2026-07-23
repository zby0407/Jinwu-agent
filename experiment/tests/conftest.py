"""测试隔离：知识库 db 与 markdown 导出指向临时目录。

P2 起，experiment service 在 validate_design / finalize 时会接触知识库
（方案 §5.4 #3/#4）。不设隔离时这些调用会写真实 ~/.evoscientist/knowledge.db
与仓库 knowledge_base/ 导出树。setdefault 不覆盖调用方显式设置的环境变量。
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault(
    "EVOSCIENTIST_DATA_DIR", tempfile.mkdtemp(prefix="kb_test_data_")
)
os.environ.setdefault(
    "EVOSCIENTIST_KB_EXPORT_DIR", tempfile.mkdtemp(prefix="kb_test_export_")
)
