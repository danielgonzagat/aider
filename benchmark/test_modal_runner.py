import json
from pathlib import Path
import tempfile
import unittest

from benchmark.modal_runner import (
    DEFAULT_LANGUAGES,
    MODAL_CONTEXT_SYMLINK_PATHS,
    MODAL_DOCKERFILE_PYTHON_VERSION,
    _build_modal_image,
    _git_assume_unchanged_command,
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
        self.assertIn("--new", cmd)
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
