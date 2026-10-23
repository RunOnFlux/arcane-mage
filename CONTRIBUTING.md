# Contributing to Arcane Mage

## Setup

```bash
uv sync --all-extras
```

This installs the library, the CLI and TUI extras, and the dev tools.

## Before opening a pull request

```bash
uv run ruff check
uv run ruff format
uv run pytest
```

All three must be clean.

## Pull requests

- **One change per pull request.** A fix, a feature and a refactor are three PRs. If one depends on another, say so in the description.
- **Rebase onto current `master`** before asking for review, and again if `master` moves while the PR is open.
- **Do not bump the version.** Maintainers bump it when cutting a release.
- **Add a changelog entry** under `## Unreleased` in `CHANGELOG.md` (under `### Added`, `### Changed` or `### Fixed`), written for someone upgrading.
- **Update the docs the change touches:** a config field in `docs/configuration.md`, a CLI command or option in `README.md`, an Ansible module option in that module's `DOCUMENTATION`.
- **Describe the PR as it stands.** If the branch changes after review, update the description to match.
- **Testing done on real hypervisors goes in the PR description**, not in files committed to the repo.

## Comments and docstrings

A comment states what the code does or must do, and why, when the code can't say it alone. It is written for someone reading the code as it is now, with no knowledge of how it got there.

Before writing a comment, ask: *would this still be true if this change had never been made, and does the next reader need it to use or change this code correctly?* If either answer is no, leave it out.

**Write:**

- the contract: what a function returns, accepts, guarantees or changes
- a constraint the code depends on that the code can't show, such as how a Proxmox endpoint behaves
- the reason for a value or choice that would otherwise look arbitrary

**Don't write:**

- history: "was", "used to", "previously", "the old default", "now"
- the bug that prompted the change, or the incident that found it
- argument for the change ("without this, every failure read the same")
- comparison with an approach the code doesn't take ("checking X alone would…")
- the name of a particular caller, deployment or downstream tool
- a restatement of the line below it

History belongs in the commit message, and only where it explains a decision.

```python
# Don't: tells the story of the bug
# Two runs on one host each uploaded and deleted arcane_efi.raw, so whichever
# finished first deleted the file out from under the other.
def _efi_file_name(vm_id: int) -> str: ...


# Do: states the contract
def _efi_file_name(vm_id: int) -> str:
    """The EFI image a VM is created from. Each run uploads and deletes its own."""
```

```python
# Don't: history and an outside caller
# Well past the old 10 s default; keep inside the agent's 240 s timeout.
CREATE_TASK_MAX_WAIT_S = 600

# Do: the constraint behind the number
# A create allocates the full disk and imports the install images; on busy
# storage this takes minutes.
CREATE_TASK_MAX_WAIT_S = 600
```

```python
# Do: a fact about Proxmox the code relies on
# The task runs on the node whose pveproxy accepted the request, which the UPID
# names; poll its status there.
```

User-facing messages follow the same rule: say what failed and what to do about it, in one or two sentences.

## Commit messages

The subject line says what the code does after the commit, for example *"Keep a labnet install's donor among its chain sources"*. The body, when there is one, gives the rule the change implements and the reason for any decision a reader would otherwise question.

## Code

- **Types:** concrete types throughout. No `typing.Any`; use a dataclass, a Pydantic model or a precise union.
- **Imports:** at module top level.
- **CLI:** Typer.
- **Proxmox reads fail closed.** A request that errors, times out or is refused is never treated as "nothing there" or as a pass. A check that can't read what it checks fails, and says why.
- **Library code doesn't know its callers.** Timeouts, output shapes and behaviour a particular consumer depends on are parameters or documented contracts, not assumptions buried in the code.

## Tests

- Unit tests mock the Proxmox API; they never reach a real hypervisor.
- A bug fix comes with a test that fails without the fix.
- A test asserts behaviour the code promises, not the shape of the current implementation.
