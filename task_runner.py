#!/usr/bin/env python3
"""
task-runner-lite: Lightweight task runner for fast parallel job execution with zero config.

Discovers and runs tasks defined in a tasks/ directory or Tasksfile.py.
Tasks run in parallel by default with dependency resolution.
"""

import argparse
import asyncio
import importlib.util
import hashlib
import json
import os
import platform
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# --- Configuration ---

TASKS_DIR = Path("tasks")
TASKSFILE = Path("Tasksfile.py")
CACHE_DIR = Path(".task-cache")
DEFAULT_WORKERS = os.cpu_count() or 4


# --- Data Structures ---

@dataclass
class Task:
    """Represents a runnable task with metadata."""
    name: str
    command: str
    description: str = ""
    depends: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    retries: int = 0


@dataclass
class TaskResult:
    """Result of executing a task."""
    name: str
    success: bool
    exit_code: int
    duration: float
    stdout: str = ""
    stderr: str = ""
    cached: bool = False


# --- Task Discovery ---

def discover_tasks_from_dir(directory: Path) -> List[Task]:
    """Discover tasks from executable scripts in a directory."""
    tasks = []
    if not directory.exists():
        return tasks

    for entry in sorted(directory.iterdir()):
        if entry.is_file() and os.access(entry, os.X_OK):
            task = Task(
                name=entry.stem,
                command=str(entry),
                description=f"Run {entry.name}",
                cwd=str(entry.parent),
            )
            tasks.append(task)
        elif entry.is_file() and entry.suffix in (".py", ".sh", ".js"):
            cmd = _build_command(entry)
            if cmd:
                task = Task(
                    name=entry.stem,
                    command=cmd,
                    description=f"Run {entry.name}",
                    cwd=str(entry.parent),
                )
                tasks.append(task)
    return tasks


def _build_command(entry: Path) -> Optional[str]:
    """Build a command to execute a script file."""
    if entry.suffix == ".py":
        return f"{sys.executable} {entry}"
    elif entry.suffix == ".sh":
        return f"bash {entry}"
    elif entry.suffix == ".js":
        return f"node {entry}"
    return None


def discover_tasks_from_module(filepath: Path) -> List[Task]:
    """Discover tasks from a Python module (Tasksfile.py)."""
    if not filepath.exists():
        return []

    spec = importlib.util.spec_from_file_location("tasksfile", filepath)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tasks = []
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        attr = getattr(module, attr_name)
        if isinstance(attr, Task):
            if attr.name == "":
                attr.name = attr_name
            tasks.append(attr)
        elif callable(attr) and hasattr(attr, "_task_meta"):
            meta = attr._task_meta
            task = Task(
                name=meta.get("name", attr_name),
                command=f"python -c \"from {filepath.stem} import {attr_name}; {attr_name}()\"",
                description=meta.get("description", ""),
                depends=meta.get("depends", []),
                inputs=meta.get("inputs", []),
                outputs=meta.get("outputs", []),
                retries=meta.get("retries", 0),
            )
            tasks.append(task)
    return tasks


def task(name: str = "", description: str = "", depends: Optional[List[str]] = None,
         inputs: Optional[List[str]] = None, outputs: Optional[List[str]] = None,
         retries: int = 0):
    """Decorator to mark a function as a task."""
    def decorator(func):
        func._task_meta = {
            "name": name or func.__name__,
            "description": description,
            "depends": depends or [],
            "inputs": inputs or [],
            "outputs": outputs or [],
            "retries": retries,
        }
        return func
    return decorator


# --- Dependency Resolution ---

def resolve_execution_order(tasks: List[Task], targets: Optional[List[str]] = None) -> List[List[str]]:
    """
    Resolve task execution order using topological sort.
    Returns list of levels, where tasks in the same level can run in parallel.
    """
    task_map = {t.name: t for t in tasks}

    if targets:
        required = set()
        for target in targets:
            _collect_deps(target, task_map, required)
        tasks = [t for t in tasks if t.name in required]
        task_map = {t.name: t for t in tasks}

    in_degree: Dict[str, int] = {t.name: 0 for t in tasks}
    dependents: Dict[str, List[str]] = {t.name: [] for t in tasks}

    for t in tasks:
        for dep in t.depends:
            if dep not in task_map:
                print(f"Warning: task '{t.name}' depends on unknown task '{dep}'", file=sys.stderr)
                continue
            in_degree[t.name] += 1
            dependents[dep].append(t.name)

    levels: List[List[str]] = []
    remaining = dict(in_degree)

    while remaining:
        level = [name for name, deg in remaining.items() if deg == 0]
        if not level:
            cycle = _find_cycle(remaining, dependents)
            print(f"Error: circular dependency detected: {' -> '.join(cycle)}", file=sys.stderr)
            sys.exit(1)

        levels.append(sorted(level))
        for name in level:
            del remaining[name]
            for dep_name in dependents.get(name, []):
                if dep_name in remaining:
                    remaining[dep_name] -= 1

    return levels


def _collect_deps(task_name: str, task_map: Dict[str, Task], collected: Set[str]):
    """Recursively collect all dependencies for a task."""
    if task_name in collected:
        return
    if task_name not in task_map:
        return
    collected.add(task_name)
    for dep in task_map[task_name].depends:
        _collect_deps(dep, task_map, collected)


def _find_cycle(remaining: Dict[str, int], dependents: Dict[str, List[str]]) -> List[str]:
    """Find a cycle in the dependency graph for error reporting."""
    visited: Set[str] = set()
    path: List[str] = []

    def dfs(node: str) -> Optional[List[str]]:
        if node in visited:
            idx = path.index(node) if node in path else -1
            return path[idx:] if idx >= 0 else [node]
        visited.add(node)
        path.append(node)
        for dep in dependents.get(node, []):
            if dep in remaining:
                result = dfs(dep)
                if result:
                    return result
        path.pop()
        return None

    for node in remaining:
        result = dfs(node)
        if result:
            return result
    return list(remaining.keys())[:3]


# --- Task Execution ---

def run_single_task(task: Task, cache_enabled: bool = False) -> TaskResult:
    """Execute a single task and return the result."""
    if cache_enabled and _is_cache_valid(task):
        return TaskResult(
            name=task.name, success=True, exit_code=0,
            duration=0.0, stdout="(cached)", stderr="", cached=True,
        )

    start_time = time.time()
    env = os.environ.copy()
    env.update(task.env)

    try:
        import subprocess
        result = subprocess.run(
            task.command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=task.cwd,
            env=env,
            timeout=300,
        )
        duration = time.time() - start_time

        task_result = TaskResult(
            name=task.name,
            success=result.returncode == 0,
            exit_code=result.returncode,
            duration=duration,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )

        if task_result.success and cache_enabled:
            _save_cache(task, task_result)

        return task_result

    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return TaskResult(
            name=task.name, success=False, exit_code=124,
            duration=duration, stdout="", stderr="Task timed out (300s)",
        )
    except FileNotFoundError as e:
        duration = time.time() - start_time
        return TaskResult(
            name=task.name, success=False, exit_code=127,
            duration=duration, stdout="", stderr=f"Command not found: {e}",
        )
    except Exception as e:
        duration = time.time() - start_time
        return TaskResult(
            name=task.name, success=False, exit_code=1,
            duration=duration, stdout="", stderr=str(e),
        )


# --- Caching ---

def _compute_task_hash(task: Task) -> str:
    """Compute a hash for cache invalidation."""
    h = hashlib.sha256()
    h.update(task.command.encode())
    h.update(str(task.depends).encode())

    for input_path in task.inputs:
        p = Path(input_path)
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(input_path.encode())

    return h.hexdigest()


def _is_cache_valid(task: Task) -> bool:
    """Check if a cached result is still valid."""
    cache_file = CACHE_DIR / f"{task.name}.json"
    if not cache_file.exists():
        return False

    try:
        data = json.loads(cache_file.read_text())
        current_hash = _compute_task_hash(task)
        if data.get("hash") != current_hash:
            return False

        for output in task.outputs:
            if not Path(output).exists():
                return False

        return True
    except (json.JSONDecodeError, KeyError):
        return False


def _save_cache(task: Task, result: TaskResult):
    """Save task result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{task.name}.json"
    data = {
        "hash": _compute_task_hash(task),
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timestamp": time.time(),
    }
    cache_file.write_text(json.dumps(data, indent=2))


def clear_cache():
    """Remove all cached results."""
    if CACHE_DIR.exists():
        import shutil
        shutil.rmtree(CACHE_DIR)


# --- Parallel Execution ---

def execute_tasks_parallel(
    tasks: List[Task],
    levels: List[List[str]],
    workers: int = DEFAULT_WORKERS,
    cache: bool = False,
    stop_on_failure: bool = True,
) -> List[TaskResult]:
    """Execute tasks in parallel respecting dependency levels."""
    task_map = {t.name: t for t in tasks}
    all_results: List[TaskResult] = []
    failed: Set[str] = set()

    for level_idx, level in enumerate(levels):
        if stop_on_failure and failed:
            break

        level_tasks = [task_map[name] for name in level if name not in failed]
        if not level_tasks:
            continue

        print(f"\n{'='*60}")
        print(f"Stage {level_idx + 1}/{len(levels)}: {', '.join(level)}")
        print(f"{'='*60}")

        level_results = _run_level(level_tasks, workers, cache)

        for result in level_results:
            all_results.append(result)
            if not result.success:
                failed.add(result.name)
                if stop_on_failure:
                    break

    return all_results


def _run_level(tasks: List[Task], workers: int, cache: bool) -> List[TaskResult]:
    """Run a single level of tasks in parallel."""
    results = []

    with ThreadPoolExecutor(max_workers=min(workers, len(tasks))) as executor:
        futures = {
            executor.submit(run_single_task, task, cache): task
            for task in tasks
        }

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            _print_result(result)

    return results


def _print_result(result: TaskResult):
    """Print a formatted task result."""
    status = "OK" if result.success else "FAIL"
    cache_tag = " [cached]" if result.cached else ""
    print(f"  [{status}] {result.name}{cache_tag} ({result.duration:.2f}s)")

    if result.stdout:
        for line in result.stdout.split("\n")[:5]:
            print(f"    {line}")
    if result.stderr and not result.success:
        for line in result.stderr.split("\n")[:5]:
            print(f"    {line}", file=sys.stderr)


# --- Reporting ---

def print_summary(results: List[TaskResult]):
    """Print execution summary."""
    total = len(results)
    passed = sum(1 for r in results if r.success)
    failed = total - passed
    cached = sum(1 for r in results if r.cached)
    total_duration = sum(r.duration for r in results)

    print(f"\n{'='*60}")
    print("Execution Summary")
    print(f"{'='*60}")
    print(f"  Total:    {total}")
    print(f"  Passed:   {passed}")
    print(f"  Failed:   {failed}")
    print(f"  Cached:   {cached}")
    print(f"  Duration: {total_duration:.2f}s")
    print(f"{'='*60}")


# --- CLI ---

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="task-runner-lite",
        description="Lightweight task runner for fast parallel job execution",
    )
    parser.add_argument(
        "tasks", nargs="*", default=None,
        help="Specific tasks to run (default: all)",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"Number of parallel workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="List available tasks",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable result caching",
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear all cached results and exit",
    )
    parser.add_argument(
        "--keep-going", action="store_true",
        help="Continue executing tasks even after failures",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show verbose output",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Show version and exit",
    )
    return parser


def cmd_list_tasks(tasks: List[Task]):
    """List all available tasks."""
    if not tasks:
        print("No tasks found.")
        print(f"  Create a {TASKSFILE} or add scripts to {TASKS_DIR}/")
        return

    print(f"Available tasks ({len(tasks)}):")
    for task in tasks:
        deps = f" [{', '.join(task.depends)}]" if task.depends else ""
        desc = f" - {task.description}" if task.description else ""
        print(f"  {task.name}{deps}{desc}")


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print("task-runner-lite 1.0.0")
        return 0

    if args.clear_cache:
        clear_cache()
        print("Cache cleared.")
        return 0

    # Discover tasks
    all_tasks: List[Task] = []
    all_tasks.extend(discover_tasks_from_module(TASKSFILE))
    all_tasks.extend(discover_tasks_from_dir(TASKS_DIR))

    if args.list:
        cmd_list_tasks(all_tasks)
        return 0

    if not all_tasks:
        print("No tasks found.", file=sys.stderr)
        print(f"Create a {TASKSFILE} or add scripts to {TASKS_DIR}/", file=sys.stderr)
        return 1

    # Resolve execution order
    levels = resolve_execution_order(all_tasks, args.tasks)
    flat_names = [name for level in levels for name in level]

    if args.tasks:
        missing = [t for t in args.tasks if t not in flat_names]
        if missing:
            print(f"Error: unknown tasks: {', '.join(missing)}", file=sys.stderr)
            return 1

    # Execute
    cache_enabled = not args.no_cache
    results = execute_tasks_parallel(
        all_tasks, levels,
        workers=args.workers,
        cache=cache_enabled,
        stop_on_failure=not args.keep_going,
    )

    # Report
    print_summary(results)

    failed = [r for r in results if not r.success]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
