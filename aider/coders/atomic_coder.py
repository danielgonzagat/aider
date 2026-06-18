import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .wholefile_coder import WholeFileCoder
from .wholefile_prompts import WholeFilePrompts


@dataclass
class AtomicValidationResult:
    ok: bool
    kind: str
    command: list[str] | None = None
    status: int | None = None
    stdout: str = ""
    stderr: str = ""
    skipped: bool = False


def _tail(text, limit=4000):
    text = str(text or "")
    return text if len(text) <= limit else text[-limit:]


def _run_validation(command, cwd=None):
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except FileNotFoundError:
        return AtomicValidationResult(
            ok=True,
            kind=f"{command[0]}-missing-skip",
            command=command,
            skipped=True,
        )
    except subprocess.TimeoutExpired as err:
        return AtomicValidationResult(
            ok=False,
            kind="validation-timeout",
            command=command,
            stdout=_tail(err.stdout),
            stderr=_tail(err.stderr),
        )

    return AtomicValidationResult(
        ok=result.returncode == 0,
        kind=command[0],
        command=command,
        status=result.returncode,
        stdout=_tail(result.stdout),
        stderr=_tail(result.stderr),
    )


def _find_ancestor_containing(start_file, marker):
    directory = Path(start_file).resolve().parent
    while True:
        if (directory / marker).exists():
            return directory
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _validate_temp_file(full_path, new_text, command_builder):
    with tempfile.TemporaryDirectory(prefix="aider-atomic-validate-") as tempdir:
        temp_path = Path(tempdir) / Path(full_path).name
        temp_path.write_text(new_text, encoding="utf-8")
        return _run_validation(command_builder(temp_path, Path(tempdir)))


def _validate_rust(full_path, new_text):
    cargo_root = _find_ancestor_containing(full_path, "Cargo.toml")
    if not cargo_root:
        return _validate_temp_file(
            full_path,
            new_text,
            lambda temp_path, tempdir: [
                "rustc",
                "--crate-type",
                "lib",
                "--emit",
                "metadata",
                str(temp_path),
                "-o",
                str(tempdir / "candidate.rmeta"),
            ],
        )

    with tempfile.TemporaryDirectory(prefix="aider-atomic-cargo-") as tempdir:
        temp_root = Path(tempdir) / cargo_root.name

        def ignore(_directory, names):
            return {name for name in names if name in {"target", ".git"}}

        shutil.copytree(cargo_root, temp_root, ignore=ignore)
        relative_file = Path(full_path).resolve().relative_to(cargo_root)
        target = temp_root / relative_file
        target.write_text(new_text, encoding="utf-8")
        return _run_validation(["cargo", "check", "--quiet"], cwd=temp_root)


def validate_atomic_candidate(full_path, new_text):
    suffix = Path(full_path).suffix.lower()
    if suffix == ".py":
        return _validate_temp_file(
            full_path,
            new_text,
            lambda temp_path, _tempdir: [sys.executable, "-m", "py_compile", str(temp_path)],
        )
    if suffix in {".js", ".mjs", ".cjs"}:
        return _validate_temp_file(
            full_path,
            new_text,
            lambda temp_path, _tempdir: ["node", "--check", str(temp_path)],
        )
    if suffix == ".go":
        return _validate_temp_file(
            full_path,
            new_text,
            lambda temp_path, _tempdir: ["gofmt", str(temp_path)],
        )
    if suffix == ".rs":
        return _validate_rust(full_path, new_text)
    return AtomicValidationResult(ok=True, kind="syntax-validation-not-required")


def format_validation_error(path, validation):
    lines = [
        f"AtomicValidationFailed: candidate for {path} failed syntax validation.",
        f"kind: {validation.kind}",
    ]
    if validation.command:
        lines.append("command: " + " ".join(validation.command))
    if validation.status is not None:
        lines.append(f"status: {validation.status}")
    if validation.stdout:
        lines.extend(["stdout:", validation.stdout])
    if validation.stderr:
        lines.extend(["stderr:", validation.stderr])
    lines.append("Return the complete corrected file content again.")
    return "\n".join(lines)


def atomic_write_text(full_path, text):
    target = Path(full_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.atomic-",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class AtomicWholeFilePrompts(WholeFilePrompts):
    main_system = """Act as an expert software developer.
Take requests for changes to the supplied code.
If the request is ambiguous, ask questions before editing.
{final_reminders}
Once you understand the request:
1. Determine silently whether code changes are needed.
2. If no file changes are needed, say exactly: No changes are needed.
3. If changes are needed, Return only file listings.
Do not explain changes before or after file listings.
Do not output markdown, analysis, plans, bullets, diffs, or commentary outside
file listings.
Atomic validation checks replacement file content before writing. If validation
fails, return corrected complete file listings only.
"""

    example_messages = [
        dict(role="user", content="Change the greeting to be more casual"),
        dict(
            role="assistant",
            content="""sample.py
{fence[0]}
import sys

def greeting(name):
    print(f"Hey {{name}}")

if __name__ == "__main__":
    greeting(sys.argv[1])
{fence[1]}
""",
        ),
    ]

    system_reminder = """Return only file listings.

A file listing has exactly this shape:

path/to/filename.ext
{fence[0]}
<complete updated file content>
{fence[1]}

Rules:
- The line immediately before the opening fence is the filename.
- Include complete file content, not patches, diffs, snippets, summaries, or
  elisions.
- Do not write analysis, explanations, bullets, or commentary before, between,
  or after file listings.
- If no file changes are needed, say exactly: No changes are needed.
- Atomic validation rejects syntax-invalid Python, JavaScript, Go, and Rust
  before writing.
- If validation fails, return corrected complete file listings only.

{final_reminders}
"""


class AtomicWholeFileCoder(WholeFileCoder):
    """Whole-file coder with pre-write syntax validation and atomic replacement."""

    edit_format = "atomic"
    gpt_prompts = AtomicWholeFilePrompts()

    def get_edits(self, mode="update"):
        edits = super().get_edits(mode=mode)
        if mode == "diff":
            return edits

        chat_files = set(self.get_inchat_relative_files())
        if not chat_files:
            return edits

        filtered_edits = []
        ignored_paths = []
        for path, fname_source, new_lines in edits:
            if path in chat_files:
                filtered_edits.append((path, fname_source, new_lines))
            else:
                ignored_paths.append(path)

        if filtered_edits:
            return filtered_edits
        if ignored_paths:
            allowed = ", ".join(sorted(chat_files))
            ignored = ", ".join(sorted(ignored_paths))
            raise ValueError(
                "AtomicIgnoredFileListings: ignored file listings for files not in chat: "
                f"{ignored}. Return complete listings only for: {allowed}."
            )
        return filtered_edits

    def apply_edits(self, edits):
        for path, _fname_source, new_lines in edits:
            full_path = self.abs_root_path(path)
            new_text = "".join(new_lines)
            validation = validate_atomic_candidate(full_path, new_text)
            if not validation.ok:
                raise ValueError(format_validation_error(path, validation))
            atomic_write_text(full_path, new_text)
