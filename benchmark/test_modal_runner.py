import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import benchmark.modal_runner as modal_runner

from benchmark.modal_runner import (
    DEFAULT_LANGUAGES,
    MODAL_CONTEXT_SYMLINK_PATHS,
    MODAL_DOCKERFILE_PYTHON_VERSION,
    _build_modal_image,
    _ensure_polyglot_checkout,
    _git_assume_unchanged_command,
    aggregate_modal_shards,
    build_benchmark_command,
    build_shards,
    parse_languages,
    BenchmarkResult,
    remote_exercises_dir_for_request,
    write_modal_result_summary,
)


class TestModalRunner(unittest.TestCase):
    def test_modal_dockerfile_image_pins_python_for_modal_runtime(self):
        class FakeBuiltImage:
            commands = None

            def run_commands(self, *commands):
                self.commands = commands
                return "image-with-runtime-deps"

        class FakeImage:
            calls = []

            @classmethod
            def from_dockerfile(cls, path, **kwargs):
                built_image = FakeBuiltImage()
                cls.calls.append((path, kwargs, built_image))
                return built_image

        class FakeModal:
            Image = FakeImage

        image = _build_modal_image(FakeModal)

        self.assertEqual(image, "image-with-runtime-deps")
        self.assertEqual(FakeImage.calls[0][1]["add_python"], MODAL_DOCKERFILE_PYTHON_VERSION)
        self.assertEqual(FakeImage.calls[0][1]["build_args"], {"AIDER_MODAL_RUNTIME": "1"})
        self.assertEqual(MODAL_DOCKERFILE_PYTHON_VERSION, "3.11")
        runtime_commands = FakeImage.calls[0][2].commands
        self.assertTrue(
            any(
                "uv pip install --system --no-cache-dir -e /aider[dev]" in command
                for command in runtime_commands
            )
        )
        self.assertIn("git config --global core.fileMode false", runtime_commands)
        assume_unchanged_command = _git_assume_unchanged_command(
            MODAL_CONTEXT_SYMLINK_PATHS
        )
        self.assertIn(assume_unchanged_command, runtime_commands)
        self.assertEqual(len(MODAL_CONTEXT_SYMLINK_PATHS), 6)
        for symlink_path in MODAL_CONTEXT_SYMLINK_PATHS:
            self.assertIn(symlink_path, assume_unchanged_command)

    def test_modal_dockerfile_pins_jest_29_for_exercism_throw_error_matcher(self):
        dockerfile = (Path(__file__).parent / "Dockerfile").read_text()

        self.assertIn("jest@29.7.0", dockerfile)

    def test_modal_dockerfile_pins_rust_toolchain_and_retries_downloads(self):
        dockerfile = (Path(__file__).parent / "Dockerfile").read_text()

        self.assertIn("ENV RUSTUP_MAX_RETRIES=10", dockerfile)
        self.assertIn("--default-toolchain 1.96.0", dockerfile)

    def test_write_modal_result_summary_persists_shard_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = BenchmarkResult(
                language="python",
                run_name="atomic-smoke-python",
                returncode=1,
                result_dir=tmpdir,
                command="./benchmark/benchmark.py atomic-smoke-python",
                output_tail="failed tests tail",
            )

            summary_path = write_modal_result_summary(result)

            self.assertEqual(summary_path, Path(tmpdir) / ".aider.modal-result.json")
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["language"], "python")
            self.assertEqual(summary["run_name"], "atomic-smoke-python")
            self.assertEqual(summary["returncode"], 1)
            self.assertEqual(summary["output_tail"], "failed tests tail")

    def test_aggregate_modal_shards_combines_language_trees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_result = root / "2026-06-18-00-00-00--run-python"
            go_result = root / "2026-06-18-00-00-01--run-go"

            python_case = python_result / "python" / "exercises" / "practice" / "grep"
            go_case = go_result / "go" / "exercises" / "practice" / "markdown"
            python_case.mkdir(parents=True)
            go_case.mkdir(parents=True)
            (python_case / ".aider.results.json").write_text("{}")
            (go_case / ".aider.results.json").write_text("{}")
            (python_result / ".aider.modal-result.json").write_text("{}")

            output_dir = root / "combined"
            aggregate_modal_shards([python_result, go_result], output_dir)

            python_results = output_dir / "python" / "exercises" / "practice" / "grep"
            go_results = output_dir / "go" / "exercises" / "practice" / "markdown"

            self.assertTrue((python_results / ".aider.results.json").exists())
            self.assertTrue((go_results / ".aider.results.json").exists())
            self.assertFalse((output_dir / ".aider.modal-result.json").exists())

    def test_aggregate_modal_shards_ignores_unscored_language_trees(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shard = root / "2026-06-18-00-00-00--run-python"
            scored_case = shard / "python" / "exercises" / "practice" / "grep"
            unscored_case = shard / "go" / "exercises" / "practice" / "markdown"
            scored_case.mkdir(parents=True)
            unscored_case.mkdir(parents=True)
            (scored_case / ".aider.results.json").write_text("{}")

            output_dir = root / "combined"
            aggregate_modal_shards([shard], output_dir)

            self.assertTrue((output_dir / "python").exists())
            self.assertFalse((output_dir / "go").exists())

    def test_aggregate_modal_shards_refuses_existing_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shard = root / "2026-06-18-00-00-00--run-python"
            case_dir = shard / "python" / "exercises" / "practice" / "grep"
            case_dir.mkdir(parents=True)
            output_dir = root / "combined"
            output_dir.mkdir()

            with self.assertRaises(FileExistsError):
                aggregate_modal_shards([shard], output_dir)

    def test_existing_polyglot_checkout_at_ref_skips_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "polyglot-benchmark-go"
            target.mkdir(parents=True)
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd == ["git", "-C", str(target), "rev-parse", "HEAD"]:
                    return SimpleNamespace(stdout="abc123\n")
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(modal_runner, "REMOTE_BENCHMARK_DIR", Path(tmpdir)):
                with patch("benchmark.modal_runner.subprocess.run", side_effect=fake_run):
                    _ensure_polyglot_checkout(
                        "https://example.invalid/repo.git",
                        "abc123",
                        target.name,
                    )

            self.assertEqual(calls, [["git", "-C", str(target), "rev-parse", "HEAD"]])

    def test_parse_languages_defaults_and_normalizes(self):
        self.assertEqual(parse_languages(None), DEFAULT_LANGUAGES)
        self.assertEqual(parse_languages(" Python,go, javascript "), ("python", "go", "javascript"))

    def test_default_polyglot_checkout_is_isolated_by_language(self):
        self.assertEqual(
            remote_exercises_dir_for_request("polyglot-benchmark", "cpp"),
            "polyglot-benchmark-cpp",
        )
        self.assertEqual(
            remote_exercises_dir_for_request("polyglot-benchmark", "javascript"),
            "polyglot-benchmark-javascript",
        )
        self.assertEqual(
            remote_exercises_dir_for_request("custom-exercises", "python"),
            "custom-exercises",
        )

    def test_build_shards_names_by_language(self):
        shards = build_shards("atomic-deepseek", ("go", "python"))
        self.assertEqual([shard.language for shard in shards], ["go", "python"])
        self.assertEqual([shard.run_name for shard in shards], [
            "atomic-deepseek-go",
            "atomic-deepseek-python",
        ])

    def test_build_benchmark_command_has_reproducible_flags(self):
        cmd = build_benchmark_command(
            run_name="atomic-deepseek-python",
            model="deepseek/deepseek-chat",
            edit_format="atomic",
            language="python",
            threads=3,
            tries=2,
            exercises_dir="polyglot-benchmark",
        )

        self.assertEqual(cmd[:2], ["./benchmark/benchmark.py", "atomic-deepseek-python"])
        self.assertIn("--cont", cmd)
        self.assertNotIn("--new", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("deepseek/deepseek-chat", cmd)
        self.assertIn("--edit-format", cmd)
        self.assertIn("atomic", cmd)
        self.assertIn("--languages", cmd)
        self.assertIn("python", cmd)
        self.assertIn("--threads", cmd)
        self.assertIn("3", cmd)
        self.assertIn("--tries", cmd)
        self.assertIn("2", cmd)
        self.assertNotIn("DEEPSEEK_API_KEY", " ".join(cmd))

    def test_build_benchmark_command_can_force_fresh_run(self):
        cmd = build_benchmark_command(
            run_name="fresh-run",
            model="deepseek/deepseek-chat",
            edit_format="atomic",
            language="python",
            threads=1,
            tries=1,
            exercises_dir="polyglot-benchmark",
            resume=False,
        )

        self.assertIn("--new", cmd)
        self.assertNotIn("--cont", cmd)

    def test_optional_filters_are_added_only_when_set(self):
        cmd = build_benchmark_command(
            run_name="sample",
            model="deepseek/deepseek-chat",
            edit_format="atomic",
            language="go",
            threads=1,
            tries=1,
            exercises_dir="polyglot-benchmark",
            keywords="hexadecimal",
            num_tests=1,
            read_model_settings="settings.yml",
            reasoning_effort="medium",
            thinking_tokens=1024,
        )

        self.assertIn("--keywords", cmd)
        self.assertIn("hexadecimal", cmd)
        self.assertIn("--num-tests", cmd)
        self.assertIn("1", cmd)
        self.assertIn("--read-model-settings", cmd)
        self.assertIn("settings.yml", cmd)
        self.assertIn("--reasoning-effort", cmd)
        self.assertIn("medium", cmd)
        self.assertIn("--thinking-tokens", cmd)
        self.assertIn("1024", cmd)

    def test_no_aider_smoke_flags_are_added(self):
        cmd = build_benchmark_command(
            run_name="modal-smoke",
            model="deepseek/deepseek-chat",
            edit_format="atomic",
            language="python",
            threads=1,
            tries=1,
            exercises_dir="polyglot-benchmark",
            no_aider=True,
            no_unit_tests=True,
        )

        self.assertIn("--no-aider", cmd)
        self.assertIn("--no-unit-tests", cmd)


if __name__ == "__main__":
    unittest.main()
