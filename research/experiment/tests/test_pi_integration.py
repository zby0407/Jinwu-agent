from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from automatic_experiment.state import PROJECT_ROOT


@unittest.skipUnless(
    (PROJECT_ROOT / ".pi").is_dir(),
    "standalone Pi bundle is not part of the integrated repository",
)
class PiIntegrationTests(unittest.TestCase):
    def test_model_and_thinking_are_fixed(self) -> None:
        settings = json.loads((PROJECT_ROOT / ".pi" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["defaultProvider"], "dashscope")
        self.assertEqual(settings["defaultModel"], "qwen3.7-max-2026-06-08")
        self.assertEqual(settings["defaultThinkingLevel"], "high")

    def test_exactly_seven_tools_are_registered(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        self.assertEqual(source.count("pi.registerTool({"), 7)
        for name in (
            "automatic_experiment_bind_request",
            "automatic_experiment_inspect_inputs",
            "automatic_experiment_validate_design",
            "automatic_experiment_prepare_attempt",
            "automatic_experiment_execute_attempt",
            "automatic_experiment_verify_result",
            "automatic_experiment_finalize",
        ):
            self.assertIn(name, source)

    def test_required_commands_are_registered(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        for command in (
            "automatic-experiment",
            "automatic-experiment-status",
            "automatic-experiment-stop",
            "automatic-experiment-doctor",
        ):
            self.assertIn(f'pi.registerCommand("{command}"', source)

    def test_ordinary_request_never_enters_history_choice(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        bridge = (PROJECT_ROOT / "src" / "automatic_experiment" / "bridge.py").read_text(
            encoding="utf-8"
        )
        service = (PROJECT_ROOT / "src" / "automatic_experiment" / "service.py").read_text(
            encoding="utf-8"
        )
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('| "prepare-replay"', source)
        self.assertIn("pendingRequestInput = requestInput", source)
        self.assertIn("(?:重放|replay)", source)
        self.assertNotIn('| "lookup"', source)
        self.assertNotIn('runBridge("lookup"', source)
        self.assertNotIn("ctx.ui.select(", source)
        self.assertNotIn("生命周期操作", source)
        self.assertNotIn("重放最近一次已验证设计", source)
        self.assertNotIn("让千问重新设计并比较历史设计", source)
        self.assertNotIn("/automatic-experiment 新设计", source)
        self.assertNotIn("independent_redesign", source)
        self.assertNotIn('if mode == "lookup"', bridge)
        self.assertNotIn('"lookup",', bridge)
        self.assertNotIn("def lookup_request(", service)
        self.assertIn("普通任务每次都由千问根据当前问题重新设计", readme)
        self.assertIn("不会由系统自动弹出", readme)

    def test_exact_replay_bypasses_model_redesign_steps(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("真实重放已由确定性核心准备完成", source)
        self.assertIn("不要重新绑定、检查输入、设计或构建代码", source)
        self.assertIn("preparedReplayAttemptId", source)

    def test_finalize_disables_tools_for_the_rest_of_the_turn(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        finalize_block = source[source.index('name: "automatic_experiment_finalize"') :]
        self.assertIn("pi.setActiveTools([])", finalize_block)
        self.assertIn("already_finalized_this_turn", source)
        self.assertIn("若进入终态，立即生成正式报告", source)

    def test_nested_objects_progress_continue_and_interrupted_report_are_wired(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        for legacy_name in (
            "response_json",
            "design_json",
            "files_json",
            "scientific_assessment_json",
        ):
            self.assertNotIn(legacy_name, source)
        self.assertIn('Type.Object({}, { additionalProperties: true })', source)
        self.assertIn("assessment_preview_required", source)
        self.assertIn("verificationPreviewAttemptId", source)
        self.assertIn(
            "第一次调用核验工具时必须省略 scientific_assessment", source
        )
        self.assertIn("setInterval(publish, 30_000)", source)
        self.assertIn('| "prepare-continuation"', source)
        self.assertIn('| "finalize-interrupted"', source)
        self.assertIn("(?:继续|continue|resume)", source)
        self.assertIn("运行已停止，正式报告已生成", source)
        self.assertIn(
            "activeRunId === null && pendingRequestInput !== null", source
        )
        self.assertIn("模型服务在实验开始前不可用", source)
        self.assertIn('event.message.stopReason === "error"', source)
        self.assertIn('pi.on("agent_settled"', source)
        self.assertIn("retryableModelErrorPending", source)
        self.assertIn("Pi 自动重试后仍未恢复", source)
        self.assertIn('customType: "automatic-experiment-interrupted-report"', source)
        self.assertIn("display: true", source)
        runner = (PROJECT_ROOT / "tests" / "pi_rpc_live_runner.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn("let inputClosed = false", runner)
        self.assertEqual(runner.count("child.stdin.end();"), 1)
        self.assertIn('event.type === "auto_retry_start"', runner)
        self.assertIn('event.type === "agent_settled"', runner)
        self.assertLess(
            runner.index('event.type === "auto_retry_start"'),
            runner.index('event.type === "agent_settled"'),
        )

    def test_prompt_does_not_force_a_model_split_metric_stack(self) -> None:
        source = (
            PROJECT_ROOT / ".pi" / "extensions" / "automatic-experiment" / "index.ts"
        ).read_text(encoding="utf-8")
        prompt = source[source.index("const TURN_INSTRUCTIONS") : source.index("function isWithin")]
        self.assertIn("不得默认加入模型、数据切分、基线、机器学习、消融或稳健性", prompt)
        self.assertNotIn("至少分别解释模型/计算形式、数据切分和主要指标", prompt)

    def test_pi_offline_loads_qwen_provider(self) -> None:
        command = shutil.which("pi.cmd") or shutil.which("pi")
        self.assertIsNotNone(command, "Pi executable was not found")
        completed = subprocess.run(
            [command, "--offline", "--list-models", "qwen3.7"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("qwen3.7-max-2026-06-08", completed.stdout)


if __name__ == "__main__":
    unittest.main()
