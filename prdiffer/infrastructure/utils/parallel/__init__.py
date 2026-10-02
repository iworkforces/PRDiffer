"""Parallel execution utilities using anyio task groups.

This package provides native async parallel executor that replaces
ThreadPoolExecutor with anyio's structured concurrency primitives.

Modules:
- executor: AsyncParallelExecutor class for parallel task execution
- results: BatchResult and ErrorStrategy for result handling
"""
