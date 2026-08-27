"""Static code and budget policy used before any sandbox starts."""

from __future__ import annotations

import ast
import re

from .contracts import ContractError

ALLOWED_IMPORTS = {
    "collections",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "matplotlib",
    "astropy",
    "h5netcdf",
    "h5py",
    "numpy",
    "pandas",
    "pyarrow",
    "pathlib",
    "random",
    "scipy",
    "sklearn",
    "statistics",
    "typing",
    "xarray",
}
THIRD_PARTY_IMPORTS = {
    "jsonschema",
    "astropy",
    "h5netcdf",
    "h5py",
    "matplotlib",
    "numpy",
    "pandas",
    "pyarrow",
    "scipy",
    "sklearn",
    "xarray",
}
SAFE_STANDARD_LIBRARY_IMPORTS = ALLOWED_IMPORTS - THIRD_PARTY_IMPORTS
DENIED_MODULES = {
    "asyncio",
    "ctypes",
    "http",
    "importlib",
    "multiprocessing",
    "os",
    "pickle",
    "requests",
    "resource",
    "runpy",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sysconfig",
    "urllib",
}
DENIED_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint"}
DENIED_ATTRIBUTES = {
    "system",
    "popen",
    "spawn",
    "fork",
    "forkpty",
    "execv",
    "execve",
    "kill",
    "killpg",
}
COMMAND_MARKERS = re.compile(
    r"(?:pip\s+install|conda\s+install|apt(?:-get)?\s+install|curl\s+https?://|"
    r"wget\s+https?://|powershell(?:\.exe)?|cmd(?:\.exe)?|bash\s+-c|sh\s+-c)",
    re.IGNORECASE,
)
SAFE_CODE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,199}$")
ALLOWED_FILE_SUFFIXES = {".py", ".json", ".md", ".txt"}
ALLOWED_SCIENTIFIC_ARTIFACT_KINDS = (
    "json",
    "csv",
    "text",
    "markdown",
    "image",
    "fits",
    "netcdf",
    "hdf5",
    "parquet",
    "other",
)
WORKER_RESULT_MARKERS = {
    "automatic-experiment-worker-result-v1",
    "execution_completed",
    "measurements",
    "result_items",
    "artifacts",
    "warnings",
    "endpoint_results",
    "scientific_payload",
}
FILE_ACCESS_ATTRIBUTES = {
    "open",
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "read_csv",
    "read_json",
    "read_parquet",
    "open_dataset",
    "open_mfdataset",
    "open_dataarray",
    "read_excel",
    "load",
    "save",
}
TEMPORAL_PREDICTION_MARKERS = re.compile(
    r"(?:predict|forecast|backtest|loocv|time[-_ ]series|early[-_ ]rise|"
    r"预测|回测|留一|前\s*\d+\s*个?月|早期上升)",
    re.IGNORECASE,
)


class CodePolicyError(ContractError):
    """Generated code requests a capability outside the execution policy."""


class CodeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.has_entrypoint = False
        self.imports: set[str] = set()
        self.hardcoded_path_names: set[str] = set()
        self.input_files_mapping_names: set[str] = set()
        self.input_file_list_names: set[str] = set()
        self.path_object_names: set[str] = set()
        self.trusted_helper_parameters: dict[str, set[str]] = {}
        self.path_scope_stack: list[set[str]] = []

    @staticmethod
    def _subscript_key(node: ast.AST) -> str | None:
        if not isinstance(node, ast.Subscript):
            return None
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return None

    @classmethod
    def _is_context_key(cls, node: ast.AST, key: str) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "context"
            and cls._subscript_key(node) == key
        )

    def _is_input_file_list(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Subscript) and (
            self._is_context_key(node.value, "input_files")
            or (
                isinstance(node.value, ast.Name)
                and node.value.id in self.input_files_mapping_names
            )
        )

    def _is_context_path(self, node: ast.AST) -> bool:
        if self._is_context_key(node, "output_dir"):
            return True
        if isinstance(node, ast.Subscript) and (
            self._is_context_key(node.value, "input_path_by_id")
            or self._is_context_key(node.value, "artifact_path_by_id")
        ):
            return True
        return (
            isinstance(node, ast.Subscript)
            and self._is_input_file_list(node.value)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        )

    def _is_trusted_path_expr(self, node: ast.AST) -> bool:
        if self._is_context_path(node):
            return True
        if isinstance(node, ast.Name):
            return node.id in self.path_object_names or any(
                node.id in scope for scope in self.path_scope_stack
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"Path", "str"}
            and len(node.args) == 1
            and not node.keywords
        ):
            return self._is_trusted_path_expr(node.args[0])
        return (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and self._is_trusted_path_expr(node.left)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
        )

    @staticmethod
    def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [target.id for target in targets if isinstance(target, ast.Name)]

    @staticmethod
    def _is_hardcoded_sandbox_path(value: str) -> bool:
        normalized = value.replace("\\", "/").lstrip("./").casefold()
        return normalized in {"inputs", "outputs"} or normalized.startswith(
            ("inputs/", "outputs/", "workspace/input", "workspace/output")
        )

    def _contains_hardcoded_path(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and self._is_hardcoded_sandbox_path(child.value)
            ):
                return True
            if isinstance(child, ast.Name) and child.id in self.hardcoded_path_names:
                return True
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        assigned = self._assigned_names(node)
        if self._is_context_key(node.value, "input_files"):
            self.input_files_mapping_names.update(assigned)
        if self._is_input_file_list(node.value):
            self.input_file_list_names.update(assigned)
        if self._is_trusted_path_expr(node.value):
            target = (
                self.path_scope_stack[-1]
                if self.path_scope_stack
                else self.path_object_names
            )
            target.update(assigned)
        if self._contains_hardcoded_path(node.value):
            self.hardcoded_path_names.update(assigned)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        assigned = self._assigned_names(node)
        if node.value is not None and self._is_context_key(node.value, "input_files"):
            self.input_files_mapping_names.update(assigned)
        if node.value is not None and self._is_input_file_list(node.value):
            self.input_file_list_names.update(assigned)
        if node.value is not None and self._is_trusted_path_expr(node.value):
            target = (
                self.path_scope_stack[-1]
                if self.path_scope_stack
                else self.path_object_names
            )
            target.update(assigned)
        if node.value is not None and self._contains_hardcoded_path(node.value):
            self.hardcoded_path_names.update(assigned)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add):
            left_is_path = self._is_context_path(node.left) or (
                isinstance(node.left, ast.Name)
                and node.left.id in self.path_object_names
            )
            right_is_path = self._is_context_path(node.right) or (
                isinstance(node.right, ast.Name)
                and node.right.id in self.path_object_names
            )
            left_is_text = isinstance(node.left, ast.Constant) and isinstance(
                node.left.value, str
            )
            right_is_text = isinstance(node.right, ast.Constant) and isinstance(
                node.right.value, str
            )
            if (left_is_path and right_is_text) or (right_is_path and left_is_text):
                self.errors.append(
                    f"line {node.lineno}: context paths are pathlib.Path objects; "
                    "join them with the / operator"
                )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.errors.append(f"line {node.lineno}: relative imports are not allowed")
        self._check_module(node.module or "", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in DENIED_CALLS:
            self.errors.append(
                f"line {node.lineno}: call to {node.func.id} is not allowed"
            )
        if isinstance(node.func, ast.Attribute) and node.func.attr in DENIED_ATTRIBUTES:
            self.errors.append(
                f"line {node.lineno}: call to .{node.func.attr} is not allowed"
            )
        json_stream_load = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "load"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
        )
        file_access = (isinstance(node.func, ast.Name) and node.func.id == "open") or (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in FILE_ACCESS_ATTRIBUTES
            and not json_stream_load
        )
        if file_access:
            inspected: ast.AST | None
            if isinstance(node.func, ast.Name):
                inspected = node.args[0] if node.args else None
            elif node.func.attr in {
                "read_csv",
                "read_json",
                "read_parquet",
                "read_excel",
                "load",
                "save",
            }:
                inspected = node.args[0] if node.args else None
            else:
                inspected = node.func.value
            if (
                isinstance(inspected, ast.Name)
                and inspected.id in self.input_file_list_names
            ):
                self.errors.append(
                    f"line {node.lineno}: context['input_files'][input_id] is a list; "
                    "select one Path or use context['input_path_by_id'][input_id]"
                )
            elif inspected is not None and not self._is_trusted_path_expr(inspected):
                self.errors.append(
                    f"line {node.lineno}: file access paths must derive from "
                    "context['input_path_by_id'], context['input_files'][...][index], "
                    "context['artifact_path_by_id'], or context['output_dir']"
                )
            if inspected is not None and self._contains_hardcoded_path(inspected):
                self.errors.append(
                    f"line {node.lineno}: hard-coded inputs/outputs paths are not allowed; "
                    "use context['input_files'] and context['output_dir']"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr not in {"__name__"}:
            self.errors.append(
                f"line {node.lineno}: dunder attribute access is not allowed"
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name == "run_experiment":
            self.has_entrypoint = True
        self.path_scope_stack.append(
            set(self.trusted_helper_parameters.get(node.name, set()))
        )
        try:
            self.generic_visit(node)
        finally:
            self.path_scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name == "run_experiment":
            self.errors.append("run_experiment must be synchronous")
        self.generic_visit(node)

    def _check_module(self, module: str, line: int) -> None:
        root = module.split(".", 1)[0]
        if root:
            self.imports.add(root)
        if root in DENIED_MODULES or root not in ALLOWED_IMPORTS:
            self.errors.append(
                f"line {line}: import {root or '<relative>'} is not allowed"
            )

    def prepare_trusted_helper_paths(self, tree: ast.AST) -> None:
        """Prove helper path parameters from every call in run_experiment."""

        entrypoint = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
            ),
            None,
        )
        if entrypoint is None:
            return
        entry_nodes = _entrypoint_nodes(entrypoint)
        changed = True
        while changed:
            changed = False
            for node in entry_nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if value is None or not self._is_trusted_path_expr(value):
                    continue
                for name in self._assigned_names(node):
                    if name not in self.path_object_names:
                        self.path_object_names.add(name)
                        changed = True
        helpers = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name != "run_experiment"
        }
        calls: dict[str, list[ast.Call]] = {name: [] for name in helpers}
        for node in entry_nodes:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in calls
            ):
                calls[node.func.id].append(node)
        for name, helper in helpers.items():
            helper_calls = calls[name]
            if not helper_calls:
                continue
            positional = [
                *helper.args.posonlyargs,
                *helper.args.args,
            ]
            for index, parameter in enumerate(positional):
                if all(
                    index < len(call.args)
                    and self._is_trusted_path_expr(call.args[index])
                    for call in helper_calls
                ):
                    self.trusted_helper_parameters.setdefault(name, set()).add(
                        parameter.arg
                    )


def _literal_dict(node: ast.AST) -> dict[str, ast.AST] | None:
    result: dict[str, ast.AST] = {}
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                return None
            result[key.value] = value
        return result
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
        and not node.args
        and all(keyword.arg is not None for keyword in node.keywords)
    ):
        for keyword in node.keywords:
            result[str(keyword.arg)] = keyword.value
        return result
    return None


def _entrypoint_nodes(entrypoint: ast.FunctionDef) -> list[ast.AST]:
    """Walk run_experiment while excluding nested helpers and classes."""

    rows: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return
        rows.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in entrypoint.body:
        visit(statement)
    return rows


def _entrypoint_assignments(entrypoint: ast.FunctionDef) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}
    for node in _entrypoint_nodes(entrypoint):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assignments[node.target.id] = node.value
    return assignments


def _static_assignments(
    tree: ast.AST, entrypoint: ast.FunctionDef
) -> dict[str, ast.AST]:
    """Collect statically resolvable module and entrypoint assignments.

    Generated workers commonly keep repeated contract strings as module-level
    constants.  They are as static as function-local constants, so include
    them while letting local assignments shadow the module value.
    """

    assignments: dict[str, ast.AST] = {}
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    assignments[node.target.id] = node.value
    assignments.update(_entrypoint_assignments(entrypoint))
    return assignments


def _resolve_static(
    node: ast.AST | None, assignments: dict[str, ast.AST]
) -> ast.AST | None:
    current = node
    seen: set[str] = set()
    while isinstance(current, ast.Name) and current.id in assignments:
        if current.id in seen:
            return None
        seen.add(current.id)
        current = assignments[current.id]
    return current


def _static_string_choices(
    node: ast.AST | None,
    assignments: dict[str, ast.AST],
) -> set[str] | None:
    """Resolve a bounded literal string or a literal conditional expression."""

    resolved = _resolve_static(node, assignments)
    if isinstance(resolved, ast.Constant) and isinstance(resolved.value, str):
        return {resolved.value}
    if isinstance(resolved, ast.IfExp):
        left = _static_string_choices(resolved.body, assignments)
        right = _static_string_choices(resolved.orelse, assignments)
        if left is not None and right is not None:
            return left | right
    return None


def _field_errors(
    row: dict[str, ast.AST],
    expected: set[str],
    label: str,
) -> list[str]:
    missing = sorted(expected - set(row))
    unknown = sorted(set(row) - expected)
    if not missing and not unknown:
        return []
    return [f"{label} fields invalid: missing={missing}, unknown={unknown}"]


def _validate_worker_return_literals(tree: ast.AST) -> list[str]:
    entrypoint = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
        ),
        None,
    )
    if entrypoint is None:
        return []
    entrypoint_nodes = _entrypoint_nodes(entrypoint)
    returns = [node for node in entrypoint_nodes if isinstance(node, ast.Return)]
    if not returns:
        return ["run_experiment must directly return a worker result dictionary"]
    assignments = _static_assignments(tree, entrypoint)
    errors: list[str] = []
    top_fields = {
        "schema_version",
        "execution_completed",
        "measurements",
        "result_items",
        "artifacts",
        "warnings",
        "endpoint_results",
        "scientific_payload",
    }
    for return_index, return_node in enumerate(returns):
        artifact_paths: set[str] = set()
        measurement_sources: list[tuple[str, str]] = []
        returned = _resolve_static(return_node.value, assignments)
        row = _literal_dict(returned) if returned is not None else None
        label = f"run_experiment return[{return_index}]"
        if row is None:
            errors.append(
                f"{label} must be a worker-result dictionary literal or a name assigned to one"
            )
            continue
        errors.extend(_field_errors(row, top_fields, label))
        schema = row.get("schema_version")
        if not (
            isinstance(schema, ast.Constant)
            and schema.value == "automatic-experiment-worker-result-v1"
        ):
            errors.append(
                f"{label}.schema_version must be automatic-experiment-worker-result-v1"
            )
        completed = row.get("execution_completed")
        if not (isinstance(completed, ast.Constant) and completed.value is True):
            errors.append(f"{label}.execution_completed must be True")

        measurements = _resolve_static(row.get("measurements"), assignments)
        if isinstance(measurements, ast.List):
            for index, item in enumerate(measurements.elts):
                measurement = _literal_dict(_resolve_static(item, assignments) or item)
                item_label = f"{label}.measurements[{index}]"
                if measurement is None:
                    errors.append(f"{item_label} must be a dictionary literal")
                    continue
                errors.extend(
                    _field_errors(
                        measurement,
                        {"name", "value", "unit", "role", "source_artifact"},
                        item_label,
                    )
                )
                role = measurement.get("role")
                if not (
                    isinstance(role, ast.Constant)
                    and role.value in {"primary", "secondary", "diagnostic"}
                ):
                    errors.append(
                        f"{item_label}.role must be primary, secondary, or diagnostic"
                    )
                source = measurement.get("source_artifact")
                source = _resolve_static(source, assignments) or source
                if isinstance(source, ast.Constant):
                    if source.value is not None and not isinstance(source.value, str):
                        errors.append(
                            f"{item_label}.source_artifact must be a string or null"
                        )
                    elif isinstance(source.value, str):
                        measurement_sources.append((item_label, source.value))
                elif source is not None:
                    errors.append(
                        f"{item_label}.source_artifact must be a string literal or null"
                    )
        elif measurements is not None and not isinstance(measurements, ast.Name):
            errors.append(f"{label}.measurements must be an array")

        result_items = _resolve_static(row.get("result_items"), assignments)
        if isinstance(result_items, ast.List):
            for index, item in enumerate(result_items.elts):
                result_item = _literal_dict(_resolve_static(item, assignments) or item)
                item_label = f"{label}.result_items[{index}]"
                if result_item is None:
                    errors.append(f"{item_label} must be a dictionary literal")
                    continue
                errors.extend(
                    _field_errors(
                        result_item,
                        {
                            "id",
                            "display_name",
                            "value_kind",
                            "value",
                            "unit",
                            "role",
                            "source_artifact",
                        },
                        item_label,
                    )
                )
                kind = result_item.get("value_kind")
                if not (
                    isinstance(kind, ast.Constant)
                    and kind.value in {"number", "count", "boolean", "category", "text"}
                ):
                    errors.append(
                        f"{item_label}.value_kind must be number, count, boolean, "
                        "category, or text"
                    )
                source = result_item.get("source_artifact")
                source = _resolve_static(source, assignments) or source
                if isinstance(source, ast.Constant):
                    if source.value is not None and not isinstance(source.value, str):
                        errors.append(
                            f"{item_label}.source_artifact must be a string or null"
                        )
                    elif isinstance(source.value, str):
                        measurement_sources.append((item_label, source.value))
                elif source is not None:
                    errors.append(
                        f"{item_label}.source_artifact must be a string literal or null"
                    )
        elif result_items is not None and not isinstance(result_items, ast.Name):
            errors.append(f"{label}.result_items must be an array")

        artifacts = _resolve_static(row.get("artifacts"), assignments)
        if isinstance(artifacts, ast.List):
            for index, item in enumerate(artifacts.elts):
                artifact = _literal_dict(_resolve_static(item, assignments) or item)
                item_label = f"{label}.artifacts[{index}]"
                if artifact is None:
                    errors.append(f"{item_label} must be a dictionary literal")
                    continue
                errors.extend(
                    _field_errors(artifact, {"path", "kind", "description"}, item_label)
                )
                path = artifact.get("path")
                if not (isinstance(path, ast.Constant) and isinstance(path.value, str)):
                    errors.append(
                        f"{item_label}.path must be a relative string literal"
                    )
                else:
                    normalized = path.value.replace("\\", "/")
                    if (
                        normalized.startswith("/")
                        or ".." in normalized.split("/")
                        or normalized.rsplit("/", 1)[-1] == "result.json"
                    ):
                        errors.append(
                            f"{item_label}.path must be safe and cannot use reserved result.json"
                        )
                    artifact_paths.add(normalized)
                kind = artifact.get("kind")
                if not (
                    isinstance(kind, ast.Constant)
                    and kind.value in ALLOWED_SCIENTIFIC_ARTIFACT_KINDS
                ):
                    errors.append(
                        f"{item_label}.kind is not an allowed scientific artifact kind; "
                        "allowed values: "
                        + ", ".join(ALLOWED_SCIENTIFIC_ARTIFACT_KINDS)
                    )
        elif artifacts is not None and not isinstance(artifacts, ast.Name):
            errors.append(f"{label}.artifacts must be an array")
        if isinstance(artifacts, ast.List):
            for item_label, source in measurement_sources:
                if source.replace("\\", "/") not in artifact_paths:
                    errors.append(
                        f"{item_label}.source_artifact must reference a declared artifact"
                    )

        endpoints = _resolve_static(row.get("endpoint_results"), assignments)
        if isinstance(endpoints, ast.List):
            for index, item in enumerate(endpoints.elts):
                endpoint = _literal_dict(_resolve_static(item, assignments) or item)
                item_label = f"{label}.endpoint_results[{index}]"
                if endpoint is None:
                    errors.append(f"{item_label} must be a dictionary literal")
                    continue
                errors.extend(
                    _field_errors(endpoint, {"id", "status", "summary"}, item_label)
                )
                # Endpoint success is a runtime scientific fact, so generated code
                # may compute it into a local variable. Resolve reviewed local
                # assignments here; the worker-result contract validates the value
                # again after execution.
                status_choices = _static_string_choices(
                    endpoint.get("status"), assignments
                )
                if not status_choices or not status_choices.issubset(
                    {"completed", "failed", "not_evaluated"}
                ):
                    errors.append(
                        f"{item_label}.status must resolve to completed, failed, or "
                        "not_evaluated"
                    )
        elif endpoints is not None and not isinstance(endpoints, ast.Name):
            errors.append(f"{label}.endpoint_results must be an array")

        scientific = _resolve_static(row.get("scientific_payload"), assignments)
        if scientific is not None:
            scientific_row = _literal_dict(scientific)
            if scientific_row is None:
                if not isinstance(scientific, ast.Name):
                    errors.append(f"{label}.scientific_payload must be a dictionary")
            else:
                errors.extend(
                    _field_errors(
                        scientific_row,
                        {
                            "primary_estimand",
                            "estimate",
                            "interval",
                            "equivalence_bounds",
                            "sensitivity",
                            "uncertainty_reasons",
                        },
                        f"{label}.scientific_payload",
                    )
                )
                sensitivity = _resolve_static(
                    scientific_row.get("sensitivity"), assignments
                )
                if isinstance(sensitivity, ast.Constant):
                    if sensitivity.value is not None and not isinstance(
                        sensitivity.value, str
                    ):
                        errors.append(
                            f"{label}.scientific_payload.sensitivity must be a string or null"
                        )
                elif isinstance(sensitivity, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
                    errors.append(
                        f"{label}.scientific_payload.sensitivity must be a string or null"
                    )
                uncertainty_reasons = _resolve_static(
                    scientific_row.get("uncertainty_reasons"), assignments
                )
                if isinstance(
                    uncertainty_reasons,
                    (ast.Constant, ast.Tuple, ast.Dict, ast.Set),
                ):
                    errors.append(
                        f"{label}.scientific_payload.uncertainty_reasons must be an array"
                    )
    return errors


def _declared_worker_refs(
    tree: ast.AST,
) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    measurement_fields = {"name", "value", "unit", "role", "source_artifact"}
    artifact_fields = {"path", "kind", "description"}
    result_fields = {
        "id",
        "display_name",
        "value_kind",
        "value",
        "unit",
        "role",
        "source_artifact",
    }
    endpoint_fields = {"id", "status", "summary"}
    scientific_fields = {
        "primary_estimand",
        "estimate",
        "interval",
        "equivalence_bounds",
        "sensitivity",
        "uncertainty_reasons",
    }
    measurements: set[str] = set()
    artifacts: set[str] = set()
    results: set[str] = set()
    endpoints: set[str] = set()
    estimands: set[str] = set()
    entrypoint = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
        ),
        None,
    )
    assignments = (
        _static_assignments(tree, entrypoint) if entrypoint is not None else {}
    )
    for node in ast.walk(tree):
        row = _literal_dict(node)
        if row is None:
            continue
        keys = set(row)
        if measurement_fields.issubset(keys):
            value = row["name"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                measurements.add(value.value)
        if artifact_fields.issubset(keys):
            value = row["path"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                artifacts.add(value.value.replace("\\", "/"))
        if result_fields.issubset(keys):
            value = row["id"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                results.add(value.value)
        if endpoint_fields.issubset(keys):
            value = row["id"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                endpoints.add(value.value)
        if scientific_fields.issubset(keys):
            value = _resolve_static(row["primary_estimand"], assignments)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                estimands.add(value.value)
    return measurements, results, artifacts, endpoints, estimands


def _declared_worker_result_contracts(
    tree: ast.AST,
) -> dict[str, dict[str, set[str] | None]]:
    """Read statically declared typed-result fields from the worker return."""

    result_fields = {
        "id",
        "display_name",
        "value_kind",
        "value",
        "unit",
        "role",
        "source_artifact",
    }
    entrypoint = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
        ),
        None,
    )
    if entrypoint is None:
        return {}
    assignments = _static_assignments(tree, entrypoint)
    contracts: dict[str, dict[str, set[str] | None]] = {}
    for node in ast.walk(entrypoint):
        row = _literal_dict(node)
        if row is None or not result_fields.issubset(row):
            continue
        result_ids = _static_string_choices(row["id"], assignments)
        if not result_ids:
            continue
        for result_id in result_ids:
            contracts[result_id] = {
                field: _static_string_choices(row[field], assignments)
                for field in ("display_name", "value_kind", "unit", "role")
            }
    return contracts


def _declared_worker_measurement_contracts(
    tree: ast.AST,
) -> dict[str, dict[str, set[str] | None]]:
    """Read statically declared measurement units and roles."""

    measurement_fields = {"name", "value", "unit", "role", "source_artifact"}
    entrypoint = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
        ),
        None,
    )
    if entrypoint is None:
        return {}
    assignments = _static_assignments(tree, entrypoint)
    contracts: dict[str, dict[str, set[str] | None]] = {}
    for node in ast.walk(entrypoint):
        row = _literal_dict(node)
        if row is None or not measurement_fields.issubset(row):
            continue
        names = _static_string_choices(row["name"], assignments)
        if not names:
            continue
        for name in names:
            contracts[name] = {
                field: _static_string_choices(row[field], assignments)
                for field in ("unit", "role")
            }
    return contracts


def _literal_subscript_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    value = node.slice
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _consumed_artifact_refs(tree: ast.AST) -> set[str]:
    """Find literal reads from context['artifact_path_by_id']."""

    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if (
            isinstance(value, ast.Subscript)
            and isinstance(value.value, ast.Name)
            and value.value.id == "context"
            and _literal_subscript_key(value) == "artifact_path_by_id"
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            aliases.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )

    references: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        artifact_id = _literal_subscript_key(node)
        if artifact_id is None:
            continue
        mapping = node.value
        if isinstance(mapping, ast.Name) and mapping.id in aliases:
            references.add(artifact_id)
            continue
        if (
            isinstance(mapping, ast.Subscript)
            and isinstance(mapping.value, ast.Name)
            and mapping.value.id == "context"
            and _literal_subscript_key(mapping) == "artifact_path_by_id"
        ):
            references.add(artifact_id)
    return references


def scan_python(
    source: str,
    label: str = "experiment.py",
    *,
    required_measurements: set[str] | None = None,
    required_measurement_contracts: dict[str, dict[str, str]] | None = None,
    required_results: set[str] | None = None,
    required_result_contracts: dict[str, dict[str, str]] | None = None,
    required_endpoints: set[str] | None = None,
    expected_artifacts: set[str] | None = None,
    required_consumed_artifacts: set[str] | None = None,
    primary_estimand: str | None = None,
) -> set[str]:
    if len(source.encode("utf-8")) > 512 * 1024:
        raise CodePolicyError(f"{label} exceeds 512 KiB")
    if COMMAND_MARKERS.search(source):
        raise CodePolicyError(
            f"{label} contains a package, network, or shell command marker"
        )
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        raise CodePolicyError(
            f"{label} has invalid Python syntax: line {exc.lineno}"
        ) from exc
    visitor = CodeVisitor()
    visitor.prepare_trusted_helper_paths(tree)
    visitor.visit(tree)
    if label == "experiment.py" and not visitor.has_entrypoint:
        visitor.errors.append("experiment.py must define run_experiment(context)")
    if label == "experiment.py":
        entrypoint = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "run_experiment"
            ),
            None,
        )
        if entrypoint is not None:
            for node in _entrypoint_nodes(entrypoint):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                broad = node.type is None or (
                    isinstance(node.type, ast.Name)
                    and node.type.id in {"Exception", "BaseException"}
                )
                if broad:
                    visitor.errors.append(
                        f"line {node.lineno}: broad exception handling inside "
                        "run_experiment can hide missing inputs or replace real failures "
                        "with fabricated fallback results; catch only the expected "
                        "specific exception and let other failures terminate"
                    )
        if TEMPORAL_PREDICTION_MARKERS.search(source):
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "rolling"
                    and any(
                        keyword.arg == "center"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                        for keyword in node.keywords
                    )
                ):
                    visitor.errors.append(
                        f"line {node.lineno}: centered rolling windows use future "
                        "observations and are not allowed in temporal prediction features"
                    )
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                target_names = {
                    target.id.casefold()
                    for target in targets
                    if isinstance(target, ast.Name)
                }
                if not any(
                    marker in target
                    for target in target_names
                    for marker in ("cutoff", "end_idx", "window_end", "feature_end")
                ):
                    continue
                value = node.value
                if value is not None and any(
                    isinstance(child, ast.Name)
                    and any(
                        marker in child.id.casefold()
                        for marker in ("peak", "future", "max_idx")
                    )
                    for child in ast.walk(value)
                ):
                    visitor.errors.append(
                        f"line {node.lineno}: a prediction feature cutoff cannot depend "
                        "on a future peak, maximum, or future index"
                    )
        placeholder_markers = {
            "replace with the task computation",
            "'TASK ESTIMAND'",
            '"TASK ESTIMAND"',
        }
        if any(marker in source for marker in placeholder_markers):
            visitor.errors.append(
                "experiment.py still contains the authoring placeholder and does not implement the validated design"
            )
        reserved_result_literals = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            == "result.json"
        ]
        if reserved_result_literals:
            visitor.errors.append(
                "experiment.py cannot read, write, or declare reserved result.json"
            )
        missing_markers = sorted(
            marker for marker in WORKER_RESULT_MARKERS if marker not in source
        )
        if missing_markers:
            visitor.errors.append(
                "experiment.py must return the full worker result contract; "
                f"missing markers: {missing_markers}"
            )
        visitor.errors.extend(_validate_worker_return_literals(tree))
        measurements, results, artifacts, endpoints, estimands = _declared_worker_refs(
            tree
        )
        measurement_contracts = _declared_worker_measurement_contracts(tree)
        result_contracts = _declared_worker_result_contracts(tree)
        consumed_artifacts = _consumed_artifact_refs(tree)
        missing_measurements = sorted((required_measurements or set()) - measurements)
        missing_results = sorted((required_results or set()) - results)
        missing_endpoints = sorted((required_endpoints or set()) - endpoints)
        missing_artifacts = sorted(
            path.replace("\\", "/")
            for path in (expected_artifacts or set())
            if path.replace("\\", "/") not in artifacts
        )
        if missing_measurements:
            visitor.errors.append(
                "experiment.py does not declare all measurements required by the design criteria: "
                f"{missing_measurements}"
            )
        for measurement_name, expected in (
            required_measurement_contracts or {}
        ).items():
            observed = measurement_contracts.get(measurement_name)
            if observed is None:
                continue
            for field in ("unit", "role"):
                observed_choices = observed.get(field)
                expected_value = expected.get(field)
                if (
                    observed_choices is not None
                    and expected_value is not None
                    and observed_choices != {expected_value}
                ):
                    visitor.errors.append(
                        f"experiment.py measurement {measurement_name!r} declares "
                        f"{field}={sorted(observed_choices)!r}, but the validated "
                        f"design requires {expected_value!r}"
                    )
        if missing_results:
            visitor.errors.append(
                "experiment.py does not declare all typed results required by the stage: "
                f"{missing_results}"
            )
        for result_id, expected in (required_result_contracts or {}).items():
            observed = result_contracts.get(result_id)
            if observed is None:
                continue
            for field in ("display_name", "value_kind", "unit", "role"):
                observed_choices = observed.get(field)
                expected_value = expected.get(field)
                if (
                    observed_choices is not None
                    and expected_value is not None
                    and observed_choices != {expected_value}
                ):
                    visitor.errors.append(
                        f"experiment.py typed result {result_id!r} declares "
                        f"{field}={sorted(observed_choices)!r}, but the validated "
                        f"design requires {expected_value!r}"
                    )
        if missing_endpoints:
            visitor.errors.append(
                "experiment.py does not declare all endpoints required by the design criteria: "
                f"{missing_endpoints}"
            )
        if missing_artifacts:
            visitor.errors.append(
                "experiment.py does not declare all expected artifacts: "
                f"{missing_artifacts}"
            )
        missing_consumed_artifacts = sorted(
            (required_consumed_artifacts or set()) - consumed_artifacts
        )
        if missing_consumed_artifacts:
            visitor.errors.append(
                "experiment.py must read every artifact declared as a stage input "
                "through context['artifact_path_by_id']; missing artifact ids: "
                f"{missing_consumed_artifacts}"
            )
        if primary_estimand is not None and primary_estimand not in estimands:
            visitor.errors.append(
                "experiment.py scientific_payload.primary_estimand must exactly match "
                f"the validated design literal: {primary_estimand!r}"
            )
    if visitor.errors:
        raise CodePolicyError("; ".join(visitor.errors[:20]))
    return visitor.imports


def validate_code_files(
    files: object,
    declared_dependencies: list[str] | None = None,
    *,
    required_measurements: set[str] | None = None,
    required_measurement_contracts: dict[str, dict[str, str]] | None = None,
    required_results: set[str] | None = None,
    required_result_contracts: dict[str, dict[str, str]] | None = None,
    required_endpoints: set[str] | None = None,
    expected_artifacts: set[str] | None = None,
    required_consumed_artifacts: set[str] | None = None,
    primary_estimand: str | None = None,
) -> list[dict[str, str]]:
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise CodePolicyError("files must contain 1 to 20 generated files")
    normalized: list[dict[str, str]] = []
    paths: set[str] = set()
    imported_dependencies: set[str] = set()
    total = 0
    for index, raw in enumerate(files):
        if not isinstance(raw, dict) or set(raw) != {"path", "content"}:
            raise CodePolicyError(f"files[{index}] must contain only path and content")
        path = raw["path"]
        content = raw["content"]
        if not isinstance(path, str) or SAFE_CODE_PATH.fullmatch(path) is None:
            raise CodePolicyError(f"files[{index}].path is not safe")
        if path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise CodePolicyError(f"files[{index}].path must be a relative POSIX path")
        if path.rsplit(".", 1)[-1].lower() not in {
            suffix[1:] for suffix in ALLOWED_FILE_SUFFIXES
        }:
            raise CodePolicyError(f"files[{index}].path has an unsupported suffix")
        if path in paths:
            raise CodePolicyError(f"duplicate generated file: {path}")
        if not isinstance(content, str):
            raise CodePolicyError(f"files[{index}].content must be text")
        total += len(content.encode("utf-8"))
        if total > 1024 * 1024:
            raise CodePolicyError("generated files exceed 1 MiB")
        if path.endswith(".py"):
            imported_dependencies.update(
                scan_python(
                    content,
                    path,
                    required_measurements=(
                        required_measurements if path == "experiment.py" else None
                    ),
                    required_measurement_contracts=(
                        required_measurement_contracts
                        if path == "experiment.py"
                        else None
                    ),
                    required_results=(
                        required_results if path == "experiment.py" else None
                    ),
                    required_result_contracts=(
                        required_result_contracts if path == "experiment.py" else None
                    ),
                    required_endpoints=(
                        required_endpoints if path == "experiment.py" else None
                    ),
                    expected_artifacts=(
                        expected_artifacts if path == "experiment.py" else None
                    ),
                    required_consumed_artifacts=(
                        required_consumed_artifacts if path == "experiment.py" else None
                    ),
                    primary_estimand=(
                        primary_estimand if path == "experiment.py" else None
                    ),
                )
                & THIRD_PARTY_IMPORTS
            )
        paths.add(path)
        normalized.append({"path": path, "content": content})
    if "experiment.py" not in paths:
        raise CodePolicyError("generated files must include experiment.py")
    if declared_dependencies is not None:
        undeclared = sorted(imported_dependencies - set(declared_dependencies))
        if undeclared:
            raise CodePolicyError(
                f"generated code imports undeclared dependencies: {undeclared}"
            )
    return normalized


def verify_dependencies(dependencies: list[str]) -> None:
    allowed = THIRD_PARTY_IMPORTS | SAFE_STANDARD_LIBRARY_IMPORTS
    unknown = sorted(set(dependencies) - allowed)
    if unknown:
        raise CodePolicyError(f"unavailable or unreviewed dependencies: {unknown}")
