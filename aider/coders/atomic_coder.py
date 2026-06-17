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
    main_system = WholeFilePrompts.main_system + """
You are using the atomic whole-file edit format. Your replacement file content is
validated before it is written. If validation fails, you will receive the
validation output and must return a corrected complete file.
"""

    system_reminder = WholeFilePrompts.system_reminder + """
Atomic validation rejects syntax-invalid Python, JavaScript, Go, and Rust before
writing. Return only complete, syntactically valid file listings.
"""


class AtomicWholeFileCoder(WholeFileCoder):
    """Whole-file coder with pre-write syntax validation and atomic replacement."""

    edit_format = "atomic"
    gpt_prompts = AtomicWholeFilePrompts()

    def apply_edits(self, edits):
        for path, _fname_source, new_lines in edits:
            full_path = self.abs_root_path(path)
            new_text = "".join(new_lines)
            validation = validate_atomic_candidate(full_path, new_text)
            if not validation.ok:
                raise ValueError(format_validation_error(path, validation))
            atomic_write_text(full_path, new_text)
