"""Local execution adapter.

Help-verified argv builders and an offline-tested subprocess runner.
This package does not claim a working live multi-model system.
"""

from execution_adapter.bindings import BindingError, recommend
from execution_adapter.package_hash import source_hash
from execution_adapter.runner import dispatch

__all__ = ["BindingError", "dispatch", "recommend", "source_hash"]
__version__ = "0.1.0"
