"""MemoryCheck: lifecycle conformance tests for persistent AI memory systems."""
from memorycheck._version import __version__
from memorycheck.adapters.base import MemoryAdapter
from memorycheck.adapters.conformance import check_adapter, validate_adapter
from memorycheck.canaries import Canary, canary
from memorycheck.config import Settings, configure
from memorycheck.contracts import Contract, Step, builtin_names, load_builtin, load_contract, parse_contract
from memorycheck.deadlines import close_adapter
from memorycheck.errors import (
    AdapterConfigurationError, AdapterError, CleanupError, ContractError, ContractExecutionError,
    ContractValidationError, InvariantViolation, MemoryCheckError, OperationTimeoutError,
    OracleError, TraceSerializationError, UnsupportedCapability, UnsupportedCapabilityError,
)
from memorycheck.runner import ContractRunner, RunResult
from memorycheck.session import MemoryCheck
from memorycheck.types import MemoryRecord, MemoryRef, QueryObservation, Scope, StorageSnapshot

__all__ = [
    "__version__", "MemoryCheck", "MemoryAdapter", "Settings", "configure", "canary", "Canary",
    "MemoryRecord", "MemoryRef", "QueryObservation", "Scope", "StorageSnapshot", "Contract", "Step",
    "ContractRunner", "RunResult", "builtin_names", "load_builtin", "load_contract", "parse_contract",
    "check_adapter", "validate_adapter", "MemoryCheckError", "AdapterError", "AdapterConfigurationError",
    "ContractError", "ContractValidationError", "ContractExecutionError", "InvariantViolation",
    "UnsupportedCapability", "UnsupportedCapabilityError", "OperationTimeoutError", "OracleError",
    "TraceSerializationError", "CleanupError", "close_adapter",
]
