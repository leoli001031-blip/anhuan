"""F1 storage tests against the formal random scratch MinIO."""
from __future__ import annotations

import unittest

from platform_foundation.f1 import storage

from f11_support import configure_formal_runtime, formal_minio_endpoint


def setUpModule() -> None:
    configure_formal_runtime()


class F1StorageTests(unittest.TestCase):
    def _cleanup_object(self, object_key: str) -> None:
        def remove() -> None:
            storage._client().remove_object(storage.BUCKET, object_key)
            self.assertFalse(storage.object_exists(object_key))

        self.addCleanup(remove)

    def test_upload_download_roundtrip(self) -> None:
        payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n" + b"f1-storage-test-bytes" * 100
        stored = storage.upload_bytes("test.pdf", "application/pdf", payload)
        self._cleanup_object(stored.object_key)
        self.assertTrue(stored.object_key.endswith(".pdf"))
        self.assertEqual(stored.size, len(payload))
        self.assertTrue(storage.object_exists(stored.object_key))
        got = storage.download_bytes(stored.object_key)
        self.assertEqual(got, payload)

    def test_presigned_url(self) -> None:
        payload = b"PK\x03\x04" + b"presign-test"
        stored = storage.upload_bytes("doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", payload)
        self._cleanup_object(stored.object_key)
        url = storage.presigned_url(stored.object_key)
        self.assertTrue(url.startswith(f"http://{formal_minio_endpoint()}"))

    def test_reject_container_mismatch(self) -> None:
        # A pdf MIME type whose magic bytes do not match is rejected.
        with self.assertRaises(storage.StorageError) as ctx:
            storage.upload_bytes("fake.pdf", "application/pdf", b"not-a-pdf")
        self.assertEqual(str(ctx.exception), "CONTAINER_MISMATCH")

    def test_reject_unsupported_type(self) -> None:
        with self.assertRaises(storage.StorageError) as ctx:
            storage.upload_bytes("evil.exe", "application/x-msdownload", b"x" * 100)
        self.assertEqual(str(ctx.exception), "FILE_TYPE_NOT_ALLOWED")

    def test_reject_empty_file(self) -> None:
        with self.assertRaises(storage.StorageError) as ctx:
            storage.upload_bytes("empty.pdf", "application/pdf", b"")
        self.assertEqual(str(ctx.exception), "EMPTY_FILE")

    def test_reject_oversize_file(self) -> None:
        with self.assertRaises(storage.StorageError) as ctx:
            storage.upload_bytes("big.pdf", "application/pdf", b"x" * (storage.MAX_SIZE_BYTES + 1))
        self.assertEqual(str(ctx.exception), "FILE_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
