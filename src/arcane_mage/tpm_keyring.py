"""TPM 2.0 NV Index keyring backend for headless Linux systems.

Stores secrets in TPM 2.0 NV indices via ctypes calls to libtss2-esys,
accessed directly through the kernel resource manager at /dev/tpmrm0
(no tpm2-abrmd daemon required).  The user running arcane-mage must be
in the ``tss`` group (or whatever group owns /dev/tpmrm0).

Registered as a ``keyring.backends`` entry-point so that the Python
``keyring`` library discovers it automatically.  On systems without a
TPM the ``priority`` property raises, making ``viable`` return False,
and keyring silently skips this backend.

Each service+username pair is stored in a separate NV index, derived
from a hash of the key. This allows multiple credentials to coexist.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path

os.environ.setdefault("TSS2_LOG", "all+NONE")

from keyring import credentials, errors
from keyring.backend import KeyringBackend
from keyring.compat import properties

# ---- TPM constants --------------------------------------------------------

_NV_BASE_INDEX = 0x01800100
_NV_MAX_SLOTS = 16
_NV_SIZE = 128
_TPM_DEVICE = "/dev/tpmrm0"

ESYS_TR_NONE = 0xFFF
ESYS_TR_PASSWORD = 0x0FF
ESYS_TR_RH_OWNER = 0x101

TPM2_ALG_SHA256 = 0x000B

TPMA_NV_OWNERWRITE = 0x00000002
TPMA_NV_OWNERREAD = 0x00020000
TPMA_NV_NO_DA = 0x02000000

# TSS2_RC error codes
TPM2_RC_NV_RANGE = 0x0146
TPM2_RC_NV_DEFINED = 0x014C
TPM2_RC_HANDLE = 0x08B
TPM2_RC_REFERENCE_H0 = 0x0910

# ---- C struct mirrors (ctypes) -------------------------------------------

_TPMU_HA_SIZE = 64  # sizeof(TPMU_HA) = SHA-512 digest
_MAX_NV_BUFFER_SIZE = 2048


class TPM2B_Auth(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("buffer", ctypes.c_uint8 * _TPMU_HA_SIZE),
    ]


class TPM2B_Digest(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("buffer", ctypes.c_uint8 * _TPMU_HA_SIZE),
    ]


class TPMS_NvPublic(ctypes.Structure):
    _fields_ = [
        ("nvIndex", ctypes.c_uint32),
        ("nameAlg", ctypes.c_uint16),
        ("attributes", ctypes.c_uint32),
        ("authPolicy", TPM2B_Digest),
        ("dataSize", ctypes.c_uint16),
    ]


class TPM2B_NvPublic(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("nvPublic", TPMS_NvPublic),
    ]


class TPM2B_MaxNvBuffer(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("buffer", ctypes.c_uint8 * _MAX_NV_BUFFER_SIZE),
    ]


# ---- Library loading ------------------------------------------------------


def _load_esys() -> ctypes.CDLL:
    path = ctypes.util.find_library("tss2-esys")
    if not path:
        msg = "libtss2-esys not found"
        raise RuntimeError(msg)
    return ctypes.CDLL(path)


def _load_tctildr() -> ctypes.CDLL:
    path = ctypes.util.find_library("tss2-tctildr")
    if not path:
        msg = "libtss2-tctildr not found"
        raise RuntimeError(msg)
    return ctypes.CDLL(path)


def _check_rc(rc: int, func: str) -> None:
    if rc != 0:
        msg = f"{func} failed: 0x{rc:08X}"
        raise RuntimeError(msg)


def _is_nv_not_found(rc: int) -> bool:
    """RC values that mean the NV index doesn't exist."""
    base = rc & 0x0FF
    return base in (
        TPM2_RC_HANDLE,
        TPM2_RC_NV_RANGE,
        TPM2_RC_REFERENCE_H0,
    ) or (rc & 0xFFF) in (
        TPM2_RC_NV_RANGE,
        TPM2_RC_REFERENCE_H0,
    )


def _is_nv_defined(rc: int) -> bool:
    """RC value meaning the NV index already exists."""
    return (rc & 0xFFF) == TPM2_RC_NV_DEFINED or (rc & 0x0FF) == (TPM2_RC_NV_DEFINED & 0x0FF)


def _nv_index_for(service: str, username: str) -> int:
    """Derive a stable NV index from service+username."""
    key = f"{service}:{username}".encode()
    slot = int.from_bytes(hashlib.sha256(key).digest()[:2], "big") % _NV_MAX_SLOTS
    return _NV_BASE_INDEX + slot


# ---- ESAPI context manager ------------------------------------------------


class _EsapiContext:
    """Thin wrapper: open/close an ESAPI context via the kernel RM."""

    def __init__(self) -> None:
        self._esys = _load_esys()
        self._tctildr = _load_tctildr()
        self._tcti_ctx = ctypes.c_void_p()
        self._esys_ctx = ctypes.c_void_p()

    def __enter__(self) -> _EsapiContext:
        tcti_name = f"device:{_TPM_DEVICE}".encode()
        rc = self._tctildr.Tss2_TctiLdr_Initialize(
            tcti_name,
            ctypes.byref(self._tcti_ctx),
        )
        _check_rc(rc, "Tss2_TctiLdr_Initialize")

        rc = self._esys.Esys_Initialize(
            ctypes.byref(self._esys_ctx),
            self._tcti_ctx,
            None,
        )
        _check_rc(rc, "Esys_Initialize")
        return self

    def __exit__(self, *args: object) -> None:
        if self._esys_ctx:
            self._esys.Esys_Finalize(ctypes.byref(self._esys_ctx))
        if self._tcti_ctx:
            self._tctildr.Tss2_TctiLdr_Finalize(
                ctypes.byref(self._tcti_ctx),
            )

    def tr_from_tpm_public(self, handle: int) -> int:
        obj = ctypes.c_uint32()
        rc = self._esys.Esys_TR_FromTPMPublic(
            self._esys_ctx,
            handle,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
            ctypes.byref(obj),
        )
        _check_rc(rc, "Esys_TR_FromTPMPublic")
        return obj.value

    def tr_set_auth(self, handle: int, auth_bytes: bytes) -> None:
        auth = TPM2B_Auth()
        auth.size = len(auth_bytes)
        for i, b in enumerate(auth_bytes):
            auth.buffer[i] = b
        rc = self._esys.Esys_TR_SetAuth(
            self._esys_ctx,
            handle,
            ctypes.byref(auth),
        )
        _check_rc(rc, "Esys_TR_SetAuth")

    def nv_define_space(
        self,
        auth_bytes: bytes,
        nv_index: int,
        data_size: int,
    ) -> int:
        auth = TPM2B_Auth()
        auth.size = len(auth_bytes)
        for i, b in enumerate(auth_bytes):
            auth.buffer[i] = b

        nv_pub = TPM2B_NvPublic()
        nv_pub.nvPublic.nvIndex = nv_index
        nv_pub.nvPublic.nameAlg = TPM2_ALG_SHA256
        nv_pub.nvPublic.attributes = TPMA_NV_OWNERWRITE | TPMA_NV_OWNERREAD | TPMA_NV_NO_DA
        nv_pub.nvPublic.dataSize = data_size
        nv_pub.nvPublic.authPolicy.size = 0
        nv_pub.size = ctypes.sizeof(TPMS_NvPublic)

        nv_handle = ctypes.c_uint32()
        rc = self._esys.Esys_NV_DefineSpace(
            self._esys_ctx,
            ESYS_TR_RH_OWNER,
            ESYS_TR_PASSWORD,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
            ctypes.byref(auth),
            ctypes.byref(nv_pub),
            ctypes.byref(nv_handle),
        )
        _check_rc(rc, "Esys_NV_DefineSpace")
        return nv_handle.value

    def nv_write(self, nv_handle: int, data: bytes) -> None:
        buf = TPM2B_MaxNvBuffer()
        buf.size = len(data)
        for i, b in enumerate(data):
            buf.buffer[i] = b
        rc = self._esys.Esys_NV_Write(
            self._esys_ctx,
            ESYS_TR_RH_OWNER,
            nv_handle,
            ESYS_TR_PASSWORD,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
            ctypes.byref(buf),
            0,
        )
        _check_rc(rc, "Esys_NV_Write")

    def nv_read(self, nv_handle: int, size: int) -> bytes:
        data_ptr = ctypes.POINTER(TPM2B_MaxNvBuffer)()
        rc = self._esys.Esys_NV_Read(
            self._esys_ctx,
            ESYS_TR_RH_OWNER,
            nv_handle,
            ESYS_TR_PASSWORD,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
            size,
            0,
            ctypes.byref(data_ptr),
        )
        _check_rc(rc, "Esys_NV_Read")
        result = bytes(data_ptr.contents.buffer[: data_ptr.contents.size])
        self._esys.Esys_Free(data_ptr)
        return result

    def nv_undefine_space(self, nv_handle: int) -> None:
        rc = self._esys.Esys_NV_UndefineSpace(
            self._esys_ctx,
            ESYS_TR_RH_OWNER,
            nv_handle,
            ESYS_TR_PASSWORD,
            ESYS_TR_NONE,
            ESYS_TR_NONE,
        )
        _check_rc(rc, "Esys_NV_UndefineSpace")


# ---- Auth helper ----------------------------------------------------------


def _machine_auth() -> bytes:
    """Derive a 32-byte NV index auth value from machine identity."""
    mid = Path("/etc/machine-id")
    seed = mid.read_bytes().strip() if mid.exists() else b"arcane-mage-fallback"
    return hashlib.sha256(
        b"arcane-mage-tpm-nv-auth-v1:" + seed,
    ).digest()


# ---- Keyring backend ------------------------------------------------------


class TPMKeyring(KeyringBackend):
    """Store secrets in TPM 2.0 NV indices via libtss2-esys."""

    @properties.classproperty
    def priority(cls) -> float:
        if not Path(_TPM_DEVICE).exists():
            raise RuntimeError("No TPM resource manager device")
        if not ctypes.util.find_library("tss2-esys"):
            raise RuntimeError("libtss2-esys not found")
        return 5

    def get_password(self, service: str, username: str) -> str | None:
        nv_index = _nv_index_for(service, username)
        try:
            with _EsapiContext() as ctx:
                nv_handle = ctx.tr_from_tpm_public(nv_index)
                data = ctx.nv_read(nv_handle, _NV_SIZE)
                return data.decode("ascii").strip("\x00")
        except RuntimeError as exc:
            if _is_nv_not_found(_extract_rc(exc)):
                return None
            raise

    def set_password(
        self,
        service: str,
        username: str,
        password: str,
    ) -> None:
        encoded = password.encode("ascii").ljust(_NV_SIZE, b"\x00")
        if len(encoded) > _NV_SIZE:
            msg = f"Password too long for NV index ({len(encoded)} > {_NV_SIZE})"
            raise errors.PasswordSetError(msg)

        auth = _machine_auth()
        nv_index = _nv_index_for(service, username)

        with _EsapiContext() as ctx:
            try:
                nv_handle = ctx.nv_define_space(
                    auth,
                    nv_index,
                    _NV_SIZE,
                )
            except RuntimeError as exc:
                if not _is_nv_defined(_extract_rc(exc)):
                    raise
                nv_handle = ctx.tr_from_tpm_public(nv_index)
                ctx.nv_undefine_space(nv_handle)
                nv_handle = ctx.nv_define_space(
                    auth,
                    nv_index,
                    _NV_SIZE,
                )
            ctx.nv_write(nv_handle, encoded)

    def delete_password(self, service: str, username: str) -> None:
        nv_index = _nv_index_for(service, username)
        try:
            with _EsapiContext() as ctx:
                nv_handle = ctx.tr_from_tpm_public(nv_index)
                ctx.nv_undefine_space(nv_handle)
        except RuntimeError as exc:
            if not _is_nv_not_found(_extract_rc(exc)):
                raise

    def get_credential(
        self,
        service: str,
        username: str | None = None,
    ) -> credentials.Credential | None:
        return None


def _extract_rc(exc: RuntimeError) -> int:
    """Pull the hex RC from our error messages like 'Foo failed: 0x...'."""
    msg = str(exc)
    if "0x" in msg:
        hex_part = msg.split("0x")[-1].rstrip(")")
        try:
            return int(hex_part, 16)
        except ValueError:
            pass
    return 0
