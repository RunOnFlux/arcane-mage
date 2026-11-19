# Changelog

## 2.3.0

### Changed

- Needs arcane-mage 6.2.0.
- `provision` needs `console_password` or `console_login: false`: a node is given a console password or, on purpose, no console login. A task with neither fails before anything is created. The password follows the console's rule (12 characters, no character more than 3 times in a row).

## 2.2.0

### Changed

- Needs arcane-mage 6.1.0: a VM `provision` creates has a virtio-gpu display (`vga: virtio,clipboard=vnc`).

## 2.1.0

### Changed

- Needs arcane-mage 6.0.0: two `provision` tasks started together on one hypervisor each get their own VM id and their own install images.
- `provision` returns the id the VM was created with, from arcane-mage, not a lookup by name afterwards.

## 2.0.0

### Breaking Changes

- Needs ansible-core 2.22 and arcane-mage 5.0.1. A task fails with both versions named when the library is missing or older.
- Licensed under the Blue Oak Model License 1.0.0.

### Added

- Published on Ansible Galaxy.
- `provision` takes `chain` and `params_source`, for labnet installs.
- `requirements.txt` declares arcane-mage for execution environments.

## 1.2.0

- `provision` takes `tags`, `description`, `memory_mb` and `force_standalone`.
- `validate` takes `vm_name` and `force_standalone`.
- Every module needs arcane-mage 4.0.0.

## 1.1.0

- `provision` takes `ssh_keys` (`operate`, `status`).
