# -*- coding: utf-8 -*-
import unittest

import protocol_signin as psi


class ProtocolSigninHelpers(unittest.TestCase):
    def test_is_session_sso_basic(self):
        self.assertFalse(psi.is_session_sso(""))
        self.assertFalse(psi.is_session_sso("not-a-jwt"))
        self.assertTrue(
            psi.is_session_sso(
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturepaddingxx"
            )
        )

    def test_email_selectors_cover_continue_flow_constants(self):
        # 保证登录入口与邮箱候选选择器存在（页面结构依赖）
        self.assertIn('input[type="email"]', psi._EMAIL_SELECTORS)
        self.assertIn('input[data-testid="email"]', psi._EMAIL_SELECTORS)
        self.assertIn('input[type="password"]', psi._PASS_SELECTORS)
        self.assertEqual(psi.SIGNIN_URL, "https://accounts.x.ai/sign-in")


if __name__ == "__main__":
    unittest.main()
