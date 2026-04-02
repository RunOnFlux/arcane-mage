from __future__ import annotations

import pytest

from arcane_mage.helpers import ExecBinaryError, exec_binary


class TestExecBinary:
    async def test_simple_command(self):
        result = await exec_binary(["echo", "hello"])
        assert result.strip() == "hello"

    async def test_command_not_found(self):
        with pytest.raises(ChildProcessError, match="Binary not found"):
            await exec_binary(["nonexistent_binary_12345"])

    async def test_nonzero_exit_code(self):
        with pytest.raises(ExecBinaryError):
            await exec_binary(["false"])

    async def test_custom_expected_return_code(self):
        result = await exec_binary(["false"], expect_returncode=1)
        assert result == ""

    async def test_with_cwd(self, tmp_path):
        result = await exec_binary(["pwd"], cwd=str(tmp_path))
        # resolve symlinks (macOS /private/var/folders -> /var/folders etc)
        assert tmp_path.resolve().as_posix() in result.strip()


class TestExecBinaryError:
    def test_stderr_property(self):
        err = ExecBinaryError(["test"], b"out", b"error message")
        assert err.stderr() == "error message"

    def test_str_representation(self):
        err = ExecBinaryError(["test", "cmd"], b"", b"failed")
        assert "test" in str(err)
