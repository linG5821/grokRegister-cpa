"""chenyme / g2a 后处理：无 convert、字段与 multipart。"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import g2a_build_import as gbi


class BuildImportEntryTests(unittest.TestCase):
    def test_minimal_fields_no_team_id(self):
        entry = gbi.build_import_entry({
            "email": "A@X.com",
            "access_token": "x.y.z",
            "refresh_token": "rt",
            "id_token": "id",
            "expires_in": 100,
            "sub": "user-1",
        })
        self.assertEqual(entry["provider"], "grok_build")
        self.assertEqual(entry["client_id"], gbi.CLIENT_ID)
        self.assertNotIn("team_id", entry)
        self.assertEqual(entry["email"], "a@x.com")

    def test_append_dedup_by_email(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "imp.json")
            e1 = {"provider": "grok_build", "email": "a@x.com", "name": "a@x.com",
                  "access_token": "a1", "refresh_token": "r1", "user_id": "u1"}
            e2 = dict(e1)
            e2["access_token"] = "a2"
            e2["refresh_token"] = "r2"
            gbi.append_build_import(path, e1)
            gbi.append_build_import(path, e2)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data["accounts"]), 1)
            self.assertEqual(data["accounts"][0]["access_token"], "a2")
            self.assertEqual(data["accounts"][0]["refresh_token"], "r2")

    def test_rebuild_from_cpa_dir_is_full_latest(self):
        with tempfile.TemporaryDirectory() as d:
            auth = os.path.join(d, "auth")
            os.makedirs(auth)
            # two cpa files
            for email, at, rt in (
                ("a@x.com", "aa", "ra"),
                ("b@x.com", "bb", "rb"),
            ):
                with open(os.path.join(auth, f"xai-{email}.json"), "w", encoding="utf-8") as f:
                    json.dump({
                        "email": email, "access_token": at, "refresh_token": rt,
                        "sub": f"sub-{email}", "expires_in": 100,
                    }, f)
            # stale import with old token for a@ + orphan c@
            imp = os.path.join(d, "exports", "imp.json")
            os.makedirs(os.path.dirname(imp), exist_ok=True)
            with open(imp, "w", encoding="utf-8") as f:
                json.dump({"accounts": [
                    {"provider": "grok_build", "email": "a@x.com", "access_token": "OLD",
                     "refresh_token": "OLD_R", "name": "a@x.com"},
                    {"provider": "grok_build", "email": "c@x.com", "access_token": "cc",
                     "refresh_token": "rc", "name": "c@x.com"},
                ]}, f)
            path, n = gbi.rebuild_import_from_cpa_dir(auth, imp)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            by_email = {a["email"]: a for a in data["accounts"]}
            # a 被 CPA 最新覆盖
            self.assertEqual(by_email["a@x.com"]["access_token"], "aa")
            # b 从 CPA 补入
            self.assertEqual(by_email["b@x.com"]["access_token"], "bb")
            # c 不在 CPA 目录但原文件有 → 保留
            self.assertEqual(by_email["c@x.com"]["access_token"], "cc")
            self.assertEqual(n, 3)
            # 无重复 email
            emails = [a["email"] for a in data["accounts"]]
            self.assertEqual(len(emails), len(set(emails)))


class ChenymeImportTests(unittest.TestCase):
    def test_web_import_no_convert(self):
        import chenyme_g2a as cg

        posts = []

        def fake_post(url, **kwargs):
            posts.append((url, kwargs))
            m = MagicMock()
            m.status_code = 200
            m.text = "ok"
            m.raise_for_status = lambda: None
            m.json = lambda: {
                "data": {"tokens": {"accessToken": "tok", "accessTokenExpiresAt": "2099-01-01T00:00:00Z"}}
            }
            return m

        with patch.object(cg.requests, "post", side_effect=fake_post):
            cg.clear_token_cache()
            tok = cg.login("http://g2a.test", "admin", "pw")
            self.assertEqual(tok, "tok")
            cg.import_web_sso("http://g2a.test", tok, "sso-value")
        urls = [u for u, _ in posts]
        self.assertTrue(any(u.endswith("/accounts/web/import") for u in urls))
        self.assertFalse(any("convert" in u for u in urls))
        web = [k for u, k in posts if u.endswith("/accounts/web/import")][0]
        self.assertIn("multipart", web)

    def test_build_import_multipart_file(self):
        import chenyme_g2a as cg

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            m = MagicMock()
            m.status_code = 200
            m.text = "ok"
            m.raise_for_status = lambda: None
            return m

        entry = {
            "provider": "grok_build",
            "email": "a@x.com",
            "access_token": "a",
            "refresh_token": "r",
            "client_id": gbi.CLIENT_ID,
        }
        with patch.object(cg.requests, "post", side_effect=fake_post):
            cg.import_build_account("http://g2a.test", "adm", entry)
        self.assertTrue(captured["url"].endswith("/accounts/import"))
        self.assertIn("multipart", captured["kwargs"])


if __name__ == "__main__":
    unittest.main()
