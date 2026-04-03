from __future__ import annotations

import pytest

from arcane_mage.proxmox import ApiResponse, ParsedToken, ParsedUserPass, ProxmoxApi, ResolvedConnection


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
        assert result == ParsedToken("user@pam", "mytoken", "secret-value")
        assert result.user == "user@pam"
        assert result.username == "user"
        assert result.token_name == "mytoken"
        assert result.token_value == "secret-value"

    def test_parse_token_invalid(self):
        result = ProxmoxApi.parse_token("invalid-token-format")

        assert result is None

    def test_parse_token_username_no_realm(self):
        result = ProxmoxApi.parse_token("admin!mytoken=value")

        assert result is not None
        assert result.username == "admin"

    def test_parse_user_pass_valid(self):
        result = ProxmoxApi.parse_user_pass("admin:password123")

        assert result is not None
        assert result == ParsedUserPass("admin", "password123")
        assert result.username == "admin"

    def test_parse_user_pass_invalid(self):
        result = ProxmoxApi.parse_user_pass("no-colon-here")

        assert result is None

    def test_parse_user_pass_strips_pam_suffix(self):
        result = ProxmoxApi.parse_user_pass("admin@pam:password123")

        assert result is not None
        assert result == ParsedUserPass("admin", "password123")
        assert result.username == "admin"


class TestResolvedConnection:
    def test_creation(self):
        token = ParsedToken("user@pam", "mytoken", "secret-value")
        conn = ResolvedConnection(url="https://pve.local:8006", token=token)

        assert conn.url == "https://pve.local:8006"
        assert conn.token == token
        assert conn.token.user == "user@pam"
        assert conn.token.token_name == "mytoken"
        assert conn.token.token_value == "secret-value"

    def test_frozen(self):
        token = ParsedToken("user@pam", "mytoken", "secret-value")
        conn = ResolvedConnection(url="https://pve.local:8006", token=token)

        with pytest.raises(AttributeError):
            conn.url = "https://other.local:8006"

        with pytest.raises(AttributeError):
            conn.token = ParsedToken("other@pam", "t", "v")
