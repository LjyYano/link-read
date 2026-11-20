"""Offline failure-path checks for original-license collection."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("collect_licenses", Path(__file__).parents[1] / "scripts/collect_licenses.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LicenseCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.collector = module.Collector(self.output, self.root)

    def write(self, name, content=b"Unmodified upstream license fixture.\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_nested_notices_keep_exact_bytes_and_distinct_paths(self):
        first = self.write("source/LICENSE", b"Copyright A\r\nOriginal license A.\r\n")
        second = self.write("source/vendor/LICENSE", "原始声明 B\n".encode())
        self.collector.add("python", "example", "1.2", "MIT", ["https://example.test/source"],
                           [(first, "LICENSE"), (second, "vendor/LICENSE")])
        files = self.collector.components[0]["files"]
        self.assertEqual(len(files), 2)
        for entry, source in zip(files, [first, second]):
            self.assertEqual((self.output / entry["path"]).read_bytes(), source.read_bytes())
            self.assertEqual(entry["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_notice_alone_is_not_a_substitute_for_license_text(self):
        notice = self.write("NOTICE")
        with self.assertRaisesRegex(RuntimeError, r"Missing license text: npm example@1.2"):
            self.collector.add("npm", "example", "1.2", "MIT", [], [(notice, "NOTICE")])
        self.assertFalse(self.output.exists())

    def test_missing_second_declared_license_does_not_silently_pass(self):
        license_file = self.write("LICENSE-MIT")
        with self.assertRaisesRegex(RuntimeError, r"Missing declared notice LICENSE-APACHE.*example@1.2"):
            self.collector.add("rust", "example", "1.2", "MIT OR Apache-2.0", [],
                               [(license_file, "LICENSE-MIT")], ["LICENSE-MIT", "LICENSE-APACHE"])

    def test_versioned_override_requires_matching_provenance(self):
        base = "desktop/licenses/overrides/npm/_scope_example/1.2/"
        self.write(base + "LICENSE")
        with self.assertRaisesRegex(RuntimeError, "must record the upstream source URL"):
            self.collector.add("npm", "@scope/example", "1.2", "MIT", [], [])
        provenance = {"version": "1.2", "source": "https://example.test/v1.2/LICENSE"}
        self.write(base + "SOURCE.json", json.dumps(provenance).encode())
        self.collector.add("npm", "@scope/example", "1.2", "MIT", [], [], ["LICENSE"])
        self.assertIn(provenance["source"], self.collector.components[0]["sources"])
        self.assertTrue((self.output / "npm/_scope_example/1.2/upstream/LICENSE").is_file())

    def test_unsafe_relative_path_cannot_overwrite_another_directory(self):
        license_file = self.write("LICENSE")
        with self.assertRaisesRegex(RuntimeError, "Unsafe notice path"):
            self.collector.add("npm", "example", "1.2", "MIT", [], [(license_file, "../../../../LICENSE")])
        self.assertEqual(license_file.read_bytes(), b"Unmodified upstream license fixture.\n")

    def test_output_cannot_replace_an_unrelated_directory(self):
        important = self.write("keep/important.txt", b"Keep this file")
        with patch.object(sys, "argv", ["collect_licenses.py", "--target", "test-target", "--output", str(important.parent)]):
            with self.assertRaisesRegex(RuntimeError, "Refusing to replace a non-generated"):
                module.main()
        self.assertEqual(important.read_bytes(), b"Keep this file")


if __name__ == "__main__":
    unittest.main()
