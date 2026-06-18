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
                "package hexadecimal\n"
                "\n"
                "func TestHandleErrors() {\n"
                "\ter := HandleErrors(tests)\n"
                "}\n"
            )
            errors = "./hexadecimal_test.go:4:21: cannot use tests as string"

            instructions = build_test_failure_instructions(errors, Path(tmpdir), "hexadecimal.go")

        self.assertIn("Referenced test/source lines", instructions)
        self.assertIn("hexadecimal_test.go:", instructions)
        self.assertIn("er := HandleErrors(tests)", instructions)
        self.assertIn("hexadecimal.go", instructions)


if __name__ == "__main__":
    unittest.main()
