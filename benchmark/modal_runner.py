#!/usr/bin/env python
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
import shlex
import subprocess
from typing import Iterable

try:
    import modal
except ModuleNotFoundError:  # pragma: no cover - exercised in plain unit tests
    modal = None


APP_NAME = "aider-polyglot-atomic-benchmark"
BENCHMARK_VOLUME_NAME = "aider-polyglot-benchmarks"
DEEPSEEK_SECRET_NAME = os.environ.get("AIDER_MODAL_DEEPSEEK_SECRET", "atomic-deepseek")
DEFAULT_POLYGLOT_REPO = "https://github.com/Aider-AI/polyglot-benchmark.git"
DEFAULT_LANGUAGES = ("cpp", "go", "java", "javascript", "python", "rust")
REMOTE_AIDER_DIR = Path("/aider")
REMOTE_BENCHMARK_DIR = Path("/benchmarks")
REMOTE_EXERCISES_DIR = "polyglot-benchmark"
SECONDS_PER_DAY = 24 * 60 * 60
OUTPUT_TAIL_CHARS = 8000
MODAL_RESULT_SUMMARY_FILENAME = ".aider.modal-result.json"

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "benchmark" / "Dockerfile"
MODAL_DOCKERFILE_PYTHON_VERSION = "3.11"

MODAL_CONTEXT_SYMLINK_PATHS = (
    "aider/website/_posts/2023-05-25-ctags.md",
    "aider/website/_posts/2023-07-02-benchmarks.md",
    "aider/website/_posts/2023-11-06-benchmarks-1106.md",
    "aider/website/_posts/2023-11-06-benchmarks-speed-1106.md",
    "aider/website/_posts/2023-12-21-unified-diffs.md",
    "aider/website/_posts/2024-01-25-benchmarks-0125.md",
)


def _git_assume_unchanged_command(paths: Iterable[str]) -> str:
    quoted_paths = " ".join(shlex.quote(path) for path in paths)
    return (
        f"git -C {shlex.quote(str(REMOTE_AIDER_DIR))} "
        f"update-index --assume-unchanged {quoted_paths}"
    )


def _build_modal_image(modal_module):
    return modal_module.Image.from_dockerfile(
        DOCKERFILE,
        context_dir=REPO_ROOT,
        add_python=MODAL_DOCKERFILE_PYTHON_VERSION,
        build_args={"AIDER_MODAL_RUNTIME": "1"},
    ).run_commands(
        "python -m pip install --no-cache-dir --upgrade pip uv",
        "uv pip install --system --no-cache-dir -e /aider[dev]",
        "git config --global core.fileMode false",
        "git config --global --add safe.directory /aider",
        _git_assume_unchanged_command(MODAL_CONTEXT_SYMLINK_PATHS),
    )


@dataclass(frozen=True)
class Shard:
    language: str
    run_name: str


@dataclass(frozen=True)
class BenchmarkRequest:
    run_name: str
    model: str
    edit_format: str
    language: str
    threads: int
    tries: int
    exercises_dir: str
    keywords: str | None = None
    num_tests: int = -1
    read_model_settings: str | None = None
    reasoning_effort: str | None = None
    thinking_tokens: int | None = None
    no_aider: bool = False
    no_unit_tests: bool = False
    resume: bool = True
    polyglot_repo: str = DEFAULT_POLYGLOT_REPO
    polyglot_ref: str | None = None


@dataclass(frozen=True)
class BenchmarkResult:
    language: str
    run_name: str
    returncode: int
    result_dir: str | None
    command: str
    output_tail: str


def write_modal_result_summary(result: BenchmarkResult) -> Path | None:
    if result.result_dir is None:
        return None

    summary_path = Path(result.result_dir) / MODAL_RESULT_SUMMARY_FILENAME
    summary_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True))
    return summary_path


def _language_result_dirs(shard_dir: Path) -> list[Path]:
    language_dirs = []
    for child in Path(shard_dir).iterdir():
        practice_dir = child / "exercises" / "practice"
        if not child.is_dir() or not practice_dir.is_dir():
            continue
        if any(practice_dir.glob("*/.aider.results.json")):
            language_dirs.append(child)
    return sorted(language_dirs)


def aggregate_modal_shards(shard_dirs: Iterable[Path | str], output_dir: Path | str) -> Path:
    output_path = Path(output_dir)
    if output_path.exists():
        raise FileExistsError(f"aggregate output already exists: {output_path}")

    output_path.mkdir(parents=True)
    copied_languages: list[str] = []
    try:
        for shard_dir in shard_dirs:
            shard_path = Path(shard_dir)
            if not shard_path.is_dir():
                raise FileNotFoundError(f"modal shard result directory not found: {shard_path}")

            for language_dir in _language_result_dirs(shard_path):
                target = output_path / language_dir.name
                if target.exists():
                    raise FileExistsError(f"duplicate language result tree: {target}")
                shutil.copytree(language_dir, target)
                copied_languages.append(language_dir.name)

        if not copied_languages:
            raise ValueError("no language result trees found to aggregate")
    except Exception:
        shutil.rmtree(output_path, ignore_errors=True)
        raise

    return output_path


def remote_exercises_dir_for_request(exercises_dir: str, language: str) -> str:
    if exercises_dir != REMOTE_EXERCISES_DIR:
        return exercises_dir
    return f"{REMOTE_EXERCISES_DIR}-{language}"


def parse_languages(languages: str | Iterable[str] | None) -> tuple[str, ...]:
    if languages is None:
        return DEFAULT_LANGUAGES
    if isinstance(languages, str):
        items = languages.split(",")
    else:
        items = languages
    parsed = tuple(item.strip().lower() for item in items if item and item.strip())
    return parsed or DEFAULT_LANGUAGES


def build_shards(base_run_name: str, languages: str | Iterable[str] | None = None) -> list[Shard]:
    return [
        Shard(language=language, run_name=f"{base_run_name}-{language}")
        for language in parse_languages(languages)
    ]


def build_benchmark_command(
    *,
    run_name: str,
    model: str,
    edit_format: str,
    language: str,
    threads: int,
    tries: int,
    exercises_dir: str,
    keywords: str | None = None,
    num_tests: int = -1,
    read_model_settings: str | None = None,
    reasoning_effort: str | None = None,
    thinking_tokens: int | None = None,
    no_aider: bool = False,
    no_unit_tests: bool = False,
    resume: bool = True,
) -> list[str]:
    cmd = [
        "./benchmark/benchmark.py",
        run_name,
        "--model",
        model,
        "--edit-format",
        edit_format,
        "--languages",
        language,
        "--threads",
        str(threads),
        "--tries",
        str(tries),
        "--exercises-dir",
        exercises_dir,
    ]
    cmd.append("--cont" if resume else "--new")

    if no_aider:
        cmd.append("--no-aider")
    if no_unit_tests:
        cmd.append("--no-unit-tests")
    if keywords:
        cmd.extend(["--keywords", keywords])
    if num_tests and num_tests > 0:
        cmd.extend(["--num-tests", str(num_tests)])
    if read_model_settings:
        cmd.extend(["--read-model-settings", read_model_settings])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if thinking_tokens:
        cmd.extend(["--thinking-tokens", str(thinking_tokens)])

    return cmd


def shell_join(cmd: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def detect_local_polyglot_ref() -> str | None:
    local_checkout = REPO_ROOT / "tmp.benchmarks" / "polyglot-benchmark"
    if not local_checkout.exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(local_checkout), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    return completed.stdout.strip() or None


def _checkout_matches_ref(target: Path, ref: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return False
    return completed.stdout.strip() == ref


def _ensure_polyglot_checkout(repo: str, ref: str | None, exercises_dir: str) -> None:
    target = REMOTE_BENCHMARK_DIR / exercises_dir
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        subprocess.run(["git", "clone", repo, str(target)], check=True)

    if ref:
        if _checkout_matches_ref(target, ref):
            return
        subprocess.run(["git", "-C", str(target), "fetch", "--all", "--tags"], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", ref], check=True)


def _find_latest_result_dir(run_name: str) -> str | None:
    matches = sorted(REMOTE_BENCHMARK_DIR.glob(f"*--{run_name}"))
    if not matches:
        return None
    return str(matches[-1])


def _run_benchmark_request(request: BenchmarkRequest) -> BenchmarkResult:
    if not request.no_aider and not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError(
            "Modal secret is missing DEEPSEEK_API_KEY. Create a Modal secret named "
            f"{DEEPSEEK_SECRET_NAME!r} with that environment variable."
        )

    remote_exercises_dir = remote_exercises_dir_for_request(
        request.exercises_dir,
        request.language,
    )
    _ensure_polyglot_checkout(
        request.polyglot_repo,
        request.polyglot_ref,
        remote_exercises_dir,
    )

    cmd = build_benchmark_command(
        run_name=request.run_name,
        model=request.model,
        edit_format=request.edit_format,
        language=request.language,
        threads=request.threads,
        tries=request.tries,
        exercises_dir=remote_exercises_dir,
        keywords=request.keywords,
        num_tests=request.num_tests,
        read_model_settings=request.read_model_settings,
        reasoning_effort=request.reasoning_effort,
        thinking_tokens=request.thinking_tokens,
        no_aider=request.no_aider,
        no_unit_tests=request.no_unit_tests,
        resume=request.resume,
    )

    env = os.environ.copy()
    env["AIDER_DOCKER"] = "1"
    env["AIDER_BENCHMARK_DIR"] = str(REMOTE_BENCHMARK_DIR)

    completed = subprocess.run(
        cmd,
        cwd=str(REMOTE_AIDER_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = completed.stdout[-OUTPUT_TAIL_CHARS:]
    result = BenchmarkResult(
        language=request.language,
        run_name=request.run_name,
        returncode=completed.returncode,
        result_dir=_find_latest_result_dir(request.run_name),
        command=shell_join(cmd),
        output_tail=output,
    )
    write_modal_result_summary(result)
    return result


if modal is not None:
    app = modal.App(APP_NAME)
    benchmark_volume = modal.Volume.from_name(BENCHMARK_VOLUME_NAME, create_if_missing=True)
    image = _build_modal_image(modal)

    @app.function(
        image=image,
        volumes={str(REMOTE_BENCHMARK_DIR): benchmark_volume},
        secrets=[modal.Secret.from_name(DEEPSEEK_SECRET_NAME)],
        timeout=SECONDS_PER_DAY,
        memory=12 * 1024,
        max_containers=len(DEFAULT_LANGUAGES),
    )
    def run_shard(payload: dict) -> dict:
        request = BenchmarkRequest(**payload)
        result = _run_benchmark_request(request)
        benchmark_volume.commit()
        return asdict(result)

    @app.local_entrypoint()
    def main(
        run_name: str = "atomic-deepseek-v4-pro-polyglot-atomic-modal",
        model: str = "deepseek/deepseek-chat",
        edit_format: str = "atomic",
        languages: str = "",
        threads: int = 2,
        tries: int = 2,
        exercises_dir: str = REMOTE_EXERCISES_DIR,
        keywords: str = "",
        num_tests: int = -1,
        read_model_settings: str = "",
        reasoning_effort: str = "",
        thinking_tokens: int = 0,
        no_aider: bool = False,
        no_unit_tests: bool = False,
        resume: bool = True,
        polyglot_repo: str = DEFAULT_POLYGLOT_REPO,
        polyglot_ref: str = "",
    ) -> None:
        selected_languages = parse_languages(languages or None)
        pinned_ref = polyglot_ref or detect_local_polyglot_ref()
        shards = build_shards(run_name, selected_languages)
        requests = [
            asdict(
                BenchmarkRequest(
                    run_name=shard.run_name,
                    model=model,
                    edit_format=edit_format,
                    language=shard.language,
                    threads=threads,
                    tries=tries,
                    exercises_dir=exercises_dir,
                    keywords=keywords or None,
                    num_tests=num_tests,
                    read_model_settings=read_model_settings or None,
                    reasoning_effort=reasoning_effort or None,
                    thinking_tokens=thinking_tokens or None,
                    no_aider=no_aider,
                    no_unit_tests=no_unit_tests,
                    resume=resume,
                    polyglot_repo=polyglot_repo,
                    polyglot_ref=pinned_ref,
                )
            )
            for shard in shards
        ]

        print(f"Dispatching {len(requests)} Modal benchmark shard(s).")
        if pinned_ref:
            print(f"polyglot-benchmark ref: {pinned_ref}")

        results = list(run_shard.map(requests))
        print(json.dumps(results, indent=2, sort_keys=True))
        print(
            "Download results with: "
            f"modal volume get {BENCHMARK_VOLUME_NAME} / ./tmp.modal-benchmarks"
        )
else:
    app = None

    def run_shard(payload: dict) -> dict:  # pragma: no cover
        raise RuntimeError("The Modal Python package is required to run remote shards.")
