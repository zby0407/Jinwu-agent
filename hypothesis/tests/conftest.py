"""测试隔离：知识库 db 与 markdown 导出指向临时目录。

P2 起，hypothesis preflight 的 kb 引用门禁（方案 §5.4 #2，warning 模式）会打开
默认 KnowledgeStore；隔离保证检查结果不依赖真实 ~/.evoscientist/knowledge.db
的内容，也不向其中写入。setdefault 不覆盖调用方显式设置的环境变量。
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
