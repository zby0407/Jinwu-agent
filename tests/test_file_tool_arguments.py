"""Reproduce desktop file-tool argument errors through the real tool graph."""

import asyncio

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from jw.middleware.virtual_path_code_guard import VirtualPathCodeGuardMiddleware


class LocalModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def run_calls(tmp_path, calls, use_async=False):
    from jw.middleware.file_tool_arguments import FileToolArgumentsMiddleware

    model = LocalModel(
        responses=[
            *(
                AIMessage(content="", tool_calls=[dict(call, id=str(i))])
                for i, call in enumerate(calls)
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_agent(
        model,
        middleware=[
            FileToolArgumentsMiddleware(),
            VirtualPathCodeGuardMiddleware(),
            FilesystemMiddleware(
                backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
            ),
        ],
    )
    state = {"messages": [("user", "Run the file operations.")]}
    result = asyncio.run(graph.ainvoke(state)) if use_async else graph.invoke(state)
    return [m for m in result["messages"] if m.type == "tool"]


@pytest.mark.parametrize("use_async", [False, True])
def test_observed_aliases_write_edit_read_without_model_retry(tmp_path, use_async):
    results = run_calls(
        tmp_path,
        [
            {
                "name": "write_file",
                "args": {"path": "/result.json", "content": '{"total":0}'},
            },
            {
                "name": "edit_file",
                "args": {
                    "path": "/result.json",
                    "old_str": '"total":0',
                    "new_str": '"total":6',
                },
            },
            {"name": "read_file", "args": {"path": "/result.json"}},
        ],
        use_async,
    )
    assert [r.status for r in results] == ["success"] * 3
    assert (tmp_path / "result.json").read_text() == '{"total":6}'
    assert '"total":6' in results[-1].content
    assert results[0].additional_kwargs["argument_aliases"] == {"path": "file_path"}


def test_conflicting_paths_fail_without_writing(tmp_path):
    result = run_calls(
        tmp_path,
        [
            {
                "name": "write_file",
                "args": {
                    "path": "/wrong.txt",
                    "file_path": "/right.txt",
                    "content": "text",
                },
            }
        ],
    )[0]
    assert result.status == "error"
    assert "Conflicting" in result.content
    assert not list(tmp_path.iterdir())


def test_aliases_do_not_bypass_code_guard(tmp_path):
    (tmp_path / "script.py").write_text("pass\n")
    result = run_calls(
        tmp_path,
        [
            {
                "name": "edit_file",
                "args": {
                    "path": "/script.py",
                    "old_str": "pass",
                    "new_str": 'open("/work/data.csv")',
                },
            }
        ],
    )[0]
    assert result.status == "error"
    assert "CODE CONTRACT BLOCKED" in result.content
    assert (tmp_path / "script.py").read_text() == "pass\n"


def test_canonical_arguments_remain_unchanged(tmp_path):
    result = run_calls(
        tmp_path,
        [
            {
                "name": "write_file",
                "args": {
                    "file_path": "/result.txt",
                    "content": "correct",
                },
            }
        ],
    )[0]
    assert result.status == "success"
    assert "argument_aliases" not in result.additional_kwargs
    assert (tmp_path / "result.txt").read_text() == "correct"
