from pathlib import Path

VENDOR_AUTH_POST_TEMPLATES = [
    "templates/vendor/auth/login.html",
    "templates/vendor/auth/mfa.html",
    "templates/vendor/auth/forgot-password.html",
    "templates/vendor/auth/reset-password.html",
]


def test_vendor_auth_post_forms_include_csrf_token():
    repo_root = Path(__file__).resolve().parents[1]
    for rel_path in VENDOR_AUTH_POST_TEMPLATES:
        content = (repo_root / rel_path).read_text(encoding="utf-8")
        has_csrf_include = '{% include "components/forms/csrf_input.html" %}' in content
        has_csrf_input = 'name="_csrf_token"' in content
        assert has_csrf_include or has_csrf_input, f"Missing CSRF token field in {rel_path}"
