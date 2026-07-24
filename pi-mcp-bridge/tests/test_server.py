"""Unit tests for pi_mcp_bridge.server prompt builders."""

from pi_mcp_bridge.server import (
    _build_code_assist_prompt,
    _build_debug_prompt,
    _build_explain_prompt,
    _build_review_prompt,
)


def test_build_code_assist_prompt():
    prompt = _build_code_assist_prompt({"task": "Add a helper function"})
    assert "Add a helper function" in prompt
    assert "research agent system" in prompt


def test_build_code_assist_prompt_with_files():
    prompt = _build_code_assist_prompt(
        {"task": "Refactor", "file_paths": ["src/a.py", "src/b.py"]}
    )
    assert "src/a.py" in prompt
    assert "src/b.py" in prompt


def test_build_review_prompt():
    prompt = _build_review_prompt({"code": "def foo(): pass"})
    assert "def foo(): pass" in prompt
    assert "general correctness and code quality" in prompt


def test_build_debug_prompt():
    prompt = _build_debug_prompt({"error": "SyntaxError", "context": "running pytest"})
    assert "SyntaxError" in prompt
    assert "running pytest" in prompt


def test_build_explain_prompt():
    prompt = _build_explain_prompt({"code": "x = [i for i in range(10)]"})
    assert "x = [i for i in range(10)]" in prompt
