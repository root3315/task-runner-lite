# task-runner-lite

Lightweight task runner for fast parallel job execution with zero configuration.

## Features

- **Zero config**: Drop scripts in `tasks/` or define a `Tasksfile.py` — no setup needed
- **Parallel execution**: Independent tasks run concurrently using a thread pool
- **Dependency resolution**: Automatic topological sort with cycle detection
- **Result caching**: Skip unchanged tasks based on input/output hashing
- **Staged execution**: Tasks grouped into levels for maximum parallelism
- **Python/Bash/Node scripts**: Run any executable script out of the box

## Installation

No installation required. Just clone and run with Python 3.8+:

```bash
git clone https://github.com/example/task-runner-lite.git
cd task-runner-lite
```

## Quick Start

### Method 1: Scripts Directory

Create a `tasks/` directory and add executable scripts:

```bash
mkdir -p tasks
cat > tasks/build.sh << 'EOF'
#!/bin/bash
echo "Building project..."
sleep 1
echo "Build complete"
EOF
chmod +x tasks/build.sh

cat > tasks/test.py << 'EOF'
#!/usr/bin/env python3
import unittest

class TestExample(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)

if __name__ == "__main__":
    unittest.main()
EOF
chmod +x tasks/test.py
```

### Method 2: Tasksfile.py

Create a `Tasksfile.py` with decorated functions:

```python
from task_runner import task

@task(description="Build the project", outputs=["dist/"])
def build():
    import subprocess
    subprocess.run(["echo", "Building..."])

@task(description="Run tests", depends=["build"])
def test():
    import unittest
    # run tests

@task(description="Deploy", depends=["test"], retries=2)
def deploy():
    print("Deploying...")
```

## Usage

```bash
# Run all tasks in parallel
python task_runner.py

# Run specific tasks
python task_runner.py build test

# List available tasks
python task_runner.py --list

# Use 8 parallel workers
python task_runner.py --workers 8

# Disable caching
python task_runner.py --no-cache

# Continue even if some tasks fail
python task_runner.py --keep-going

# Clear the task cache
python task_runner.py --clear-cache

# Show version
python task_runner.py --version
```

## Command Line Reference

| Flag             | Description                              |
|------------------|------------------------------------------|
| `tasks...`       | Specific tasks to run (default: all)     |
| `-w, --workers`  | Number of parallel workers (default: CPU count) |
| `-l, --list`     | List available tasks                     |
| `--no-cache`     | Disable result caching                   |
| `--clear-cache`  | Clear all cached results and exit        |
| `--keep-going`   | Continue after task failures             |
| `-v, --verbose`  | Show detailed output (discovery, commands, full stdout/stderr, cache status) |
| `--version`      | Show version and exit                    |

## Task Definition API

### `@task` Decorator

```python
@task(
    name="my-task",          # Task name (default: function name)
    description="What it does",
    depends=["other-task"],  # Dependencies
    inputs=["src/main.py"],  # Input files for cache invalidation
    outputs=["dist/"],       # Output files to verify cache validity
    retries=3,               # Number of retries on failure
)
def my_task():
    pass
```

## Caching

Task results are cached in `.task-cache/`. A cached result is reused when:

- The task command and dependencies haven't changed
- All declared output files still exist
- Input files (if specified) haven't been modified

Cache is automatically invalidated when:

- The task command string changes
- Any input file content changes
- Any output file is missing

## How It Works

1. **Discovery**: Scans `Tasksfile.py` and `tasks/` directory for tasks
2. **Resolution**: Builds a dependency graph and computes execution levels
3. **Execution**: Runs each level's tasks in parallel using a thread pool
4. **Reporting**: Prints per-task status and a final summary

## Exit Codes

| Code | Meaning                    |
|------|---------------------------|
| 0    | All tasks passed           |
| 1    | One or more tasks failed   |

## Requirements

- Python 3.8+
- No external dependencies (stdlib only)

## License

MIT
