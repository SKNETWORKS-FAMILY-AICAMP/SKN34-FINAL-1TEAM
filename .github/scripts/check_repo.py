"""Check basic repository hygiene before an application stack is selected."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".css", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".md", ".py", ".sh", ".sql", ".toml", ".ts", ".tsx",
    ".txt", ".xml", ".yaml", ".yml",
}
TEXT_NAMES = {".editorconfig", ".gitattributes", ".gitignore", "Dockerfile"}
ENV_EXAMPLES = {".env.example", ".env.sample", ".env.template"}


def tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(path.decode("utf-8")) for path in output.split(b"\0") if path]


def check_file(path: Path) -> list[str]:
    problems = []
    if path.name == ".env" or (
        path.name.startswith(".env.") and path.name not in ENV_EXAMPLES
    ):
        problems.append(f"{path}: local environment file is tracked")

    if path.is_symlink() or not path.is_file():
        return problems

    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
        return problems

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [*problems, f"{path}: expected UTF-8 text"]

    if path.suffix.lower() == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            problems.append(f"{path}:{exc.lineno}: invalid JSON: {exc.msg}")

    for line_number, line in enumerate(content.splitlines(), start=1):
        if line.startswith(("<<<<<<< ", ">>>>>>> ")):
            problems.append(f"{path}:{line_number}: unresolved merge conflict")
        if path.suffix.lower() != ".md" and line.endswith((" ", "\t")):
            problems.append(f"{path}:{line_number}: trailing whitespace")

    return problems


def main() -> int:
    problems = []
    for path in tracked_files():
        problems.extend(check_file(path))

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"Repository checks failed ({len(problems)} problem(s)).", file=sys.stderr)
        return 1

    print("Repository checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
