# Changelog

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
