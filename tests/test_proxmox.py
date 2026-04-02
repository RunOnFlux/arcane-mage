from __future__ import annotations

from arcane_mage.proxmox import ApiResponse, ProxmoxApi


class TestApiResponse:
    def test_truthy_on_200(self):
        resp = ApiResponse(status=200, payload={"data": "ok"})
        assert resp

    def test_falsy_on_error(self):
        resp = ApiResponse(status=500, error="Internal Server Error")
        assert not resp

    def test_falsy_on_200_with_error(self):
        resp = ApiResponse(status=200, error="Something went wrong")
        assert not resp

    def test_unauthorized(self):
        resp = ApiResponse(status=401)
        assert resp.unauthorized

    def test_not_unauthorized(self):
        resp = ApiResponse(status=200)
        assert not resp.unauthorized

    def test_timed_out(self):
        resp = ApiResponse(timed_out=True)
        assert resp.timed_out
        assert not resp


class TestProxmoxApiParsing:
    def test_parse_token_valid(self):
        result = ProxmoxApi.parse_token("user@pam!mytoken=secret-value")

        assert result is not None
        assert result == ("user@pam", "mytoken", "secret-value")

    def test_parse_token_invalid(self):
        result = ProxmoxApi.parse_token("invalid-token-format")

        assert result is None

    def test_parse_user_pass_valid(self):
        result = ProxmoxApi.parse_user_pass("admin:password123")

        assert result is not None
        assert result == ("admin", "password123")

    def test_parse_user_pass_invalid(self):
        result = ProxmoxApi.parse_user_pass("no-colon-here")

        assert result is None

    def test_parse_user_pass_strips_pam_suffix(self):
        result = ProxmoxApi.parse_user_pass("admin@pam:password123")

        assert result is not None
        assert result == ("admin", "password123")
