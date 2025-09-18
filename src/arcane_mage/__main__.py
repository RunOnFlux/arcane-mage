import click
from importlib.metadata import version as get_version

from .arcane_mage import ArcaneMage


@click.group(invoke_without_command=True)
@click.option(
    "-c", "--config", default="fluxnodes.yaml", help="The config file"
)
@click.option(
    "-v", "--version", is_flag=True, help="Show the application version"
)
@click.pass_context
def cli(ctx: click.Context, config: str, version: bool):
    if version:
        pkg_version = get_version("arcane-mage")
        click.echo(f"arcane-mage {pkg_version}")
        ctx.exit()
    if ctx.invoked_subcommand is None:
        app = ArcaneMage(fluxnode_config=config)
        app.run()


@cli.command()
def provision_proxmox():
    click.echo("Not Implemented")


@cli.command()
def provision_multicast():
    click.echo("Not Implemented")


@cli.command()
def provision_usb():
    click.echo("Not Implemented")


if __name__ == "__main__":
    cli()
