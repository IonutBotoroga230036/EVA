"""
E.V.A. CLI Interface
"""

import asyncio
from rich.console import Console
from rich.panel import Panel
from loguru import logger

from core.orchestrator import Eva

console = Console()


async def main():
    console.print(Panel(
        "[bold cyan]E.V.A.[/bold cyan] - Extensible Virtual Agent\n"
        "[dim]Type 'quit' to exit, 'status' for system status[/dim]",
        title="System Boot",
        border_style="cyan",
    ))

    try:
        eva = Eva()
        console.print("[green]All systems online.[/green]\n")
        console.print(f"[cyan]{eva.persona['greeting']}[/cyan]\n")
    except Exception as e:
        console.print(f"[red]Boot failed: {e}[/red]")
        return

    eva.pulse.start_listening()

    while True:
        try:
            user_input = console.input("[bold yellow]You:[/bold yellow] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "status":
            budget = eva.budget.today_summary()
            console.print(Panel(
                f"Budget: [green]{budget['spent']:.4f}[/green] / {budget['limit']} EUR "
                f"({budget['num_calls']} API calls)\n"
                f"Remaining: [green]{budget['remaining']:.4f}[/green] EUR\n"
                f"Session messages: {len(eva.session.messages)}\n"
                f"Persona: {eva.persona['name']}",
                title="System Status",
                border_style="cyan",
            ))
            continue

        with console.status("[cyan]Processing, sir...[/cyan]"):
            response = await eva.process(user_input)

        name = eva.persona["name"]
        console.print(f"\n[bold cyan]{name}:[/bold cyan] {response}\n")

    console.print("\n[dim]Systems shutting down. Goodbye, sir.[/dim]")
    eva.pulse.stop()


if __name__ == "__main__":
    asyncio.run(main())