"""Pure AST compatibility proofs; fixtures are not scientific worker results."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from automatic_experiment.policy import (
    ALLOWED_IMPORTS,
    CodePolicyError,
    scan_python,
)


@pytest.mark.parametrize(
    ("constructor", "literal"),
    [("StringIO", "'a,b\\n1,2'"), ("BytesIO", "b'a,b\\n1,2'")],
)
def test_named_io_constructor_is_a_memory_buffer_for_imported_reader(
    constructor, literal
):
    code = (
        f"from io import {constructor} as Buffer\n"
        "import pandas as pd\n"
        "def parse(text):\n"
        f"    return pd.read_csv(Buffer({literal}))\n"
    )
    assert scan_python(code, "parser.py") == {"io", "pandas"}
    assert "io" not in ALLOWED_IMPORTS


@pytest.mark.parametrize(
    "code",
    [
        "import io\n",
        "import sys\n",
        "from io import FileIO\n",
        "from io import StringIO, open\n",
        "from io import *\n",
        "import pandas as pd\npd.read_csv('arbitrary.csv')\n",
        "from io import StringIO as B\nimport pandas as pd\ndef f(B):\n return pd.read_csv(B('arbitrary.csv'))\n",
        "from io import StringIO as B\nimport pandas as pd\nB = open\npd.read_csv(B('arbitrary.csv'))\n",
        "from io import StringIO as B\nimport pandas as pd\npd.read_csv((lambda B: B('arbitrary.csv'))(open))\n",
    ],
)
def test_entire_io_sys_and_unproved_reader_paths_still_fail(code):
    with pytest.raises(CodePolicyError, match=r"not allowed|must derive"):
        scan_python(code, "parser.py")


@pytest.mark.parametrize(
    ("module", "alias"), [("numpy", "np"), ("scipy", "s"), ("pandas", "pd")]
)
def test_only_imported_scientific_module_version_metadata_is_accepted(module, alias):
    code = f"import {module} as {alias}\nversion = {alias}.__version__\n"
    assert scan_python(code, "versions.py") == {module}


@pytest.mark.parametrize(
    "code",
    [
        "def f(np):\n return np.__version__\n",
        "import numpy as np\nvalue = np.__dict__\n",
        "import numpy as np\ndef f(np):\n return np.__version__\n",
        "import numpy as np\nnp = object()\nv = np.__version__\n",
    ],
)
def test_unknown_shadowed_module_and_other_dunders_still_fail(code):
    with pytest.raises(CodePolicyError, match="dunder"):
        scan_python(code, "versions.py")


def test_safe_single_binding_constant_join_and_existing_helper_path_parameter():
    code = (
        "OUTPUT = 'tables/fixture.json'\n"
        "def read_one(path):\n return path.read_text()\n"
        "def run_experiment(context):\n"
        " p = context['input_path_by_id']['input_01']\n"
        " q = p\n"
        " text = read_one(q)\n"
        " out = context['output_dir'] / OUTPUT\n"
        " out.write_text(text)\n"
    )
    assert scan_python(code, "paths.py") == set()


@pytest.mark.parametrize(
    "declaration",
    [
        "OUTPUT = '/absolute.json'",
        "OUTPUT = '../escape.json'",
        "OUTPUT = 'C:/escape.json'",
        "OUTPUT = 'safe.json'\nOUTPUT = '../escape.json'",
        "OUTPUT = make_name()",
    ],
)
def test_unproved_or_unsafe_constant_join_still_fails(declaration):
    code = (
        declaration + "\ndef run_experiment(context):\n"
        " out = context['output_dir'] / OUTPUT\n"
        " out.write_text('synthetic fixture')\n"
    )
    with pytest.raises(CodePolicyError, match="must derive"):
        scan_python(code, "paths.py")


def test_real_worker_missing_warnings_and_endpoint_fields_are_not_relaxed():
    code = """
from io import StringIO
import pandas as pd
def run_experiment(context):
    parsed = pd.read_csv(StringIO('synthetic\n'))
    return {
        'schema_version': 'automatic-experiment-worker-result-v1',
        'execution_completed': True,
        'measurements': [],
        'result_items': [],
        'artifacts': [],
        'endpoint_results': [{'endpoint_id': 'fixture', 'category': 'unknown'}],
        'scientific_payload': {}
    }
""".replace("StringIO('synthetic\n')", "StringIO('synthetic')")
    with pytest.raises(CodePolicyError, match="missing=\\['warnings'\\]") as error:
        scan_python(code, "experiment.py")
    assert "missing=['id', 'status', 'summary']" in str(error.value)


def test_unproved_path_return_helper_remains_a_contract_error():
    code = (
        "def input_path(context, key):\n"
        " return context['input_path_by_id'][key]\n"
        "def run_experiment(context):\n"
        " path = input_path(context, 'input_01')\n"
        " return path.read_text()\n"
    )
    with pytest.raises(CodePolicyError, match="must derive"):
        scan_python(code, "paths.py")
