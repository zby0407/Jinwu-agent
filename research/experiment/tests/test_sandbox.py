from __future__ import annotations

import unittest

from automatic_experiment.executor import doctor
from automatic_experiment.policy import (
    CodePolicyError,
    scan_python,
    validate_code_files,
    verify_dependencies,
)
from automatic_experiment.state import PROJECT_ROOT
from tests.helpers import INPUT_MEAN_CODE, SUCCESS_CODE


class SandboxPolicyTests(unittest.TestCase):
    def test_doctor_proves_network_and_host_are_hidden(self) -> None:
        result = doctor()
        self.assertEqual(result["status"], "ready")
        probe = result["checks"]["sandbox_probe"]
        self.assertTrue(probe["ready"])
        self.assertFalse(probe["payload"]["host_visible"])
        self.assertFalse(probe["payload"]["network_connected"])

    def test_subprocess_import_is_rejected(self) -> None:
        with self.assertRaises(CodePolicyError):
            scan_python("import subprocess\ndef run_experiment(context): return {}\n")

    def test_socket_import_is_rejected(self) -> None:
        with self.assertRaises(CodePolicyError):
            scan_python("import socket\ndef run_experiment(context): return {}\n")

    def test_dynamic_exec_is_rejected(self) -> None:
        with self.assertRaises(CodePolicyError):
            scan_python("def run_experiment(context):\n    exec('x=1')\n")

    def test_package_install_marker_is_rejected(self) -> None:
        with self.assertRaises(CodePolicyError):
            scan_python("def run_experiment(context):\n    note='pip install x'\n")

    def test_reviewed_code_is_accepted(self) -> None:
        validate_code_files([{"path": "experiment.py", "content": SUCCESS_CODE}])

    def test_typed_result_contract_mismatch_is_rejected_before_execution(
        self,
    ) -> None:
        source = SUCCESS_CODE.replace(
            '"result_items": [],',
            '"result_items": [\n'
            '    {\n'
            '        "id": "fit_obs_count",\n'
            '        "display_name": "拟合观测数",\n'
            '        "value_kind": "text",\n'
            '        "value": "12",\n'
            '        "unit": "",\n'
            '        "role": "diagnostic",\n'
            '        "source_artifact": None\n'
            '    }\n'
            '],',
            1,
        )

        with self.assertRaisesRegex(
            CodePolicyError,
            "validated design requires 'count'",
        ):
            validate_code_files(
                [{"path": "experiment.py", "content": source}],
                ["numpy"],
                required_results={"fit_obs_count"},
                required_result_contracts={
                    "fit_obs_count": {
                        "display_name": "拟合观测数",
                        "value_kind": "count",
                        "unit": "",
                        "role": "diagnostic",
                    }
                },
            )

    def test_scientific_helper_return_is_not_mistaken_for_worker_return(self) -> None:
        source = SUCCESS_CODE.replace(
            "def run_experiment(context):\n",
            "def run_experiment(context):\n"
            "    def finite_mean(values):\n"
            "        return float(np.mean(values))\n",
            1,
        ).replace(
            "mean = float(np.mean(values))",
            "mean = finite_mean(values)",
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            ["numpy"],
            required_measurements={"mean"},
            required_endpoints={"mean_endpoint"},
            expected_artifacts={"summary.json"},
            primary_estimand="arithmetic mean",
        )

    def test_static_local_primary_estimand_is_accepted(self) -> None:
        source = SUCCESS_CODE.replace(
            "    values = np.array([1.0, 2.0, 3.0, 4.0])\n",
            "    values = np.array([1.0, 2.0, 3.0, 4.0])\n"
            "    estimand_text = 'arithmetic mean'\n",
            1,
        ).replace(
            '"primary_estimand": "arithmetic mean"',
            '"primary_estimand": estimand_text',
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            ["numpy"],
            required_measurements={"mean"},
            required_endpoints={"mean_endpoint"},
            expected_artifacts={"summary.json"},
            primary_estimand="arithmetic mean",
        )

    def test_authoring_placeholder_is_rejected(self) -> None:
        source = SUCCESS_CODE.replace(
            "mean = float(np.mean(values))",
            "mean = 0.0  # replace with the task computation",
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "authoring placeholder"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_missing_design_measurement_is_rejected_before_attempt(self) -> None:
        with self.assertRaisesRegex(CodePolicyError, "required by the design criteria"):
            validate_code_files(
                [{"path": "experiment.py", "content": SUCCESS_CODE}],
                ["numpy"],
                required_measurements={"mean", "robustness_delta"},
                required_endpoints={"mean_endpoint"},
                expected_artifacts={"summary.json"},
                primary_estimand="arithmetic mean",
            )

    def test_hardcoded_sandbox_data_path_is_rejected(self) -> None:
        source = SUCCESS_CODE.replace(
            "values = np.array([1.0, 2.0, 3.0, 4.0])",
            "input_path = 'inputs/input_01/example.csv'\n"
            "    with open(input_path, encoding='utf-8') as handle:\n"
            "        handle.read()\n"
            "    values = np.array([1.0, 2.0, 3.0, 4.0])",
        )
        with self.assertRaisesRegex(CodePolicyError, "hard-coded inputs/outputs"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_relative_input_path_is_rejected_before_execution(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            'context["input_path_by_id"]["input_01"]',
            '"input_01/example_mean.csv"',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "file access paths must derive"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_relative_output_path_is_rejected_before_execution(self) -> None:
        source = SUCCESS_CODE.replace(
            'context["output_dir"] / "summary.json"',
            '"summary.json"',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "file access paths must derive"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_wrapped_context_output_path_is_accepted(self) -> None:
        source = SUCCESS_CODE.replace(
            "import json\n",
            "import json\nfrom pathlib import Path\n",
            1,
        ).replace(
            'context["output_dir"] / "summary.json"',
            'str(Path(context["output_dir"]) / "summary.json")',
            1,
        )
        validate_code_files([{"path": "experiment.py", "content": source}])

    def test_wrapped_untrusted_output_path_is_rejected(self) -> None:
        source = SUCCESS_CODE.replace(
            "import json\n",
            "import json\nfrom pathlib import Path\n",
            1,
        ).replace(
            'context["output_dir"] / "summary.json"',
            'str(Path("summary.json"))',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "file access paths must derive"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_helper_accepts_path_only_when_all_call_sites_are_context_derived(
        self,
    ) -> None:
        source = INPUT_MEAN_CODE.replace(
            "def run_experiment(context):\n"
            '    input_path = context["input_path_by_id"]["input_01"]\n'
            "    values = []\n"
            '    with input_path.open("r", encoding="utf-8", newline="") as handle:\n'
            "        for row in csv.DictReader(handle):\n"
            '            values.append(float(row["value"]))\n',
            "def read_values(path):\n"
            "    values = []\n"
            '    with open(str(path), "r", encoding="utf-8", newline="") as handle:\n'
            "        for row in csv.DictReader(handle):\n"
            '            values.append(float(row["value"]))\n'
            "    return values\n\n"
            "def run_experiment(context):\n"
            '    input_path = context["input_path_by_id"]["input_01"]\n'
            "    values = read_values(input_path)\n",
            1,
        )
        validate_code_files([{"path": "experiment.py", "content": source}])

        unsafe = source.replace(
            "values = read_values(input_path)",
            'values = read_values("inputs/private.csv")',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "file access paths must derive"):
            validate_code_files([{"path": "experiment.py", "content": unsafe}])

    def test_json_load_accepts_a_context_derived_artifact_file_handle(self) -> None:
        source = SUCCESS_CODE.replace(
            "values = np.array([1.0, 2.0, 3.0, 4.0])",
            'audit_path = context["artifact_path_by_id"]["audit_artifact"]\n'
            '    with open(audit_path, "r", encoding="utf-8") as handle:\n'
            "        json.load(handle)\n"
            "    values = np.array([1.0, 2.0, 3.0, 4.0])",
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            required_consumed_artifacts={"audit_artifact"},
        )

    def test_incomplete_worker_result_contract_is_rejected(self) -> None:
        source = "def run_experiment(context):\n    return {'mean': 2.5}\n"
        with self.assertRaisesRegex(CodePolicyError, "full worker result contract"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_invalid_nested_worker_fields_are_rejected_before_execution(self) -> None:
        source = INPUT_MEAN_CODE.replace('"role": "primary"', '"role": "primary_estimand"')
        with self.assertRaisesRegex(CodePolicyError, "role must be primary"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_runtime_endpoint_status_variable_is_accepted(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            "return {",
            "endpoint_status = \"completed\"\n"
            "    if mean < 0:\n"
            "        endpoint_status = \"failed\"\n"
            "    return {",
            1,
        ).replace(
            '"status": "completed"',
            '"status": endpoint_status',
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            ["numpy"],
            required_measurements={"mean"},
            required_endpoints={"mean_endpoint"},
            expected_artifacts={"mean.json"},
            primary_estimand="arithmetic mean",
        )

    def test_literal_conditional_endpoint_status_is_accepted(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            "return {",
            'endpoint_status = "completed" if mean >= 0 else "failed"\n'
            "    return {",
            1,
        ).replace(
            '"status": "completed"',
            '"status": endpoint_status',
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            ["numpy"],
            required_measurements={"mean"},
            required_endpoints={"mean_endpoint"},
            expected_artifacts={"mean.json"},
            primary_estimand="arithmetic mean",
        )

    def test_reserved_worker_result_path_is_rejected_before_execution(self) -> None:
        source = SUCCESS_CODE.replace("summary.json", "result.json")
        with self.assertRaisesRegex(CodePolicyError, "reserved result.json"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_invalid_artifact_kind_lists_allowed_values(self) -> None:
        source = SUCCESS_CODE.replace('"kind": "json"', '"kind": "dataset"')

        with self.assertRaisesRegex(
            CodePolicyError,
            "allowed values: json, csv, text, markdown, image, fits, netcdf, "
            "hdf5, parquet, other",
        ):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_path_object_string_addition_is_rejected_before_execution(self) -> None:
        source = SUCCESS_CODE.replace(
            'context["output_dir"] / "summary.json"',
            'context["output_dir"] + "/summary.json"',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "pathlib.Path objects"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_input_file_list_must_be_indexed_before_opening(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            'input_path = context["input_path_by_id"]["input_01"]',
            'input_files = context["input_files"]\n'
            '    input_path = input_files["input_01"]',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "is a list"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_measurement_source_must_reference_declared_artifact(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            '"source_artifact": "mean.json"',
            '"source_artifact": "other.json"',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "reference a declared artifact"):
            validate_code_files([{"path": "experiment.py", "content": source}])

    def test_statically_fixed_source_artifact_variable_is_accepted(self) -> None:
        source = INPUT_MEAN_CODE.replace(
            "    return {",
            '    result_source = "mean.json"\n    return {',
            1,
        ).replace(
            '"source_artifact": "mean.json"',
            '"source_artifact": result_source',
            1,
        )
        validate_code_files(
            [{"path": "experiment.py", "content": source}],
            ["numpy"],
            required_measurements={"mean"},
            required_endpoints={"mean_endpoint"},
            expected_artifacts={"mean.json"},
            primary_estimand="arithmetic mean",
        )

        dynamic = source.replace(
            'result_source = "mean.json"',
            'result_source = str("mean.json")',
            1,
        )
        with self.assertRaisesRegex(CodePolicyError, "string literal or null"):
            validate_code_files([{"path": "experiment.py", "content": dynamic}])

    def test_third_party_import_must_be_declared(self) -> None:
        with self.assertRaisesRegex(CodePolicyError, "undeclared dependencies"):
            validate_code_files(
                [{"path": "experiment.py", "content": SUCCESS_CODE}],
                [],
            )
        validate_code_files(
            [{"path": "experiment.py", "content": SUCCESS_CODE}],
            ["numpy"],
        )

    def test_safe_standard_library_dependencies_are_accepted(self) -> None:
        verify_dependencies(["csv", "json", "math", "pathlib", "statistics"])

    def test_unreviewed_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(CodePolicyError, "unavailable or unreviewed"):
            verify_dependencies(["not_a_reviewed_package"])

    def test_process_limit_is_applied_inside_namespace(self) -> None:
        runner = (
            PROJECT_ROOT / "src" / "automatic_experiment" / "sandbox_runner.sh"
        ).read_text(encoding="utf-8")
        self.assertLess(
            runner.index('bwrap "${bwrap_args[@]}"'),
            runner.index("/usr/bin/prlimit"),
        )
        self.assertIn("--nproc=32", runner)


if __name__ == "__main__":
    unittest.main()
