from __future__ import annotations

from ui_ux_company.auth import AuthService


def test_auth_token_and_roles(settings):
    settings.auth_enabled = True
    settings.auth_token = "test-token"
    settings.auth_role = "dictator"
    auth = AuthService(settings)
    user = auth.authenticate("test-token")
    assert user is not None
    assert user.role == "dictator"
    assert auth.authenticate("wrong-token") is None
    assert auth.can_write(user, "POST", "/api/runs")
    assert not auth.can_write(
        type(user)(name="Viewer", role="viewer", token_sha256=""),
        "POST",
        "/api/runs",
    )
