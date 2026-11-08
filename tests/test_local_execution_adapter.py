"""Run the standalone adapter suite from the repository root."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class LocalExecutionAdapterTests(unittest.TestCase):
    def test_package_suite_passes_offline(self):
        root = Path(__file__).resolve().parents[1] / "adapters" / "local_execution"
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
