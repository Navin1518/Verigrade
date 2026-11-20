import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Backend"))

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_file:
    TEST_DB = db_file.name

os.environ["VERIGRADE_DB"] = TEST_DB

import app as backend


class BackendApiTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(TEST_DB)
        except FileNotFoundError:
            pass

    def setUp(self):
        self.client = backend.app.test_client()
        with backend.db.get_connection() as conn:
            conn.execute("DELETE FROM ledger")

    def test_health_and_publish_verify(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])

        response = self.client.post("/api/publish", json={
            "roll_no": "TEST-001",
            "student_name": "Test Student",
            "subject": "Computer Science",
            "exam_session": "2025-2026",
            "marks": 92,
        })
        self.assertEqual(response.status_code, 201)
        publish = response.get_json()
        record_id = publish["record"]["id"]

        verified = self.client.post("/api/verify", json={"record_id": record_id})
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.get_json()["status"], "VERIFIED")

    def test_duplicate_publish_is_rejected(self):
        payload = {
            "roll_no": "TEST-002",
            "student_name": "Test Student",
            "subject": "Mathematics",
            "exam_session": "2025-2026",
            "marks": 80,
        }
        first = self.client.post("/api/publish", json=payload)
        second = self.client.post("/api/publish", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "DUPLICATE_RECORD")


if __name__ == "__main__":
    unittest.main()
