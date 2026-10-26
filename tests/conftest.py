import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workflow_engine.engine import WorkflowEngine  # noqa: E402
from workflow_engine.store import RunStore  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture()
def engine(tmp_path):
    return WorkflowEngine(RunStore(tmp_path / "runs.db"))
