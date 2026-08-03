from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_firmware_backups.sh"
FLASH_SIZE = 524_288


class FirmwareBackupScriptTest(unittest.TestCase):
    def test_rejects_the_same_file_as_both_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "original.bin"
            backup.write_bytes(b"\xaa" * FLASH_SIZE)
            result = subprocess.run(
                [str(SCRIPT), str(backup), str(backup), str(FLASH_SIZE)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("two distinct backup files", result.stderr)

    def test_accepts_two_distinct_identical_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "original-a.bin"
            second = Path(directory) / "original-b.bin"
            first.write_bytes(b"\xaa" * FLASH_SIZE)
            second.write_bytes(b"\xaa" * FLASH_SIZE)
            result = subprocess.run(
                [str(SCRIPT), str(first), str(second), str(FLASH_SIZE)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("byte-for-byte identical", result.stdout)

    def test_rejects_identical_but_truncated_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "original-a.bin"
            second = Path(directory) / "original-b.bin"
            first.write_bytes(b"truncated")
            second.write_bytes(b"truncated")
            result = subprocess.run(
                [str(SCRIPT), str(first), str(second), str(FLASH_SIZE)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stderr)


if __name__ == "__main__":
    unittest.main()
