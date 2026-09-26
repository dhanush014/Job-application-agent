"""jobagent command line."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import typer

from .config import load_config

app = typer.Typer(add_completion=False, help="Auto-apply agent for Greenhouse + Ashby (runs on Groq's free tier).")
CONFIG = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml")

EXAMPLES = Path(__file__).resolve().parents[2]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _pipeline(config: str):
    from .llm import GroqLLM
    from .pipeline import Pipeline
    from .store import open_store

    cfg = load_config(config)
    try:
        return Pipeline(cfg, open_store(cfg), GroqLLM(cfg.llm))
    except (RuntimeError, KeyError, FileNotFoundError) as e:
        typer.secho(f"setup error: {e}", fg="red")
        raise typer.Exit(1)


@app.command()
def init(dest: Path = typer.Argument(Path("."), help="Where to create config + profile files")):
    """Create config.yaml, companies.yaml, .env and profile/ from the examples."""
    pairs = [
        ("config.example.yaml", "config.yaml"),
        ("companies.example.yaml", "companies.yaml"),
        (".env.example", ".env"),
        ("profile.example", "profile"),
    ]
    for src, dst in pairs:
        s, d = EXAMPLES / src, dest / dst
        if d.exists():
            typer.echo(f"exists, skipped: {d}")
        elif s.is_dir():
            shutil.copytree(s, d)
            typer.echo(f"created {d}/")
        elif s.exists():
            shutil.copy(s, d)
            typer.echo(f"created {d}")
    typer.echo("Next: edit profile/master_resume.yaml, profile/answers.yaml, companies.yaml, .env; then `jobagent check-resume`.")


@app.command("check-resume")
def check_resume(config: str = CONFIG):
    """Validate your master resume + answer bank and render a one-page preview."""
    from .answers import Bank
    from .resume.render import fit, save_pdf

    cfg = load_config(config)
    master = cfg.master_resume()
    Bank.from_yaml(cfg.answer_bank())
    res = fit(master, [b.id for b in master.iter_bullets()])
    out = save_pdf(res.render, cfg.data_path / "master_preview.pdf")
    typer.echo(f"pages={res.render.pages} fill={res.render.fill:.0%} bullets={len(res.included)} dropped={len(res.dropped)}")
    for w in res.warnings:
        typer.secho(f"  ! {w}", fg="yellow")
    typer.echo(f"preview: {out}")


@app.command()
def discover(config: str = CONFIG, verbose: bool = False):
    """Fetch jobs from every company in companies.yaml."""
    _setup_logging(verbose)
    p = _pipeline(config)
    typer.echo(f"new jobs: {p.discover()}")


@app.command()
def run(config: str = CONFIG, submit: bool = typer.Option(True, help="Submit READY applications"), verbose: bool = False):
    """Discover, score, tailor, answer and (auto-)submit. Safe to run from cron."""
    _setup_logging(verbose)
    p = _pipeline(config)
    stats = p.run(submit=submit)
    typer.echo(
        f"new={stats.discovered_new} filtered={stats.filtered} low_fit={stats.low_fit} ready={stats.ready} "
        f"needs_review={stats.needs_review} failed={stats.failed} submitted={stats.submitted} dry_run={stats.dry_run}"
    )
    for n in dict.fromkeys(stats.notes):
        typer.secho(f"  ! {n}", fg="yellow")
    if getattr(p.llm, "tokens_used", None):
        typer.echo(f"groq tokens used: {p.llm.tokens_used}")


@app.command()
def loop(config: str = CONFIG, every_minutes: int = 120, verbose: bool = False):
    """Run forever, every N minutes (a free alternative to cron)."""
    while True:
        try:
            run(config=config, submit=True, verbose=verbose)
        except Exception as e:  # keep looping
            typer.secho(f"run failed: {e}", fg="red")
        time.sleep(every_minutes * 60)


@app.command()
def serve(config: str = CONFIG, host: str = "127.0.0.1", port: int = 8000, verbose: bool = False):
    """Open the review dashboard (one-click apply)."""
    import uvicorn

    from .web.app import create_app

    _setup_logging(verbose)
    typer.echo(f"dashboard: http://{host}:{port}")
    uvicorn.run(create_app(_pipeline(config)), host=host, port=port, log_level="warning")


@app.command()
def export(config: str = CONFIG, path: Path = typer.Argument(None)):
    """Write every application (resume, JD, answers, dates) to an Excel file."""
    from .store import open_store
    from .store.export import export_xlsx

    cfg = load_config(config)
    out = path or cfg.path(cfg.storage.excel_path or "data/applications.xlsx")
    typer.echo(f"wrote {export_xlsx(open_store(cfg).list(), Path(out))}")


if __name__ == "__main__":
    app()
