import os
import sys
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertEqual(signup.get_json()["user"]["role"], "viewer")
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["user"]["email"], "test@example.com")

        logout = self.client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": me.get_json()["csrf_token"]},
        )
        self.assertEqual(logout.status_code, 200)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 401)

        login = self.client.post("/api/auth/login", json={
            "email": "test@example.com",
            "password": "StrongPass123",
        })
        self.assertEqual(login.status_code, 200)

    def test_session_publish_requires_role_and_csrf_token(self):
        signup = self.client.post("/api/auth/signup", json={
            "name": "Publisher Account",
            "email": "session-publisher@example.com",
            "password": "StrongPass123",
        })
        token = signup.get_json()["csrf_token"]
        payload = {
            "roll_no": "SESSION-001",
            "student_name": "Session Student",
            "subject": "Science",
            "marks": 70,
        }

        forbidden = self.client.post(
            "/api/publish",
            headers={"X-CSRF-Token": token},
            json=payload,
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.get_json()["code"], "FORBIDDEN")

        user = backend.db.find_user_by_email("session-publisher@example.com")
        backend.db.update_user_role(user["id"], "publisher")
        missing_token = self.client.post("/api/publish", json=payload)
        self.assertEqual(missing_token.status_code, 403)
        self.assertEqual(missing_token.get_json()["code"], "CSRF_FAILED")

        published = self.client.post(
            "/api/publish",
            headers={"X-CSRF-Token": token},
            json=payload,
        )
        self.assertEqual(published.status_code, 201)

    def test_bootstrap_admin_can_manage_roles(self):
        with patch.dict(os.environ, {"VERIGRADE_ADMIN_EMAIL": "bootstrap@example.com"}):
            admin_client = backend.app.test_client()
            admin_signup = admin_client.post("/api/auth/signup", json={
                "name": "Bootstrap Admin",
                "email": "bootstrap@example.com",
                "password": "StrongPass123",
            })
            self.assertEqual(admin_signup.status_code, 201)
            self.assertEqual(admin_signup.get_json()["user"]["role"], "admin")

            viewer_client = backend.app.test_client()
            viewer_signup = viewer_client.post("/api/auth/signup", json={
                "name": "New Publisher",
                "email": "new-publisher@example.com",
                "password": "StrongPass123",
            })
            self.assertEqual(viewer_signup.get_json()["user"]["role"], "viewer")
            viewer = backend.db.find_user_by_email("new-publisher@example.com")

            listed = admin_client.get("/api/admin/users")
            self.assertEqual(listed.status_code, 200)
            missing_csrf = admin_client.patch(
                f"/api/admin/users/{viewer['id']}/role",
                json={"role": "publisher"},
            )
            self.assertEqual(missing_csrf.status_code, 403)
            self.assertEqual(missing_csrf.get_json()["code"], "CSRF_FAILED")
            changed = admin_client.patch(
                f"/api/admin/users/{viewer['id']}/role",
                headers={"X-CSRF-Token": admin_signup.get_json()["csrf_token"]},
                json={"role": "publisher"},
            )
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(changed.get_json()["user"]["role"], "publisher")

            self_change = admin_client.patch(
                f"/api/admin/users/{admin_signup.get_json()['user']['id']}/role",
                headers={"X-CSRF-Token": admin_signup.get_json()["csrf_token"]},
                json={"role": "viewer"},
            )
            self.assertEqual(self_change.status_code, 403)

        denied = viewer_client.get("/api/admin/users")
        self.assertEqual(denied.status_code, 403)

    def test_session_correction_requires_two_distinct_approvers(self):
        first_client = self.client
        first_signup = first_client.post("/api/auth/signup", json={
            "name": "Approver One Session",
            "email": "approver-one-session@example.com",
            "password": "StrongPass123",
        })
        second_client = backend.app.test_client()
        second_signup = second_client.post("/api/auth/signup", json={
            "name": "Approver Two Session",
            "email": "approver-two-session@example.com",
            "password": "StrongPass123",
        })
        first_user = backend.db.find_user_by_email("approver-one-session@example.com")
        second_user = backend.db.find_user_by_email("approver-two-session@example.com")
        viewer_decision = first_client.post("/api/corrections", json={})
        self.assertEqual(viewer_decision.status_code, 403)
        self.assertEqual(viewer_decision.get_json()["code"], "FORBIDDEN")
        backend.db.update_user_role(first_user["id"], "approver")
        backend.db.update_user_role(second_user["id"], "approver")

        published = backend.app.test_client().post(
            "/api/publish",
            headers=self.publish_headers(),
            json={
                "roll_no": "SESSION-COR-001",
                "student_name": "Correction Student",
                "subject": "Physics",
                "marks": 60,
            },
        )
        record_id = published.get_json()["record"]["id"]
        payload = {
            "target_id": record_id,
            "new_marks": 65,
            "reason": "Verified source-register transcription error.",
            "second_approver_email": "approver-one-session@example.com",
            "second_approver_password": "StrongPass123",
        }
        same_approver = first_client.post(
            "/api/corrections",
            headers={"X-CSRF-Token": first_signup.get_json()["csrf_token"]},
            json=payload,
        )
        self.assertEqual(same_approver.status_code, 400)
        self.assertEqual(same_approver.get_json()["code"], "APPROVERS_NOT_DISTINCT")

        payload["second_approver_email"] = "approver-two-session@example.com"
        payload["second_approver_password"] = "WrongPass123"
        wrong_password = first_client.post(
            "/api/corrections",
            headers={"X-CSRF-Token": first_signup.get_json()["csrf_token"]},
            json=payload,
        )
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(wrong_password.get_json()["code"], "UNAUTHENTICATED")

        payload["second_approver_email"] = "approver-two-session@example.com"
        payload["second_approver_password"] = "StrongPass123"
        approved = first_client.post(
            "/api/corrections",
            headers={"X-CSRF-Token": first_signup.get_json()["csrf_token"]},
            json=payload,
        )
        self.assertEqual(approved.status_code, 201)
        record = approved.get_json()["record"]
        self.assertEqual(record["approver_1"], "Approver One Session")
        self.assertEqual(record["approver_2"], "Approver Two Session")

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
