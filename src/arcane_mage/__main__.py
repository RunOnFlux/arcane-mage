import click

from .arcane_mage import ArcaneMage


@click.group(invoke_without_command=True)
@click.option(
    "-c", "--config", default="fluxnodes.yaml", help="The config file"
)
@click.pass_context
def cli(ctx: click.Context, config: str):
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
