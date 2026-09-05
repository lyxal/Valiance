"""Lazy public API for Valiance bytecode compilation and execution."""

from __future__ import annotations

from importlib import import_module

_EXPORT_MODULES = {
    "FunctionCode": "valiance.runtime.bytecode",
    "Instruction": "valiance.runtime.bytecode",
    "OpCode": "valiance.runtime.bytecode",
    "Program": "valiance.runtime.bytecode",
    "CompileError": "valiance.runtime.compiler",
    "compile_program": "valiance.runtime.compiler",
    "DEFAULT_OPTIMIZATION_PIPELINE": "valiance.runtime.optimizer",
    "BytecodePeepholeOptimizationPass": "valiance.runtime.optimizer",
    "ConstantFoldingOptimizationPass": "valiance.runtime.optimizer",
    "ControlFlowOptimizationPass": "valiance.runtime.optimizer",
    "ExplicitArgumentOptimizationPass": "valiance.runtime.optimizer",
    "FunctionOptimizationPass": "valiance.runtime.optimizer",
    "OptimizationError": "valiance.runtime.optimizer",
    "OptimizationPass": "valiance.runtime.optimizer",
    "OptimizationPipeline": "valiance.runtime.optimizer",
    "SmallFunctionInliningPass": "valiance.runtime.optimizer",
    "StackShuffleOptimizationPass": "valiance.runtime.optimizer",
    "optimize_program": "valiance.runtime.optimizer",
    "BytecodeFormatError": "valiance.runtime.serialization",
    "dumps": "valiance.runtime.serialization",
    "loads": "valiance.runtime.serialization",
    "CompiledModule": "valiance.runtime.compiled_module",
    "build_module": "valiance.runtime.compiled_module",
    "dumps_module": "valiance.runtime.compiled_module",
    "implementation_hash": "valiance.runtime.compiled_module",
    "interface_hash": "valiance.runtime.compiled_module",
    "loads_module": "valiance.runtime.compiled_module",
    "AssertionFailure": "valiance.runtime.vm",
    "DoublePanicAbort": "valiance.runtime.vm",
    "RuntimeError": "valiance.runtime.vm",
    "VirtualMachine": "valiance.runtime.vm",
    "run": "valiance.runtime.vm",
    "LazyList": "valiance.runtime.runtime_values",
    "Scheduler": "valiance.runtime.concurrency",
    "TaskScope": "valiance.runtime.concurrency",
    "TaskHandle": "valiance.runtime.concurrency",
    "TaskState": "valiance.runtime.concurrency",
    "TaskYield": "valiance.runtime.concurrency",
    "TaskBlocked": "valiance.runtime.concurrency",
    "Channel": "valiance.runtime.concurrency",
    "ChannelSender": "valiance.runtime.concurrency",
    "ChannelReceiver": "valiance.runtime.concurrency",
    "Receive": "valiance.runtime.concurrency",
    "CancelledFault": "valiance.runtime.concurrency",
    "ClosedFault": "valiance.runtime.concurrency",
    "DeadlockFault": "valiance.runtime.concurrency",
    "render_concurrency_fault": "valiance.runtime.concurrency",
    "ExternalCallMode": "valiance.runtime.concurrency",
    "ExternalCallPolicy": "valiance.runtime.concurrency",
    "SuspensionRegistration": "valiance.runtime.concurrency",
    "TransferClass": "valiance.runtime.transfer",
    "declare_transfer_class": "valiance.runtime.transfer",
    "TransferViolation": "valiance.runtime.transfer",
    "IsolatedResource": "valiance.runtime.transfer",
    "validate_task_transfer": "valiance.runtime.transfer",
    "require_task_transfer": "valiance.runtime.transfer",
}


def __getattr__(name: str):
    """Import one public runtime symbol only when requested."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)


__all__ = sorted(_EXPORT_MODULES)
