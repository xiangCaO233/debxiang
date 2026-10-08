import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import common  # noqa: E402


class FakeResponse:
    def __init__(self, body, *, status, headers):
        self.stream = io.BytesIO(body)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return self.stream.read(size)


class DownloadTests(unittest.TestCase):
    def test_truncated_first_response_resumes_at_offset_and_verifies_sha256(self):
        payload = b"complete upstream artifact"
        first_length = 9
        digest = hashlib.sha256(payload).hexdigest()
        responses = [
            FakeResponse(
                payload[:first_length],
                status=200,
                headers={"Content-Length": str(len(payload))},
            ),
            FakeResponse(
                payload[first_length:],
                status=206,
                headers={
                    "Content-Length": str(len(payload) - first_length),
                    "Content-Range": f"bytes {first_length}-{len(payload) - 1}/{len(payload)}",
                },
            ),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "artifact.tar.xz"
            with (
                mock.patch.object(common, "request", side_effect=responses) as request_mock,
                mock.patch.object(common.time, "sleep") as sleep_mock,
            ):
                actual = common.download("https://example.invalid/artifact", target, digest)

            self.assertEqual(actual, digest)
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(target.with_suffix(target.suffix + ".part").exists())
            self.assertEqual(
                request_mock.call_args_list,
                [
                    mock.call("https://example.invalid/artifact", offset=0),
                    mock.call("https://example.invalid/artifact", offset=first_length),
                ],
            )
            sleep_mock.assert_called_once_with(3)

    def test_complete_response_with_wrong_sha256_is_rejected_and_removed(self):
        payload = b"complete but unexpected artifact"
        wrong_digest = "0" * 64
        response = FakeResponse(
            payload,
            status=200,
            headers={"Content-Length": str(len(payload))},
        )

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "artifact.tar.gz"
            with (
                mock.patch.object(common, "request", return_value=response) as request_mock,
                mock.patch.object(common.time, "sleep") as sleep_mock,
            ):
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    common.download("https://example.invalid/artifact", target, wrong_digest)

            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(target.suffix + ".part").exists())
            request_mock.assert_called_once_with("https://example.invalid/artifact", offset=0)
            sleep_mock.assert_not_called()


class ContainerCleanupTests(unittest.TestCase):
    def test_keyboard_interrupt_removes_only_cidfile_container(self):
        container_id = "a1" * 32
        calls = []

        def fake_subprocess_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[:2] == ["podman", "run"]:
                cidfile = Path(command[command.index("--cidfile") + 1])
                cidfile.write_text(container_id + "\n")
                raise KeyboardInterrupt("cancelled test job")
            return subprocess.CompletedProcess(command, 0)

        with mock.patch.object(common.subprocess, "run", side_effect=fake_subprocess_run):
            with self.assertRaisesRegex(KeyboardInterrupt, "cancelled test job"):
                common.run("podman", "run", "--rm", "fixture-image", "fixture-command")

        self.assertEqual(len(calls), 2)
        run_command, run_kwargs = calls[0]
        self.assertEqual(run_command[:2], ["podman", "run"])
        self.assertEqual(run_command[-3:], ["--rm", "fixture-image", "fixture-command"])
        self.assertEqual(Path(run_command[run_command.index("--cidfile") + 1]).name, "cid")
        self.assertEqual(run_kwargs, {"check": True})

        cleanup_command, cleanup_kwargs = calls[1]
        self.assertEqual(cleanup_command, ["podman", "rm", "--force", container_id])
        self.assertEqual(cleanup_kwargs["check"], False)
        self.assertIs(cleanup_kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(cleanup_kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
