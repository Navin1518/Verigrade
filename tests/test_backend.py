import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Backend"))

with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_file:
    TEST_DB = db_file.name

os.environ["VERIGRADE_DB"] = TEST_DB
os.environ["VERIGRADE_API_KEYS"] = (
    '{"publish-secret":{"name":"Registrar","roles":["publisher"]},'
    '"approver-one-secret":{"name":"Approver One","roles":["approver"]},'
    '"approver-two-secret":{"name":"Approver Two","roles":["approver"]}}'
)
os.environ["VERIGRADE_SESSION_SECRET"] = "test-session-secret-only"

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

    def publish_headers(self):
        return {"Authorization": "Bearer publish-secret"}

    def correction_headers(self):
        return {
            "Authorization": "Bearer approver-one-secret",
            "X-Verigrade-Approver-2": "approver-two-secret",
        }

    def test_health_and_publish_verify(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])

        response = self.client.post("/api/publish", headers=self.publish_headers(), json={
            "roll_no": "TEST-001",
            "student_name": "Test Student",
            "subject": "Computer Science",
            "exam_session": "2025-2026",
            "marks": 92,
            "max_marks": 100,
        })
        self.assertEqual(response.status_code, 201)
        publish = response.get_json()
        record_id = publish["record"]["id"]
        self.assertEqual(publish["record"]["max_marks"], 100)

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
        first = self.client.post("/api/publish", headers=self.publish_headers(), json=payload)
        second = self.client.post("/api/publish", headers=self.publish_headers(), json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "DUPLICATE_RECORD")

    def test_mutations_require_authentication(self):
        publish = self.client.post("/api/publish", json={
            "roll_no": "TEST-X",
            "student_name": "Test Student",
            "subject": "Science",
            "marks": 70,
        })
        correction = self.client.post("/api/corrections", json={"target_id": 1, "new_marks": 75})
        self.assertEqual(publish.status_code, 401)
        self.assertEqual(publish.get_json()["code"], "UNAUTHENTICATED")
        self.assertEqual(correction.status_code, 401)
        self.assertEqual(correction.get_json()["code"], "UNAUTHENTICATED")

    def test_correction_uses_authenticated_approver_names(self):
        published = self.client.post("/api/publish", headers=self.publish_headers(), json={
            "roll_no": "TEST-003",
            "student_name": "Test Student",
            "subject": "Physics",
            "marks": 60,
            "max_marks": 100,
        })
        record_id = published.get_json()["record"]["id"]
        response = self.client.post(
            "/api/corrections",
            headers=self.correction_headers(),
            json={
                "target_id": record_id,
                "new_marks": 65,
                "reason": "Verified transcription error in the source register.",
                "approver_1": "Forged Name",
                "approver_2": "Forged Name",
            },
        )
        self.assertEqual(response.status_code, 201)
        record = response.get_json()["record"]
        self.assertEqual(record["approver_1"], "Approver One")
        self.assertEqual(record["approver_2"], "Approver Two")

    def test_max_marks_participates_in_hash(self):
        published = self.client.post("/api/publish", headers=self.publish_headers(), json={
            "roll_no": "TEST-004",
            "student_name": "Test Student",
            "subject": "Chemistry",
            "marks": 80,
            "max_marks": 100,
        })
        record_id = published.get_json()["record"]["id"]
        with backend.db.get_connection() as conn:
            conn.execute(
                "UPDATE ledger SET max_marks = 200 WHERE id = ?",
                (record_id,),
            )
        verified = self.client.post(
            "/api/verify",
            json={"record_id": record_id},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertIn(
            verified.get_json()["status"],
            ("TAMPERED", "CORRUPTED"),
        )

    def test_signup_login_logout(self):
        signup = self.client.post("/api/auth/signup", json={
            "name": "Test Admin",
            "email": "Test@Example.com",
            "password": "StrongPass123",
        })
        self.assertEqual(signup.status_code, 201)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["user"]["email"], "test@example.com")

        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 200)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 401)

        login = self.client.post("/api/auth/login", json={
            "email": "test@example.com",
            "password": "StrongPass123",
        })
        self.assertEqual(login.status_code, 200)

    def test_login_rejects_wrong_password(self):
        self.client.post("/api/auth/signup", json={
            "name": "Test User",
            "email": "wrong@example.com",
            "password": "StrongPass123",
        })
        response = self.client.post("/api/auth/login", json={
            "email": "wrong@example.com",
            "password": "WrongPass123",
        })
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
