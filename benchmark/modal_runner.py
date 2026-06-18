#!/usr/bin/env python
from __future__ import annotations

import json
import os
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

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "benchmark" / "Dockerfile"
MODAL_DOCKERFILE_PYTHON_VERSION = "3.11"


def _build_modal_image(modal_module):
    return modal_module.Image.from_dockerfile(
        DOCKERFILE,
        context_dir=REPO_ROOT,
        add_python=MODAL_DOCKERFILE_PYTHON_VERSION,
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
        "--new",
    ]

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


def _ensure_polyglot_checkout(repo: str, ref: str | None) -> None:
    target = REMOTE_BENCHMARK_DIR / REMOTE_EXERCISES_DIR
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        subprocess.run(["git", "clone", repo, str(target)], check=True)

    if ref:
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

    _ensure_polyglot_checkout(request.polyglot_repo, request.polyglot_ref)

    cmd = build_benchmark_command(
        run_name=request.run_name,
        model=request.model,
        edit_format=request.edit_format,
        language=request.language,
        threads=request.threads,
        tries=request.tries,
        exercises_dir=request.exercises_dir,
        keywords=request.keywords,
        num_tests=request.num_tests,
        read_model_settings=request.read_model_settings,
        reasoning_effort=request.reasoning_effort,
        thinking_tokens=request.thinking_tokens,
        no_aider=request.no_aider,
        no_unit_tests=request.no_unit_tests,
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
    return BenchmarkResult(
        language=request.language,
        run_name=request.run_name,
        returncode=completed.returncode,
        result_dir=_find_latest_result_dir(request.run_name),
        command=shell_join(cmd),
        output_tail=output,
    )


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
