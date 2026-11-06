# Copyright: (c) 2026, RunOnFlux
# License: BlueOak-1.0.0

from importlib.metadata import version

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible.module_utils.compat.version import LooseVersion

MINIMUM_VERSION = "5.0.1"


def require_arcane_mage(module: AnsibleModule, import_error: str | None) -> None:
    """Fail the task unless the controller has arcane-mage at MINIMUM_VERSION or later.

    import_error is the traceback from the module's own arcane_mage import, None if it worked.
    """
    if import_error:
        module.fail_json(msg=missing_required_lib(f"arcane-mage >= {MINIMUM_VERSION}"), exception=import_error)

    installed = version("arcane-mage")
    if LooseVersion(installed) < LooseVersion(MINIMUM_VERSION):
        module.fail_json(msg=f"arcane-mage >= {MINIMUM_VERSION} is required, found {installed}")
