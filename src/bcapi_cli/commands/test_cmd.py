"""bcapi test — connection and endpoint testing."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from bcapi.client._async import AsyncBCClient
from bcapi.odata._query import Query
from bcapi_cli._state import state
from bcapi_cli.output import print_context_banner

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("connection")
def test_connection() -> None:
    """Test auth and API reachability."""
    print_context_banner()

    profile = state.profile
    console.print(f"[dim]Testing connection to {profile.environment}...[/dim]")

    try:
        ok = asyncio.run(_test_connection())
        if ok:
            console.print("[green]✓[/green] Connection successful")
            console.print(f"  Tenant: {profile.tenant_id}")
            console.print(f"  Environment: {profile.environment}")
            console.print(f"  Company: {profile.company_name or profile.company_id or '(not set)'}")
        else:
            console.print("[red]✗[/red] Connection test failed — check credentials and environment name.")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        raise typer.Exit(1)


@app.command("auth")
def test_auth() -> None:
    """Test authentication only."""
    from bcapi.auth._credentials import ClientCredentialsAuth

    profile = state.profile
    console.print(f"[dim]Testing auth for {profile.client_id}...[/dim]")

    try:
        auth = ClientCredentialsAuth(
            tenant_id=profile.tenant_id,
            client_id=profile.client_id or "",
            client_secret_env=profile.client_secret_env,
        )
        token = asyncio.run(auth.get_access_token())
        console.print(f"[green]✓[/green] Auth successful (token length: {len(token)})")
    except Exception as e:
        console.print(f"[red]✗ Auth failed:[/red] {e}")
        raise typer.Exit(1)


@app.command("endpoint")
def test_endpoint(
    name: str = typer.Argument(help="Entity set name to test"),
) -> None:
    """Test a specific endpoint (GET $top=1)."""
    print_context_banner()

    ep = state.registry.get(name)
    if ep:
        console.print(f"[dim]Testing {name} ({ep.route_display})...[/dim]")
    else:
        console.print(f"[dim]Testing {name} (standard v2.0 assumed)...[/dim]")

    try:
        records = asyncio.run(_test_endpoint(name))
        console.print(f"[green]✓[/green] {name}: returned {len(records)} record(s)")
        if records:
            fields = [k for k in records[0].keys() if not k.startswith("@odata")]
            console.print(f"[dim]  Fields: {', '.join(fields[:10])}{' ...' if len(fields) > 10 else ''}[/dim]")
    except Exception as e:
        console.print(f"[red]✗ {name}:[/red] {e}")
        raise typer.Exit(1)


async def _test_connection() -> bool:
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        return await client.test_connection()


async def _test_endpoint(name: str) -> list[dict]:
    async with AsyncBCClient(profile=state.profile_name, config=state.config) as client:
        query = Query().top(1)
        response = await client.get(name, query=query)
        return response.value
