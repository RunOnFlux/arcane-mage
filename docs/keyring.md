# Credential Storage

Arcane Mage uses the Python [keyring](https://github.com/jaraco/keyring) library to securely store Proxmox API tokens and passwords. The keyring library auto-detects the best available backend for your platform.

## Supported Backends

| Platform | Backend | Notes |
|---|---|---|
| macOS | Keychain | Works out of the box |
| Linux (desktop) | GNOME Keyring / KWallet | Requires a running desktop session |
| Linux (headless) | TPM 2.0 | Built-in backend, see below |
| Windows | Windows Credential Locker | Untested |

## How Credentials Are Stored

When you add a hypervisor via the TUI or the API, the credential (token or password) is stored in the keyring under the service name `arcane_mage`. A UUID reference is saved in `~/.fluxnode_creator.yaml` — the actual secret never touches disk.

```yaml
# ~/.fluxnode_creator.yaml
hypervisors:
  - auth_type: token
    credential: a1b2c3d4-e5f6-7890-abcd-ef1234567890  # UUID reference, not the token
    keychain: true
    name: pve1
    url: https://192.168.1.100
```

The real token is resolved at runtime via `keyring.get_password("arcane_mage", "<uuid>")`.

## TPM 2.0 Backend (Headless Linux)

For headless Linux servers without a desktop keyring (common for Fluxnode hosts running automation), Arcane Mage ships a built-in TPM 2.0 keyring backend. It stores credentials directly in TPM Non-Volatile (NV) indices via the kernel resource manager — no daemon required.

### Requirements

- Linux with TPM 2.0 hardware (most modern servers have this)
- Kernel resource manager device at `/dev/tpmrm0`
- System libraries: `libtss2-esys` and `libtss2-tctildr`
- User must be in the `tss` group (or whatever group owns `/dev/tpmrm0`)

### Installation

**Ubuntu / Debian:**

```bash
sudo apt-get install libtss2-esys-3.0.2-0 libtss2-tctildr0
sudo usermod -aG tss $USER
# Log out and back in for group change to take effect
```

**Fedora / RHEL:**

```bash
sudo dnf install tss2-libs
sudo usermod -aG tss $USER
```

### Verification

Check that the TPM device exists and you have access:

```bash
ls -la /dev/tpmrm0
# Should show something like: crw-rw---- 1 tss tss ...

groups
# Should include 'tss'
```

Verify the backend is detected:

```bash
python -c "
from keyring.backend import KeyringBackend
for cls in KeyringBackend._classes:
    try:
        p = cls.priority
        print(f'{cls.__module__}.{cls.__name__}: priority={p}')
    except Exception as e:
        print(f'{cls.__module__}.{cls.__name__}: {e}')
"
```

You should see `arcane_mage.tpm_keyring.TPMKeyring: priority=5` in the output. If it shows an error about the TPM device or library, the backend will be skipped and keyring will fall back to the next available backend.

### How It Works

- Each credential is stored in a separate TPM NV index, derived from a SHA-256 hash of the service name and credential UUID
- Authentication is derived from `/etc/machine-id`, binding credentials to the specific machine
- Up to 16 credential slots are available (more than enough for multiple hypervisors)
- Each slot holds up to 128 bytes (sufficient for any Proxmox API token)
- The backend is registered as a `keyring.backends` entry point and auto-discovered — no configuration needed
- On systems without a TPM or the required libraries, the backend is silently skipped

### Troubleshooting

**"No TPM resource manager device"**

The TPM kernel resource manager isn't available. Check:

```bash
ls /dev/tpmrm0
# If missing, check if TPM is enabled in BIOS/UEFI
# Also check: ls /dev/tpm0 (raw device without resource manager)
dmesg | grep -i tpm
```

**"libtss2-esys not found"**

The TSS2 libraries aren't installed. Install them with your package manager (see Installation above).

**Permission denied on `/dev/tpmrm0`**

Your user isn't in the `tss` group:

```bash
sudo usermod -aG tss $USER
# Log out and back in
```

## Disabling the Keyring

If you don't want to use any keyring backend, you can set `use_keyring: false` in `~/.fluxnode_creator.yaml`. Credentials will be stored in plaintext in the YAML file instead. This is not recommended.

Alternatively, for CLI-only usage, you can skip the keyring entirely by passing credentials directly:

```bash
arcane-mage provision \
    --url https://pve.local:8006 \
    --token 'user@pam!token=value' \
    -c my_nodes.yaml
```

Or via environment variables:

```bash
export ARCANE_MAGE_URL=https://pve.local:8006
export ARCANE_MAGE_TOKEN='user@pam!token=value'
arcane-mage provision -c my_nodes.yaml
```
