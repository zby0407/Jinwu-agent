from unittest.mock import MagicMock, patch

from jw.cli.agent import _load_agent
from jw.pi_bridge.graph import PiAgentGraph


class TestLoadAgentEngine:
    def test_load_agent_returns_pi_graph_when_configured(self, tmp_path):
        cfg = MagicMock()
        cfg.agent_engine = "pi"
        cfg.pi_provider = "dashscope"
        cfg.pi_model = "qwen-plus"
        cfg.pi_session_dir = ""
        cfg.pi_args = ""
        cfg.dashscope_api_key = "fake"

        with patch("jw.agent.create_cli_agent") as mock_langgraph:
            agent = _load_agent(workspace_dir=str(tmp_path), config=cfg)
            assert isinstance(agent, PiAgentGraph)
            mock_langgraph.assert_not_called()

    def test_load_agent_returns_langgraph_by_default(self, tmp_path):
        cfg = MagicMock()
        cfg.agent_engine = "langgraph"

        with patch("jw.agent.create_cli_agent") as mock_langgraph:
            mock_langgraph.return_value = MagicMock()
            agent = _load_agent(workspace_dir=str(tmp_path), config=cfg)
            assert agent is mock_langgraph.return_value
            mock_langgraph.assert_called_once()
