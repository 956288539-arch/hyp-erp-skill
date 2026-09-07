"""Exercise persisted preferences with fictional identities in isolated directories."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "user_profile", Path(__file__).resolve().parents[1] / "hyp-erp" / "scripts" / "user_profile.py"
)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def field(value, source="erp"):
    return {"value": value, "source": source}


class UserProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hyp-profile-test-")
        self.root = Path(self.temp.name) / "profiles"
        self.alice = profiles.identity("https://erp.example.test", "TEST-ACCOUNT-A")
        self.bob = profiles.identity("https://erp.example.test", "TEST-ACCOUNT-B")

    def tearDown(self):
        self.temp.cleanup()

    def test_first_use_and_next_session(self):
        self.assertIsNone(profiles.load_profile(self.root, self.alice))
        profiles.save_profile(self.root, self.alice, {
            "employee_name": field("测试用户甲"),
            "reimbursement_department": field("测试部门甲"),
            "archive_root": field(str(Path(self.temp.name) / "发票"), "user"),
        })
        loaded = profiles.load_profile(Path(str(self.root)), dict(self.alice))
        self.assertEqual(loaded["fields"]["reimbursement_department"]["value"], "测试部门甲")
        self.assertEqual(loaded["fields"]["archive_root"]["source"], "user")
        self.assertIn("verified_at", loaded["fields"]["employee_name"])

    def test_accounts_and_sites_do_not_share_defaults(self):
        profiles.save_profile(self.root, self.alice, {"payee_name": field("测试收款人甲")})
        self.assertIsNone(profiles.load_profile(self.root, self.bob))
        profiles.save_profile(self.root, self.bob, {"payee_name": field("测试收款人乙")})
        other_site = profiles.identity("https://another.example.test", self.alice["account"])
        self.assertIsNone(profiles.load_profile(self.root, other_site))
        self.assertEqual(profiles.load_profile(self.root, self.alice)["fields"]["payee_name"]["value"], "测试收款人甲")

    def test_update_and_field_forgetting_preserve_other_data(self):
        profiles.save_profile(self.root, self.alice, {
            "company": field("测试公司"), "payee_name": field("旧收款人"), "bank_last4": field("1234")
        })
        data = profiles.save_profile(self.root, self.alice, {
            "payee_name": field("新收款人", "user"), "bank_last4": None
        })
        self.assertEqual(data["fields"]["company"]["value"], "测试公司")
        self.assertEqual(data["fields"]["payee_name"]["value"], "新收款人")
        self.assertNotIn("bank_last4", data["fields"])

    def test_forgetting_one_account_preserves_another(self):
        for owner in (self.alice, self.bob):
            profiles.save_profile(self.root, owner, {"employee_name": field("同名测试用户")})
        self.assertTrue(profiles.forget_profile(self.root, self.alice))
        self.assertIsNone(profiles.load_profile(self.root, self.alice))
        self.assertIsNotNone(profiles.load_profile(self.root, self.bob))

    def test_rejects_secrets_and_per_claim_defaults_without_overwrite(self):
        profiles.save_profile(self.root, self.alice, {"company": field("测试公司")})
        path = profiles.profile_path(self.root, self.alice)
        before = path.read_bytes()
        for key in ("password", "cookie", "bank_account", "project", "reimbursement_type", "amount"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                profiles.save_profile(self.root, self.alice, {key: field("test")})
            self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            profiles.save_profile(self.root, self.alice, {"bank_last4": field("1234567890123456")})

    def test_rejects_corruption_and_wrong_identity(self):
        profiles.save_profile(self.root, self.alice, {"company": field("测试公司")})
        path = profiles.profile_path(self.root, self.alice)
        path.write_text("not valid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            profiles.save_profile(self.root, self.alice, {"company": field("其他公司")})
        self.assertEqual(path.read_text(encoding="utf-8"), "not valid json")
        path.write_text(json.dumps({"schema_version": 1, "identity": self.bob, "fields": {}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            profiles.load_profile(self.root, self.alice)

    def test_in_progress_write_cannot_be_overwritten(self):
        with profiles.profile_lock(self.root, self.alice):
            with self.assertRaises(ValueError):
                profiles.save_profile(self.root, self.alice, {"company": field("测试公司")})
        self.assertIsNone(profiles.load_profile(self.root, self.alice))


if __name__ == "__main__":
    unittest.main()
