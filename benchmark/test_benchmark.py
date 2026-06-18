# flake8: noqa: E501

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCHMARK_DIR))
spec = importlib.util.spec_from_file_location("aider_benchmark_script", BENCHMARK_DIR / "benchmark.py")
benchmark_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark_script)

cleanup_test_output = benchmark_script.cleanup_test_output
build_test_failure_instructions = benchmark_script.build_test_failure_instructions


class TestCleanupTestOutput(unittest.TestCase):
    def test_cleanup_test_output(self):
        output = "Ran 5 tests in 0.003s\nOK"
        expected = "\nOK"
        self.assertEqual(cleanup_test_output(output, Path("testdir")), expected)

        output = "OK"
        expected = "OK"
        self.assertEqual(cleanup_test_output(output, Path("testdir")), expected)

    def test_cleanup_test_output_lines(self):
        output = """F
======================================================================
FAIL: test_cleanup_test_output (test_benchmark.TestCleanupTestOutput.test_cleanup_test_output)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/gauthier/Projects/aider/benchmark/test_benchmark.py", line 14, in test_cleanup_test_output
    self.assertEqual(cleanup_test_output(output), expected)
AssertionError: 'OK' != 'OKx'
- OK
+ OKx
?   +
"""

        expected = """F
====
FAIL: test_cleanup_test_output (test_benchmark.TestCleanupTestOutput.test_cleanup_test_output)
----
Traceback (most recent call last):
  File "/Users/gauthier/Projects/aider/benchmark/test_benchmark.py", line 14, in test_cleanup_test_output
    self.assertEqual(cleanup_test_output(output), expected)
AssertionError: 'OK' != 'OKx'
- OK
+ OKx
?   +
"""
        self.assertEqual(cleanup_test_output(output, Path("testdir")), expected)

    def test_build_test_failure_instructions_adds_referenced_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "hexadecimal_test.go"
            test_file.write_text(
                "// Your solution must include the following definitions:\n"
                "//\n"
                "// func ParseHex(string) (int64, error)\n"
                "// func HandleErrors([]string) []string\n"
                "//\n"
                "// HandleErrors returns \\\"none\\\", \\\"syntax\\\", or \\\"range\\\".\n"
                "\n"
                "package hexadecimal\n"
                "\n"
                "var testCases = []struct {\n"
                "\tin string\n"
                "\terrCase string\n"
                "}{\n"
                "\t{\\\"1\\\", \\\"none\\\"},\n"
                "\t{\\\"\\\", \\\"syntax\\\"},\n"
                "}\n"
                "\n"
                "func TestHandleErrors() {\n"
                "\ttests := []string{\\\"1\\\"}\n"
                "\ter := HandleErrors(tests)\n"
                "\tif len(er) != len(tests) {\n"
                "\t\tt.Fatal(\"wrong length\")\n"
                "\t}\n"
                "}\n"
            )
            errors = "./hexadecimal_test.go:20:21: cannot use tests as string"

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "hexadecimal.go")

        self.assertIn("Referenced test/source lines", instructions)
        self.assertIn("hexadecimal_test.go:", instructions)
        self.assertIn("er := HandleErrors(tests)", instructions)
        self.assertIn("if len(er) != len(tests)", instructions)
        self.assertIn("func ParseHex(string) (int64, error)", instructions)
        self.assertIn("\\\"none\\\", \\\"syntax\\\", or \\\"range\\\"", instructions)
        self.assertIn("hexadecimal.go", instructions)

    def test_build_test_failure_instructions_includes_adjacent_preceding_test_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "acronym_test.rs"
            lines = [f"// filler {line_no}" for line_no in range(1, 101)]
            lines[70] = "fn apostrophes() { expected HC }"
            lines[79] = "fn underscore_emphasis() { expected TRNT }"
            lines[88] = "    assert_eq!(output, expected);"
            test_file.write_text("\n".join(lines))
            errors = "./acronym_test.rs:89:5: assertion failed"

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "lib.rs")

        self.assertIn("apostrophes", instructions)
        self.assertIn("underscore_emphasis", instructions)
        self.assertIn("assert_eq!(output, expected)", instructions)

    def test_build_test_failure_instructions_keeps_original_exercise_instructions_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "example_test.py"
            test_file.write_text("def test_example():\n    assert solve() == 1\n")
            errors = "./example_test.py:2: AssertionError"

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "solution.py")

        self.assertIn("original exercise instructions", instructions)
        self.assertNotIn("Use only the compiler messages", instructions)

    def test_build_test_failure_instructions_points_to_constructor_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "SeriesTest.java"
            test_file.write_text(
                "class SeriesTest {\n"
                "  void emptySeries() {\n"
                "    assertThatExceptionOfType(IllegalArgumentException.class)\n"
                "      .isThrownBy(() -> new Series(\"\"));\n"
                "  }\n"
                "}\n"
            )
            errors = "./SeriesTest.java:4: Expecting code to raise a throwable"

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "Series.java")

        self.assertIn("constructor", instructions)
        self.assertIn("entry point", instructions)

    def test_build_test_failure_instructions_preserves_exception_and_helper_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "CircularBufferTest.java"
            test_file.write_text(
                "class CircularBufferTest {\n"
                "  void reads() throws BufferIOException {\n"
                "    new CircularBuffer<String>(1).read();\n"
                "  }\n"
                "}\n"
            )
            helper_file = Path(tmpdir) / "SgfNode.java"
            helper_file.write_text("class SgfNode {}\n")
            errors = (
                "./CircularBufferTest.java:3: error: unreported exception BufferIOException; "
                "must be caught or declared to be thrown\n"
                "./SgfParsing.java:147: error: duplicate class: SgfNode"
            )

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "CircularBuffer.java")

        self.assertIn("exception contracts", instructions)
        self.assertIn("throws clauses", instructions)
        self.assertIn("helper classes", instructions)
        self.assertIn("duplicate class", instructions)

    def test_build_test_failure_instructions_guides_opaque_coordinate_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "WordSearcherTest.java"
            test_file.write_text(
                "class WordSearcherTest {\n"
                "  void locatesWord() {\n"
                "    var expected = new WordLocation(new Pair(1, 1), new Pair(7, 1));\n"
                "    var actual = wordSearcher.search(words, grid);\n"
                "    assertThat(actual).isEqualTo(expected);\n"
                "  }\n"
                "}\n"
            )
            errors = (
                "./WordSearcherTest.java:5: AssertionFailedError:\n"
                "expected: Optional[WordLocation@87b]\n"
                " but was: Optional[WordLocation@25]"
            )

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "WordSearcher.java")

        self.assertIn("opaque object", instructions)
        self.assertIn("expected constructors", instructions)
        self.assertIn("coordinate origin", instructions)
        self.assertIn("new WordLocation(new Pair(1, 1), new Pair(7, 1))", instructions)


class TestLanguageCopy(unittest.TestCase):
    def test_copy_selected_language_practice_dirs_copies_only_requested_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "polyglot"
            result = Path(tmpdir) / "result"
            for language in ("cpp", "python", "rust"):
                practice = original / language / "exercises" / "practice" / f"{language}-exercise"
                practice.mkdir(parents=True)
                (practice / "README.md").write_text(language)

            self.assertTrue(hasattr(benchmark_script, "copy_selected_language_practice_dirs"))

            benchmark_script.copy_selected_language_practice_dirs(original, result, "python")

            self.assertTrue(
                (result / "python" / "exercises" / "practice" / "python-exercise").is_dir()
            )
            self.assertFalse((result / "cpp").exists())
            self.assertFalse((result / "rust").exists())


if __name__ == "__main__":
    unittest.main()
