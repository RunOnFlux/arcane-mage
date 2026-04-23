from __future__ import annotations

try:
    import typer
except ModuleNotFoundError:
    raise SystemExit(
        "arcane-mage CLI requires the 'cli' extra.\n"
        "Install with: uv add arcane-mage[cli]  (or arcane-mage[tui] for the full UI)"
    ) from None

import asyncio
import json
import os
import sys
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .batch import BatchProvisioner
from .models import ArcaneCreatorConfig, ArcaneOsConfig, ArcaneOsConfigGroup
from .proxmox import ProxmoxApi, ResolvedConnection

app = typer.Typer(
    name="arcane-mage",
    help="Automated Fluxnode ArcaneOS provisioning tools",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

_CHECK_MARK = "[green]✓[/green]"
_CROSS_MARK = "[red]✗[/red]"


# ---- JSON response helpers ------------------------------------------------


class CliError(Exception):
    """Raised by helpers when input validation or resolution fails."""


def _json_ok(data: dict | None = None) -> str:
    result = {"ok": True}
    if data:
        result.update(data)
    return json.dumps(result)


def _json_error(error: str, data: dict | None = None) -> str:
    result: dict = {"ok": False, "error": error}
    if data:
        result.update(data)
    return json.dumps(result)


def _handle_error(e: CliError, use_json: bool) -> None:
    if use_json:
        print(_json_error(str(e)))
    else:
        err_console.print(Panel(f"[red]{e}[/red]", title="[bold red]Error[/bold red]", border_style="red"))
    raise typer.Exit(1)


# ---- Shared helpers (raise CliError, never print) -------------------------


def version_callback(value: bool) -> None:
    if value:
        pkg_version = get_version("arcane-mage")
        console.print(f"arcane-mage {pkg_version}")
        raise typer.Exit()


async def _backfill_hypervisor_names(creator_config: ArcaneCreatorConfig) -> None:
    """Fetch and store names for hypervisors that don't have one yet."""
    dirty = False
    for hyper in creator_config.hypervisors:
        if hyper.name:
            continue

        cred = hyper.real_credential()
        if not cred:
            continue

        try:
            if hyper.auth_type == "token":
                parsed = ProxmoxApi.parse_token(cred)
                if not parsed:
                    continue
                api = ProxmoxApi.from_token(hyper.url, parsed)
            else:
                continue

            async with api:
                res = await api.get_hypervisor_nodes()
                if res and isinstance(res.payload, list):
                    names = [n.get("node", "") for n in res.payload if n.get("node")]
                    if names:
                        hyper.name = names[0] if len(names) == 1 else ",".join(sorted(names))
                        dirty = True
        except Exception:
            continue

    if dirty:
        creator_config.write()


def _resolve_hypervisor(hypervisor: str) -> ResolvedConnection:
    """Resolve URL and parsed token from a stored hypervisor by name."""
    creator_config = ArcaneCreatorConfig.from_fs()
    if not creator_config.has_config:
        raise CliError("No hypervisors configured. Add one via the TUI first.")

    needs_backfill = any(h.name is None for h in creator_config.hypervisors)
    if needs_backfill:
        asyncio.run(_backfill_hypervisor_names(creator_config))
        creator_config = ArcaneCreatorConfig.from_fs()

    match = None
    for h in creator_config.hypervisors:
        if h.name and hypervisor in h.name.split(","):
            match = h
            break
        if urlparse(h.url).hostname == hypervisor or h.url == hypervisor:
            match = h
            break

    if not match:
        names = [h.display_label for h in creator_config.hypervisors]
        raise CliError(f"Hypervisor '{hypervisor}' not found. Available: {', '.join(names)}")

    cred = match.real_credential()
    if not cred:
        raise CliError(f"Unable to retrieve credential for '{hypervisor}' from keyring")

    parsed = ProxmoxApi.parse_token(cred)
    if not parsed:
        raise CliError(f"Invalid token for '{hypervisor}'. Expected: user@pam!tokenname=tokenvalue")

    return ResolvedConnection(match.url, parsed)


def _resolve_connection(
    url: Optional[str],
    token: Optional[str],
    hypervisor: Optional[str],
) -> ResolvedConnection:
    """Resolve URL and parsed token from flags, env vars, stdin, or stored hypervisor."""
    if hypervisor:
        return _resolve_hypervisor(hypervisor)

    resolved_url = url or os.environ.get("ARCANE_MAGE_URL")
    if not resolved_url:
        raise CliError("No URL provided. Pass --url, set ARCANE_MAGE_URL, or use --hypervisor")

    resolved_token = token
    if not resolved_token and not sys.stdin.isatty():
        resolved_token = sys.stdin.read().strip() or None
    if not resolved_token:
        resolved_token = os.environ.get("ARCANE_MAGE_TOKEN")
    if not resolved_token:
        raise CliError("No token provided. Pass --token, pipe via stdin, set ARCANE_MAGE_TOKEN, or use --hypervisor")

    parsed = ProxmoxApi.parse_token(resolved_token)
    if not parsed:
        raise CliError("Invalid token format. Expected: user@pam!tokenname=tokenvalue")

    return ResolvedConnection(resolved_url, parsed)


def _load_configs(config: str) -> ArcaneOsConfigGroup:
    configs = ArcaneOsConfigGroup.from_fs(Path(config))
    if not configs:
        raise CliError(f"No nodes found in {config}")
    return configs


# ---- Commands -------------------------------------------------------------


@app.callback()
def main(
    version: Optional[bool] = typer.Option(None, "-v", "--version", callback=version_callback, is_eager=True, help="Show the application version"),
) -> None:
    pass


@app.command()
def tui(
    config: str = typer.Option("fluxnodes.yaml", "-c", "--config", help="The config file"),
) -> None:
    """Launch the interactive provisioning UI."""
    try:
        from .arcane_mage import ArcaneMage
    except ModuleNotFoundError:
        raise SystemExit(
            "arcane-mage TUI requires the 'tui' extra.\n"
            "Install with: uv add arcane-mage[tui]"
        ) from None

    tui_app = ArcaneMage(fluxnode_config=config)
    tui_app.run()


@app.command()
def provision(
    url: Optional[str] = typer.Option(None, help="Proxmox API URL (also accepts ARCANE_MAGE_URL)"),
    token: Optional[str] = typer.Option(None, help="API token (also accepts stdin or ARCANE_MAGE_TOKEN)"),
    hypervisor: Optional[str] = typer.Option(None, "--hypervisor", "-H", help="Use stored hypervisor by name"),
    config: str = typer.Option("fluxnodes.yaml", "-c", "--config", help="The config file"),
    node_filter: Optional[str] = typer.Option(None, "--node", help="Cluster node to target (e.g. pve1)"),
    start: Optional[bool] = typer.Option(None, "--start/--no-start", help="Override start_on_creation setting"),
    use_json: bool = typer.Option(False, "--json", help="Output JSON instead of text"),
) -> None:
    """Provision Fluxnodes on a Proxmox hypervisor via the API."""
    from .log import configure_cli_logging
    from .provisioner import Provisioner

    if not use_json:
        configure_cli_logging()

    try:
        conn = _resolve_connection(url, token, hypervisor)
        configs = _load_configs(config)
    except CliError as e:
        _handle_error(e, use_json)

    async def run():
        async with ProxmoxApi.from_token(conn.url, conn.token) as api:
            provisioner = Provisioner(api)
            await provisioner.detect_cluster()

            nodes = list(configs)
            if node_filter:
                nodes = [n for n in nodes if n.hypervisor and n.hypervisor.node == node_filter]

            if not nodes:
                raise CliError(f"No nodes match filter '{node_filter}'")

            # Apply --start/--no-start override before batching
            for node in nodes:
                if start is not None and node.hypervisor:
                    node.hypervisor.start_on_creation = start

            # Per-node step tracking for JSON output
            node_steps: dict[str, list[dict[str, object]]] = {}

            def batch_callback(fluxnode: ArcaneOsConfig, ok: bool, msg: str):
                hostname = fluxnode.system.hostname
                if hostname not in node_steps:
                    node_steps[hostname] = []
                    if not use_json:
                        console.print(f"\n[bold]{hostname}[/bold]")
                node_steps[hostname].append({"ok": ok, "message": msg})
                if not use_json:
                    mark = _CHECK_MARK if ok else _CROSS_MARK
                    console.print(f"  {mark} {msg}")

            batch = BatchProvisioner(provisioner, provisioner.cluster)
            batch_results = await batch.provision_batch(nodes, callback=batch_callback)

            results = []
            all_ok = True
            for br in batch_results:
                hostname = br.fluxnode.system.hostname
                results.append({"hostname": hostname, "ok": br.ok, "steps": node_steps.get(hostname, [])})

                if not use_json:
                    if br.ok:
                        console.print(f"[bold green]{hostname}: Provisioned successfully[/bold green]")
                    else:
                        console.print(f"[bold red]{hostname}: Provisioning failed[/bold red]")

                if not br.ok:
                    all_ok = False

            if use_json:
                print(_json_ok({"nodes": results}) if all_ok else _json_error("Provisioning failed", {"nodes": results}))

            return all_ok

    try:
        success = asyncio.run(run())
    except CliError as e:
        _handle_error(e, use_json)

    if not success:
        raise typer.Exit(1)


@app.command()
def deprovision(
    url: Optional[str] = typer.Option(None, help="Proxmox API URL (also accepts ARCANE_MAGE_URL)"),
    token: Optional[str] = typer.Option(None, help="API token (also accepts stdin or ARCANE_MAGE_TOKEN)"),
    hypervisor: Optional[str] = typer.Option(None, "--hypervisor", "-H", help="Use stored hypervisor by name"),
    config: Optional[str] = typer.Option(None, "-c", "--config", help="The config file"),
    vm_name: Optional[str] = typer.Option(None, "--vm-name", help="Target a specific VM by name"),
    vm_id: Optional[int] = typer.Option(None, "--vm-id", help="Target a specific VM by ID"),
    node_filter: Optional[str] = typer.Option(None, "--node", help="Cluster node to target (e.g. pve1)"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    use_json: bool = typer.Option(False, "--json", help="Output JSON instead of text"),
) -> None:
    """Stop and delete VMs. Either pass a config file or target a single VM by name/ID."""
    from .log import configure_cli_logging
    from .provisioner import Provisioner

    try:
        if vm_name and vm_id:
            raise CliError("Cannot specify both --vm-name and --vm-id")

        direct_vm = vm_name is not None or vm_id is not None

        if use_json and not force:
            raise CliError("--json requires --force for destructive operations")

        if not direct_vm and not config:
            config = "fluxnodes.yaml"

        if direct_vm and config:
            raise CliError("Cannot specify --config with --vm-name or --vm-id")

        if not use_json:
            configure_cli_logging()

        conn = _resolve_connection(url, token, hypervisor)
    except CliError as e:
        _handle_error(e, use_json)

    if direct_vm:
        async def run_direct():
            async with ProxmoxApi.from_token(conn.url, conn.token) as api:
                provisioner = Provisioner(api)

                res = await api.get_hypervisor_nodes()
                if not res or not isinstance(res.payload, list):
                    raise CliError("Unable to discover hypervisor nodes")

                hv_nodes = [n.get("node") for n in res.payload if n.get("node")]
                if node_filter:
                    hv_nodes = [n for n in hv_nodes if n == node_filter]

                found_vm: dict | None = None
                found_node: str | None = None
                for hv_node in hv_nodes:
                    vms_res = await api.get_vms(hv_node)
                    if not vms_res:
                        continue
                    for v in vms_res.payload:
                        if vm_id is not None and v.get("vmid") == vm_id:
                            found_vm = v
                            found_node = hv_node
                            break
                        if vm_name is not None and v.get("name") == vm_name:
                            found_vm = v
                            found_node = hv_node
                            break
                    if found_vm:
                        break

                if not found_vm or not found_node:
                    target = vm_name or f"id={vm_id}"
                    raise CliError(f"VM '{target}' not found")

                vm_display = found_vm.get("name", str(found_vm["vmid"]))
                vm_status = found_vm.get("status", "unknown")

                if not force and not use_json:
                    console.print(f"[bold red]This will stop and delete:[/bold red]")
                    console.print(f"  VM:     {vm_display} (id={found_vm['vmid']})")
                    console.print(f"  Node:   {found_node}")
                    console.print(f"  Status: {vm_status}")
                    if not typer.confirm("\nContinue?"):
                        return True

                steps: list[dict[str, object]] = []

                def callback(ok: bool, msg: str):
                    steps.append({"ok": ok, "message": msg})
                    if not use_json:
                        mark = _CHECK_MARK if ok else _CROSS_MARK
                        console.print(f"  {mark} {msg}")

                if not use_json:
                    console.print(f"\n[bold]{vm_display}[/bold]")

                result = await provisioner._stop_and_delete_vm(
                    found_vm["vmid"], vm_display, vm_status, found_node, callback,
                )

                vm_info = {"vm_name": vm_display, "vm_id": found_vm["vmid"], "node": found_node, "steps": steps}

                if use_json:
                    print(_json_ok(vm_info) if result else _json_error("Deprovisioning failed", vm_info))
                else:
                    if result:
                        console.print(f"  [bold green]Deprovisioned successfully[/bold green]")
                    else:
                        console.print(f"  [bold red]Deprovisioning failed[/bold red]")

                return result

        try:
            success = asyncio.run(run_direct())
        except CliError as e:
            _handle_error(e, use_json)

        if not success:
            raise typer.Exit(1)
        return

    try:
        configs = _load_configs(config)
    except CliError as e:
        _handle_error(e, use_json)

    nodes = list(configs)
    if node_filter:
        nodes = [n for n in nodes if n.hypervisor and n.hypervisor.node == node_filter]

    if not nodes:
        _handle_error(CliError(f"No nodes match filter '{node_filter}'"), use_json)

    if not force and not use_json:
        console.print(f"[bold red]This will stop and delete the following VMs:[/bold red]")
        for node in nodes:
            hyper_node = node.hypervisor.node if node.hypervisor else "?"
            vm = node.hypervisor.vm_name if node.hypervisor else node.system.hostname
            console.print(f"  - {vm} on {hyper_node}")
        if not typer.confirm("Continue?"):
            raise typer.Exit(0)

    async def run():
        async with ProxmoxApi.from_token(conn.url, conn.token) as api:
            provisioner = Provisioner(api)

            results = []
            all_ok = True
            for node in nodes:
                hostname = node.system.hostname
                steps: list[dict[str, object]] = []

                def callback(ok: bool, msg: str):
                    steps.append({"ok": ok, "message": msg})
                    if not use_json:
                        mark = _CHECK_MARK if ok else _CROSS_MARK
                        console.print(f"  {mark} {msg}")

                if not use_json:
                    console.print(f"\n[bold]{hostname}[/bold]")

                result = await provisioner.deprovision_node(node, callback=callback)
                results.append({"hostname": hostname, "ok": result, "steps": steps})

                if not use_json:
                    if result:
                        console.print(f"  [bold green]Deprovisioned successfully[/bold green]")
                    else:
                        console.print(f"  [bold red]Deprovisioning failed[/bold red]")

                if not result:
                    all_ok = False

            if use_json:
                print(_json_ok({"nodes": results}) if all_ok else _json_error("Deprovisioning failed", {"nodes": results}))

            return all_ok

    try:
        success = asyncio.run(run())
    except CliError as e:
        _handle_error(e, use_json)

    if not success:
        raise typer.Exit(1)


@app.command()
def validate(
    url: Optional[str] = typer.Option(None, help="Proxmox API URL (also accepts ARCANE_MAGE_URL)"),
    token: Optional[str] = typer.Option(None, help="API token (also accepts stdin or ARCANE_MAGE_TOKEN)"),
    hypervisor: Optional[str] = typer.Option(None, "--hypervisor", "-H", help="Use stored hypervisor by name"),
    config: str = typer.Option("fluxnodes.yaml", "-c", "--config", help="The config file"),
    node_filter: Optional[str] = typer.Option(None, "--node", help="Cluster node to target (e.g. pve1)"),
    use_json: bool = typer.Option(False, "--json", help="Output JSON instead of text"),
) -> None:
    """Check config, API connectivity, storage, ISO, and network without provisioning."""
    from .log import configure_cli_logging
    from .provisioner import Provisioner

    if not use_json:
        configure_cli_logging()

    try:
        conn = _resolve_connection(url, token, hypervisor)
        configs = _load_configs(config)
    except CliError as e:
        _handle_error(e, use_json)

    async def run():
        async with ProxmoxApi.from_token(conn.url, conn.token) as api:
            provisioner = Provisioner(api)

            nodes = list(configs)
            if node_filter:
                nodes = [n for n in nodes if n.hypervisor and n.hypervisor.node == node_filter]

            if not nodes:
                raise CliError(f"No nodes match filter '{node_filter}'")

            results = []
            all_ok = True
            for node in nodes:
                hostname = node.system.hostname
                hyper = node.hypervisor

                if not hyper:
                    results.append({"hostname": hostname, "ok": False, "checks": [{"check": "hypervisor", "ok": False, "message": "No hypervisor config"}]})
                    if not use_json:
                        console.print(Panel(f"[red]No hypervisor config[/red]", title=f"[bold]{hostname}[/bold]", border_style="red"))
                    all_ok = False
                    continue

                checks = []

                api_ok, api_msg = await provisioner.validate_api_version(hyper.node)
                checks.append({"check": "api_version", "ok": api_ok, "message": api_msg if not api_ok else f"v{api_msg}"})

                storage_ok, storage_msg = await provisioner.validate_storage(
                    hyper.node, hyper.storage_iso, hyper.storage_images, hyper.storage_import,
                )
                checks.append({"check": "storage", "ok": storage_ok, "message": storage_msg if storage_msg else f"iso={hyper.storage_iso}, images={hyper.storage_images}, import={hyper.storage_import}"})

                iso_ok = await provisioner.validate_iso_version(hyper.node, hyper.iso_name, hyper.storage_iso)
                checks.append({"check": "iso", "ok": iso_ok, "message": f"{hyper.iso_name} not found" if not iso_ok else hyper.iso_name})

                net_ok = await provisioner.validate_network(hyper.node, hyper.network)
                checks.append({"check": "network", "ok": net_ok, "message": f"Bridge {hyper.network} not found" if not net_ok else hyper.network})

                node_ok = all(c["ok"] for c in checks)
                results.append({"hostname": hostname, "node": hyper.node, "ok": node_ok, "checks": checks})
                if not node_ok:
                    all_ok = False

                if not use_json:
                    table = Table(show_header=False, show_edge=False, pad_edge=False, box=None)
                    table.add_column("status", width=3)
                    table.add_column("check")
                    table.add_column("detail")

                    for c in checks:
                        mark = _CHECK_MARK if c["ok"] else _CROSS_MARK
                        detail = f"[dim]{c['message']}[/dim]" if c["ok"] else f"[red]{c['message']}[/red]"
                        table.add_row(mark, c["check"].replace("_", " ").title(), detail)

                    border = "green" if node_ok else "red"
                    console.print(Panel(table, title=f"[bold]{hostname}[/bold] @ [bold]{hyper.node}[/bold]", border_style=border))

            if use_json:
                print(_json_ok({"nodes": results}) if all_ok else _json_error("Validation failed", {"nodes": results}))

            return all_ok

    try:
        success = asyncio.run(run())
    except CliError as e:
        _handle_error(e, use_json)

    if not success:
        raise typer.Exit(1)


@app.command()
def ping(
    url: Optional[str] = typer.Option(None, help="Proxmox API URL (also accepts ARCANE_MAGE_URL)"),
    token: Optional[str] = typer.Option(None, help="API token (also accepts stdin or ARCANE_MAGE_TOKEN)"),
    hypervisor: Optional[str] = typer.Option(None, "--hypervisor", "-H", help="Use stored hypervisor by name"),
    use_json: bool = typer.Option(False, "--json", help="Output JSON instead of text"),
) -> None:
    """Test connectivity and authentication to a Proxmox hypervisor."""
    from .provisioner import is_api_min_version

    try:
        conn = _resolve_connection(url, token, hypervisor)
    except CliError as e:
        _handle_error(e, use_json)

    async def run():
        async with ProxmoxApi.from_token(conn.url, conn.token) as api:
            res = await api.get_hypervisor_nodes()

            if not res:
                if res and res.unauthorized:
                    raise CliError("Authentication failed")
                raise CliError(f"Unable to connect to {conn.url}")

            nodes = [n.get("node", "unknown") for n in res.payload] if isinstance(res.payload, list) else []

            version = None
            if nodes:
                ver_res = await api.get_api_version(nodes[0])
                if ver_res and ver_res.payload:
                    version = ver_res.payload.get("version")

            data = {
                "url": conn.url,
                "auth_type": "token",
                "api_version": version,
                "nodes": nodes,
            }

            if use_json:
                print(_json_ok(data))
            else:
                table = Table(show_header=False, show_edge=False, pad_edge=False, box=None)
                table.add_column("status", width=3)
                table.add_column("check")
                table.add_column("detail")

                table.add_row(_CHECK_MARK, "Connected", f"[dim]{conn.url}[/dim]")
                table.add_row(_CHECK_MARK, "Authenticated", f"[dim]{conn.token.user}[/dim]")
                if version:
                    mark = _CHECK_MARK if is_api_min_version(version) else _CROSS_MARK
                    table.add_row(mark, "Api Version", f"[dim]v{version}[/dim]")
                table.add_row(_CHECK_MARK, "Nodes", f"[dim]{', '.join(nodes)}[/dim]")

                console.print(Panel(table, title=f"[bold]{', '.join(nodes)}[/bold]", border_style="green"))

            return True

    try:
        success = asyncio.run(run())
    except CliError as e:
        _handle_error(e, use_json)

    if not success:
        raise typer.Exit(1)


@app.command()
def status(
    url: Optional[str] = typer.Option(None, help="Proxmox API URL (also accepts ARCANE_MAGE_URL)"),
    token: Optional[str] = typer.Option(None, help="API token (also accepts stdin or ARCANE_MAGE_TOKEN)"),
    hypervisor: Optional[str] = typer.Option(None, "--hypervisor", "-H", help="Use stored hypervisor by name"),
    config: str = typer.Option("fluxnodes.yaml", "-c", "--config", help="The config file"),
    node_filter: Optional[str] = typer.Option(None, "--node", help="Cluster node to target (e.g. pve1)"),
    use_json: bool = typer.Option(False, "--json", help="Output JSON instead of text"),
) -> None:
    """Show which configured nodes are already provisioned on the hypervisor."""
    from .log import configure_cli_logging
    from .provisioner import Provisioner

    if not use_json:
        configure_cli_logging()

    try:
        conn = _resolve_connection(url, token, hypervisor)
        configs = _load_configs(config)
    except CliError as e:
        _handle_error(e, use_json)

    async def run():
        async with ProxmoxApi.from_token(conn.url, conn.token) as api:
            provisioner = Provisioner(api)

            discovery = await provisioner.discover_nodes(configs)
            if not discovery:
                raise CliError("Failed to discover hypervisor nodes")

            filtered = list(configs)
            if node_filter:
                filtered = [n for n in filtered if n.hypervisor and n.hypervisor.node == node_filter]

            if not filtered:
                raise CliError(f"No nodes match filter '{node_filter}'")

            results = []
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Hostname")
            table.add_column("VM Name")
            table.add_column("Node")
            table.add_column("Status")

            for node in filtered:
                hostname = node.system.hostname
                hyper = node.hypervisor

                if not hyper:
                    results.append({"hostname": hostname, "vm_name": None, "node": None, "provisioned": False})
                    table.add_row(hostname, "-", "-", "[dim]no hypervisor config[/dim]")
                    continue

                provisioned = False
                vms = discovery.provisioned_vms.get(hyper.node) or []
                for vm in vms:
                    if vm.get("name") == hyper.vm_name:
                        provisioned = True
                        break

                results.append({
                    "hostname": hostname,
                    "vm_name": hyper.vm_name,
                    "node": hyper.node,
                    "provisioned": provisioned,
                })

                status_text = "[green]provisioned[/green]" if provisioned else "[yellow]not provisioned[/yellow]"
                table.add_row(hostname, hyper.vm_name, hyper.node, status_text)

            if use_json:
                print(_json_ok({"nodes": results}))
            else:
                console.print(table)

            return True

    try:
        asyncio.run(run())
    except CliError as e:
        _handle_error(e, use_json)


@app.command(hidden=True)
def provision_multicast() -> None:
    console.print("Not Implemented")


@app.command(hidden=True)
def provision_usb() -> None:
    console.print("Not Implemented")


if __name__ == "__main__":
    app()
