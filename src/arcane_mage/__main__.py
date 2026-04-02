try:
    import click
except ModuleNotFoundError:
    raise SystemExit(
        "arcane-mage CLI requires the 'cli' extra.\n"
        "Install with: pip install arcane-mage[cli]  (or arcane-mage[tui] for the full UI)"
    ) from None

import asyncio
from importlib.metadata import version as get_version
from pathlib import Path


@click.group(invoke_without_command=True)
@click.option("-c", "--config", default="fluxnodes.yaml", help="The config file")
@click.option("-v", "--version", is_flag=True, help="Show the application version")
@click.pass_context
def cli(ctx: click.Context, config: str, version: bool) -> None:
    if version:
        pkg_version = get_version("arcane-mage")
        click.echo(f"arcane-mage {pkg_version}")
        ctx.exit()

    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    if ctx.invoked_subcommand is None:
        try:
            from .arcane_mage import ArcaneMage
        except ModuleNotFoundError:
            raise SystemExit(
                "arcane-mage TUI requires the 'tui' extra.\n"
                "Install with: pip install arcane-mage[tui]"
            ) from None

        app = ArcaneMage(fluxnode_config=config)
        app.run()


@cli.command()
@click.option("--url", required=True, help="Proxmox API URL (e.g. https://pve.local:8006)")
@click.option("--token", required=True, help="API token in user@pam!name=value format")
@click.option("--node-filter", default=None, help="Only provision nodes on this hypervisor node")
@click.option("--start/--no-start", default=None, help="Override start_on_creation setting")
@click.option("--delete-efi/--keep-efi", default=True, help="Delete EFI image after provisioning")
@click.pass_context
def provision_proxmox(
    ctx: click.Context,
    url: str,
    token: str,
    node_filter: str | None,
    start: bool | None,
    delete_efi: bool,
) -> None:
    """Provision Fluxnodes on a Proxmox hypervisor without the TUI."""
    from .log import configure_cli_logging
    from .models import ArcaneOsConfigGroup
    from .provisioner import Provisioner
    from .proxmox import ProxmoxApi

    configure_cli_logging()

    config_path = ctx.obj["config"]
    configs = ArcaneOsConfigGroup.from_fs(Path(config_path))

    if not configs:
        click.echo(f"No nodes found in {config_path}", err=True)
        raise SystemExit(1)

    parsed_token = ProxmoxApi.parse_token(token)
    if not parsed_token:
        click.echo("Invalid token format. Expected: user@pam!tokenname=tokenvalue", err=True)
        raise SystemExit(1)

    async def run():
        api = ProxmoxApi.from_token(url, *parsed_token)
        provisioner = Provisioner(api)

        nodes = list(configs)
        if node_filter:
            nodes = [n for n in nodes if n.hypervisor and n.hypervisor.node == node_filter]

        if not nodes:
            click.echo(f"No nodes match filter '{node_filter}'", err=True)
            return False

        all_ok = True
        for node in nodes:
            hostname = node.system.hostname

            if start is not None and node.hypervisor:
                node.hypervisor.start_on_creation = start

            def callback(ok: bool, msg: str):
                status = click.style("OK", fg="green") if ok else click.style("FAIL", fg="red")
                click.echo(f"  [{status}] {msg}")

            click.echo(f"Provisioning {hostname}...")
            result = await provisioner.provision_node(node, callback=callback, delete_efi=delete_efi)

            if result:
                click.echo(click.style(f"  {hostname}: provisioned successfully", fg="green"))
            else:
                click.echo(click.style(f"  {hostname}: provisioning failed", fg="red"))
                all_ok = False

        return all_ok

    success = asyncio.run(run())

    if not success:
        raise SystemExit(1)


@cli.command(hidden=True)
def provision_multicast() -> None:
    click.echo("Not Implemented")


@cli.command(hidden=True)
def provision_usb() -> None:
    click.echo("Not Implemented")


if __name__ == "__main__":
    cli()
