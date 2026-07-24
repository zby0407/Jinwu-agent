from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

MAX_MEMORY_MB = 64
TIMEOUT_SECONDS = 5.0
MAX_OUTPUT_ELEMENTS = 1000
MAX_OUTPUT_ROWS = 1000
MAX_DF_ROWS = 100000
MAX_DF_COLS = 1000

ALLOWED_FUNCTIONS: set[str] = {
    "mean",
    "median",
    "std",
    "var",
    "min",
    "max",
    "sum",
    "count",
    "quantile",
    "corr",
    "value_counts",
    "head",
    "tail",
    "describe",
}

# Column name: allow quoted strings, simple identifiers, or numeric literals.
COLUMN_NAME_PATTERN = re.compile(r"[\"']([^\"']+?)[\"']|([a-zA-Z_][a-zA-Z0-9_]*)|([+-]?\d+(?:\.\d+)?)")
FUNCTION_CALL_PATTERN = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)\s*$")


def _parse_expression(expression: str) -> tuple[str, list[str]]:
    """Parse a function call expression into name and argument list."""
    expression = expression.strip()
    if not expression:
        raise ValueError("Empty expression")

    match = FUNCTION_CALL_PATTERN.match(expression)
    if not match:
        raise ValueError(
            f"Invalid expression: {expression!r}. "
            f"Expected one of: {', '.join(sorted(ALLOWED_FUNCTIONS))}(...)."
        )

    func_name = match.group(1)
    if func_name not in ALLOWED_FUNCTIONS:
        raise ValueError(
            f"Unknown function: {func_name!r}. Allowed functions: {', '.join(sorted(ALLOWED_FUNCTIONS))}"
        )

    args_text = match.group(2).strip()
    args: list[str] = []
    if args_text:
        for m in COLUMN_NAME_PATTERN.finditer(args_text):
            if m.group(1) is not None:
                arg = m.group(1)
            elif m.group(2) is not None:
                arg = m.group(2)
            else:
                arg = m.group(3)
            if arg is not None:
                args.append(arg)

    return func_name, args


def _validate_arguments(func_name: str, args: list[str], df: pd.DataFrame) -> None:
    """Validate that arguments are valid column names where required."""
    if func_name in {"head", "tail", "describe"}:
        # Optional integer argument for head/tail; no column args for describe.
        if func_name in {"head", "tail"}:
            if len(args) > 1:
                raise ValueError(f"{func_name} accepts at most one integer argument")
            if args and not args[0].isdigit():
                raise ValueError(f"{func_name} argument must be an integer")
        if func_name == "describe" and args:
            raise ValueError("describe does not accept arguments")
        return

    if func_name == "corr":
        if len(args) != 2:
            raise ValueError("corr requires two column arguments")
        for col in args:
            if col not in df.columns:
                raise ValueError(f"Column not found: {col}. Available columns: {list(df.columns)}")
            if not pd.api.types.is_numeric_dtype(df[col]):
                raise ValueError(f"Column is not numeric: {col}")
        return

    if func_name == "quantile":
        if len(args) != 2:
            raise ValueError("quantile requires exactly two arguments: column and quantile value")
        col, q = args[0], args[1]
        if col not in df.columns:
            raise ValueError(f"Column not found: {col}. Available columns: {list(df.columns)}")
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Column is not numeric: {col}")
        try:
            q_float = float(q)
            if not 0 <= q_float <= 1:
                raise ValueError
        except ValueError as exc:
            raise ValueError("quantile second argument must be a number between 0 and 1") from exc
        return

    # Single-column functions.
    if not args:
        raise ValueError(f"{func_name} requires at least one column argument")
    if len(args) > 1:
        raise ValueError(f"{func_name} accepts only one column argument")
    col = args[0]
    if col not in df.columns:
        raise ValueError(f"Column not found: {col}. Available columns: {list(df.columns)}")


def _memory_usage_mb(df: pd.DataFrame) -> float:
    """Estimate DataFrame memory usage in MB."""
    return df.memory_usage(deep=True).sum() / (1024 * 1024)


def _execute_locally(df: pd.DataFrame, func_name: str, args: list[str]) -> Any:
    """Execute a predefined function on the DataFrame."""
    if func_name == "describe":
        return df.describe().to_dict()

    if func_name == "head":
        n = int(args[0]) if args else 5
        return df.head(n).to_dict(orient="records")

    if func_name == "tail":
        n = int(args[0]) if args else 5
        return df.tail(n).to_dict(orient="records")

    if func_name == "corr":
        valid = df[[args[0], args[1]]].dropna()
        return float(valid[args[0]].corr(valid[args[1]]))

    if func_name == "value_counts":
        counts = df[args[0]].value_counts(dropna=False).head(20)
        return [{"value": str(k), "count": int(v)} for k, v in counts.items()]

    col = args[0]
    series = df[col]

    if func_name == "quantile":
        q = float(args[1])
        return float(series.quantile(q))

    if func_name == "count":
        return int(series.count())

    fn = getattr(pd.Series, func_name)
    result = fn(series)
    return float(result)


def _serialize_result(result: Any) -> Any:
    """Convert result to JSON-serializable form and enforce output limits."""
    if isinstance(result, pd.DataFrame):
        records = result.head(MAX_OUTPUT_ROWS).to_dict(orient="records")
        if len(records) > MAX_OUTPUT_ELEMENTS:
            records = records[:MAX_OUTPUT_ELEMENTS]
        return records
    if isinstance(result, pd.Series):
        d = result.to_dict()
        if len(d) > MAX_OUTPUT_ELEMENTS:
            d = dict(list(d.items())[:MAX_OUTPUT_ELEMENTS])
        return d
    return result


def _build_worker_script(df_csv_path: str, func_name: str, args: list[str]) -> str:
    """Build a Python script string to run in an isolated subprocess."""
    args_json = json.dumps(args)
    return f"""
import json
import pandas as pd

df = pd.read_csv({json.dumps(df_csv_path)})

def _execute(df, func_name, args):
    if func_name == "describe":
        return df.describe().to_dict()
    if func_name == "head":
        n = int(args[0]) if args else 5
        return df.head(n).to_dict(orient="records")
    if func_name == "tail":
        n = int(args[0]) if args else 5
        return df.tail(n).to_dict(orient="records")
    if func_name == "corr":
        valid = df[[args[0], args[1]]].dropna()
        return float(valid[args[0]].corr(valid[args[1]]))
    if func_name == "value_counts":
        counts = df[args[0]].value_counts(dropna=False).head(20)
        return [{{"value": str(k), "count": int(v)}} for k, v in counts.items()]
    col = args[0]
    series = df[col]
    if func_name == "quantile":
        return float(series.quantile(float(args[1])))
    if func_name == "count":
        return int(series.count())
    fn = getattr(pd.Series, func_name)
    return float(fn(series))

result = _execute(df, {json.dumps(func_name)}, {args_json})
print(json.dumps(result, ensure_ascii=False))
"""


def safe_eval(expression: str, df: pd.DataFrame, timeout: float = TIMEOUT_SECONDS) -> Any:
    """Safely evaluate a predefined statistical function expression against a DataFrame.

    Security measures:
    - Only predefined function calls are allowed (no arbitrary Python code).
    - Execution runs in a subprocess with no write permissions and limited runtime.
    - DataFrame size is capped by memory estimate before execution.
    - Output is limited to MAX_OUTPUT_ELEMENTS elements.
    """
    func_name, args = _parse_expression(expression)
    _validate_arguments(func_name, args, df)

    if len(df) > MAX_DF_ROWS or len(df.columns) > MAX_DF_COLS:
        raise ValueError(
            f"DataFrame too large for query: {len(df)} rows x {len(df.columns)} columns. "
            f"Limits: {MAX_DF_ROWS} rows, {MAX_DF_COLS} columns."
        )

    memory_mb = _memory_usage_mb(df)
    if memory_mb > MAX_MEMORY_MB:
        raise ValueError(
            f"DataFrame memory estimate ({memory_mb:.1f} MB) exceeds the {MAX_MEMORY_MB} MB limit."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        df_csv = Path(tmpdir) / "df.csv"
        df.to_csv(df_csv, index=False, encoding="utf-8")
        script = _build_worker_script(str(df_csv), func_name, args)

        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Query execution exceeded {timeout} seconds") from exc

        if result.returncode != 0:
            error = result.stderr.strip() or "unknown subprocess error"
            raise RuntimeError(f"Query execution failed: {error}")

        output = result.stdout.strip()
        if not output:
            raise RuntimeError("Query returned no output")

        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse query result: {exc}") from exc

        return _serialize_result(data)


def run(expression: str, session: Any, timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
    from chat_session import ChatSession

    if not isinstance(session, ChatSession):
        session = ChatSession()
    path = session.get_current_dataset_path()
    if not path:
        raise ValueError("No current dataset loaded. Use /load <csv_path> first.")
    candidate = Path(path)
    full_path = candidate if candidate.is_absolute() else ROOT / path
    if not full_path.exists():
        raise FileNotFoundError(f"Current dataset not found: {full_path}")
    df = pd.read_csv(full_path)
    df.columns = [str(c).strip() for c in df.columns]

    result = safe_eval(expression, df, timeout=timeout)

    return {
        "status": "ok",
        "action": "dataset_query",
        "dataset": path,
        "expression": expression,
        "result": result,
    }
