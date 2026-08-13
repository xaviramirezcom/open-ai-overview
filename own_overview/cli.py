"""``own-overview`` command line — seed, ingest, query.

A thin, friendly front door over the pipeline:

    own-overview seed                 generate synthetic CDA data + ingest it (local)
    own-overview seed --emit-events   ... instead fire lifecycle events at EventBridge
    own-overview ingest <s3_path> ... ingest one committed batch
    own-overview query "<question>" --role <role>   ask, with access control

The ``--role`` on ``query`` is the demo's punchline: change the role and the
retrievable set changes (an adjuster can't see the underwriting memo; an
underwriter can), because the permission filter is applied *in retrieval*, not
after generation.

Heavy imports (the graph, the vector store, boto3) are done inside the commands
so ``--help`` stays instant and a missing optional dep only bites the path that
needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import get_settings

if TYPE_CHECKING:
    from .contracts import Answer

app = typer.Typer(
    add_completion=False,
    help="Build your company's own AI Overview over Guidewire CDA data.",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


@app.command()
def seed(
    emit_events: bool = typer.Option(
        False,
        "--emit-events",
        help="Push CDA lifecycle events to (LocalStack) EventBridge instead of "
        "ingesting inline. Exercises the real event-driven path.",
    ),
    tenants: list[str] = typer.Option(
        None, "--tenants", "-t", help="Restrict to these tenant ids (repeatable)."
    ),
    envs: list[str] = typer.Option(
        None, "--envs", "-e", help="Restrict to these environments (repeatable)."
    ),
) -> None:
    """Generate the synthetic CDA dataset in true CDA layout, then ingest it.

    Local (default): writes Parquet + manifests under ``cda_local_root`` and
    loops the generated batches through the ingestion orchestrator, so a
    following ``query`` has data. With ``--emit-events`` it instead fires the
    matching lifecycle events at EventBridge for the Lambda to pick up.
    """
    from .ingestion.cda.simulator import EVENT_BUS_NAME, emit_to_eventbridge, generate
    from .ingestion.cda.source import LocalCdaSource
    from .ingestion.ingest import ingest_batch

    settings = get_settings()

    console.print(
        f"[bold]Generating synthetic CDA data[/bold] under "
        f"[cyan]{settings.cda_local_root}[/cyan] ..."
    )
    batches = generate(settings, tenants=tenants or None, envs=envs or None)
    console.print(
        f"  wrote [bold]{len(batches)}[/bold] table batches across "
        f"{len({b.tenant_id for b in batches})} tenant(s) / "
        f"{len({(b.tenant_id, b.env) for b in batches})} scope(s)."
    )

    if emit_events:
        console.print(
            f"[bold]Emitting[/bold] {len(batches)} lifecycle events to EventBridge "
            f"bus [cyan]{EVENT_BUS_NAME}[/cyan] "
            f"(endpoint: {settings.aws_endpoint_url or 'real AWS'}) ..."
        )
        try:
            accepted = emit_to_eventbridge(batches, settings)
        except Exception as exc:  # noqa: BLE001 - surface a friendly hint
            console.print(
                Panel(
                    f"{exc}\n\nIs LocalStack up and bootstrapped?\n"
                    "  docker compose up -d && ./scripts/bootstrap_localstack.sh",
                    title="[red]Could not emit events[/red]",
                    border_style="red",
                )
            )
            raise typer.Exit(1) from exc
        console.print(f"  [green]{accepted}[/green] event(s) accepted by EventBridge.")
        console.print("The ingestion Lambda will pick them up. Then run a query.")
        return

    # Local path: ingest inline. Build components once and reuse across batches.
    from .config import build_chunker, build_embedder, build_vector_store

    embedder = build_embedder(settings)
    store = build_vector_store(settings, embedder=embedder)
    chunker = build_chunker(settings)
    source = LocalCdaSource(settings.cda_local_root)

    table = Table(title="Ingestion", show_lines=False)
    for col in ("tenant", "env", "table", "upserts", "deletes", "chunks"):
        table.add_column(col)
    totals = {"upserts": 0, "deletes": 0, "chunks": 0}
    for b in batches:
        res = ingest_batch(
            b.event, settings, source=source, store=store, embedder=embedder, chunker=chunker
        )
        for k in totals:
            totals[k] += res.get(k, 0)
        table.add_row(
            res["tenant"],
            res["env"],
            res["table"],
            str(res.get("upserts", 0)),
            str(res.get("deletes", 0)),
            str(res.get("chunks", 0)),
        )
    console.print(table)
    console.print(
        f"[green]Done.[/green] {totals['upserts']} upserts, "
        f"{totals['deletes']} deletes, {totals['chunks']} chunks indexed.\n"
        'Try: own-overview query "Why did the premium on POL-55012 go up?" --role adjuster'
    )


# ---------------------------------------------------------------------------
# ingest (one batch)
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    s3_path: str = typer.Argument(
        ...,
        help="Path to the committed batch, relative to cda_local_root "
        "(e.g. acme/prod/policy/<fingerprint>/<timestamp>).",
    ),
    tenant: str = typer.Option(..., "--tenant", help="Tenant id."),
    env: str = typer.Option(..., "--env", help="Environment (dev/qa/prod)."),
    table: str = typer.Option(..., "--table", help="CDA table (policy/claim/underwriting/etc.)."),
    fingerprint: str = typer.Option(None, "--fingerprint", help="Schema fingerprint (optional)."),
) -> None:
    """Ingest a single committed CDA batch (the unit the Lambda handles)."""
    from .ingestion.cda.events import CdaEventType, CdaLifecycleEvent
    from .ingestion.ingest import ingest_batch

    settings = get_settings()
    event = CdaLifecycleEvent(
        type=CdaEventType.STREAMING_BATCH_COMPLETED,
        tenant_id=tenant,
        env=env,
        table=table,
        batch_id="manual",
        s3_path=s3_path,
        fingerprint=fingerprint,
    )
    res = ingest_batch(event, settings)
    if res.get("skipped"):
        console.print(f"[yellow]Skipped:[/yellow] {res.get('reason')}")
        return
    console.print(
        f"[green]Ingested[/green] {tenant}/{env}/{table}: "
        f"{res['upserts']} upserts, {res['deletes']} deletes, {res['chunks']} chunks."
    )


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command()
def query(
    question: str = typer.Argument(..., help="The natural-language question."),
    role: list[str] = typer.Option(
        ..., "--role", "-r", help="Caller role(s) (repeatable). Drives access control."
    ),
    tenant: str = typer.Option("acme", "--tenant", help="Tenant id (isolation scope)."),
    env: str = typer.Option("prod", "--env", help="Environment (isolation scope)."),
    user: str = typer.Option("u_demo", "--user", help="Caller user id (for the audit log)."),
) -> None:
    """Ask a question. The signed identity (tenant, env, roles) is built here and
    the permission filter is compiled into retrieval — so the answer reflects
    exactly what this caller is allowed to see.

    Run it twice with different ``--role`` to watch access control at retrieval:
    an adjuster can't retrieve the underwriting memo behind POL-55012's premium
    increase; an underwriter can.
    """
    from .pipeline.graph import build_query_graph
    from .security.identity import dev_identity

    settings = get_settings()
    identity = dev_identity(user_id=user, tenant_id=tenant, env=env, roles=list(role))

    console.print(
        Panel(
            f"[bold]{question}[/bold]\n"
            f"scope=[cyan]{tenant}/{env}[/cyan]  "
            f"roles=[magenta]{', '.join(role)}[/magenta]  user={user}",
            title="Query",
            border_style="cyan",
        )
    )

    graph = build_query_graph(settings=settings)
    state = graph.invoke({"question": question, "identity": identity})

    answer = state.get("answer")
    if answer is None:
        console.print("[red]No answer produced.[/red]")
        raise typer.Exit(1)

    _print_answer(answer)


def _print_answer(answer: Answer) -> None:
    """Pretty-print an :class:`~own_overview.contracts.Answer`."""
    abstained = getattr(answer, "abstained", False)
    grounded = getattr(answer, "groundedness", None)

    body = answer.text or "(no answer)"
    border = "yellow" if abstained else "green"
    title = "Abstained" if abstained else "Answer"
    console.print(Panel(body, title=title, border_style=border))

    citations = getattr(answer, "citations", None) or []
    if citations:
        table = Table(title="Citations", show_lines=False)
        table.add_column("#", justify="right")
        table.add_column("source_id")
        table.add_column("chunk_id")
        for c in citations:
            table.add_row(str(c.marker), str(c.source_id), str(c.chunk_id))
        console.print(table)
    else:
        console.print("[dim]No citations (nothing retrievable for this role/scope).[/dim]")

    bits = []
    if grounded is not None:
        bits.append(f"groundedness=[bold]{grounded:.2f}[/bold]")
    bits.append(f"abstained=[bold]{abstained}[/bold]")
    console.print("  ".join(bits))


if __name__ == "__main__":
    app()
