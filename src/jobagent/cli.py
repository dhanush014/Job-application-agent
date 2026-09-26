"""jobagent command line."""

from __future__ import annotations

import logging
import shutil
import sys
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
    res = fit(master, [b.id for b in master.iter_bullets()], style=cfg.resume_style())
    out = save_pdf(res.render, cfg.data_path / "master_preview.pdf")
    lay = res.doc.layout
    typer.echo(
        f"pages={res.render.pages} content={res.natural_fill:.0%} of page, "
        f"font={lay.family} {lay.point_size:.1f}pt, spacing={lay.spacing:.0%}, "
        f"bullets={len(res.included)} dropped={len(res.dropped)}"
    )
    for e in res.doc.entries():
        typer.echo(f"  {e.heading[:44]:46s} {len(e.bullets)} bullets")
    for w in res.warnings:
        typer.secho(f"  ! {w}", fg="yellow")
    typer.echo(f"preview: {out}")


@app.command("check-llm")
def check_llm(config: str = CONFIG):
    """Test the configured Groq models with a tiny JSON request and list available models."""
    from pydantic import BaseModel

    from .llm import GroqLLM

    class Ping(BaseModel):
        ok: bool
        word: str

    cfg = load_config(config)
    try:
        llm = GroqLLM(cfg.llm)
    except RuntimeError as e:
        typer.secho(f"setup error: {e}", fg="red")
        raise typer.Exit(1)
    try:
        ids = sorted(m.id for m in llm.client.models.list().data)
        typer.echo("models on your Groq account: " + ", ".join(ids))
    except Exception as e:
        typer.secho(f"could not list models: {e}", fg="yellow")
    failed = False
    for tier, model in (("fast", cfg.llm.fast_model), ("smart", cfg.llm.smart_model)):
        before = llm.tokens_used
        try:
            out = llm.json(tier, "Reply as instructed.", 'Return {"ok": true, "word": "hello"}.', Ping)
            typer.secho(f"  {tier} model {model}: OK ({out.word}, {llm.tokens_used - before} tokens)", fg="green")
        except Exception as e:
            failed = True
            typer.secho(f"  {tier} model {model}: FAILED - {type(e).__name__}: {str(e)[:300]}", fg="red")
        if limit := _tpm_limit(llm, model):
            typer.echo(f"      Groq allows {limit} tokens/min for this model", nl=False)
            if cfg.llm.tokens_per_minute:
                typer.echo(f" (config paces at {cfg.llm.tokens_per_minute})")
            else:
                typer.secho(f" — set llm.tokens_per_minute: {limit} in config.yaml to avoid 429s", fg="yellow")
    if failed:
        typer.echo("Pick working model ids from the list above for llm.fast_model / llm.smart_model in config.yaml.")
        raise typer.Exit(1)


def _tpm_limit(llm, model: str) -> int | None:
    """Groq reports the per-minute token budget in a response header."""
    try:
        raw = llm.client.chat.completions.with_raw_response.create(
            model=model, messages=[{"role": "user", "content": "hi"}], max_completion_tokens=1
        )
        return int(raw.headers.get("x-ratelimit-limit-tokens", 0)) or None
    except Exception:
        return None


@app.command()
def discover(config: str = CONFIG, verbose: bool = False):
    """Fetch jobs from every company in companies.yaml."""
    _setup_logging(verbose)
    p = _pipeline(config)
    typer.echo(f"new jobs: {p.discover()}")


@app.command()
def shortlist(
    urls: list[str] = typer.Argument(None, help="Specific job links (default: search companies.yaml)"),
    config: str = CONFIG,
    limit: int = typer.Option(10, help="How many jobs to tailor a resume for"),
    out: Path = typer.Option(None, help="Where to write the folder (default: data/shortlist/<date>)"),
    verbose: bool = False,
):
    """Build a folder of jobs to apply to by hand: link + tailored resume, no browser."""
    _setup_logging(verbose)
    p = _pipeline(config)
    index, apps = p.shortlist(urls=list(urls) if urls else None, limit=limit, out_dir=out)
    for i, a in enumerate(sorted(apps, key=lambda a: -(a.score or 0)), start=1):
        typer.echo(f"  {i:2d}. [{a.score if a.score is not None else '--':>3}] {a.title} @ {a.company_name}")
        typer.echo(f"      {a.apply_url}")
    if not apps:
        typer.secho("No jobs matched. Loosen preferences in config.yaml or pass job links directly.", fg="yellow")
        return
    typer.secho(f"\n{len(apps)} job(s) ready: {index}", fg="green", bold=True)
    typer.echo(f"Open it with:  open {index}" if sys.platform == "darwin" else f"Open {index} in your browser.")
    if getattr(p.llm, "tokens_used", None):
        typer.echo(f"groq tokens used: {p.llm.tokens_used}")


@app.command("try")
def try_jobs(
    urls: list[str] = typer.Argument(..., help="Greenhouse or Ashby job links"),
    config: str = CONFIG,
    live: bool = typer.Option(False, "--live", help="Actually submit (default: fill the form and stop)"),
    headless: bool = typer.Option(False, help="Hide the browser (default: show it so you can watch)"),
    verbose: bool = False,
):
    """Trial run on specific postings: tailor, write, fill, and (with --live) submit."""
    _setup_logging(verbose)
    p = _pipeline(config)
    for app_, filtered in p.try_urls(urls, live=live, headless=headless):
        typer.secho(f"\n{app_.title} @ {app_.company_name}", bold=True)
        typer.echo(f"  fit score: {app_.score}   status: {app_.status.value}")
        if filtered:
            typer.secho(f"  note: your filters would normally skip this job ({filtered})", fg="yellow")
        typer.echo(f"  resume:        {app_.resume_pdf}")
        if app_.cover_letter_pdf:
            typer.echo(f"  cover letter:  {app_.cover_letter_pdf}")
        if app_.screenshot:
            typer.echo(f"  screenshot:    {app_.screenshot}")
        answered = sum(1 for a in app_.answers if a.value not in (None, "", []))
        typer.echo(f"  form: {answered}/{len(app_.answers)} fields answered")
        for r in app_.review_reasons:
            typer.secho(f"  ! {r}", fg="yellow")
    typer.echo("\nOpen the dashboard (jobagent serve) to see each resume, answer and cover letter.")


@app.command()
def run(
    config: str = CONFIG,
    submit: bool = typer.Option(True, help="Submit READY applications"),
    limit: int = typer.Option(0, help="Only prepare/submit this many jobs (0 = no limit), e.g. 2 for a trial"),
    verbose: bool = False,
):
    """Discover, score, tailor, answer and (auto-)submit. Safe to run from cron."""
    _setup_logging(verbose)
    p = _pipeline(config)
    stats = p.run(submit=submit, limit=limit or None)
    typer.echo(
        f"new={stats.discovered_new} filtered={stats.filtered} low_fit={stats.low_fit} ready={stats.ready} "
        f"needs_review={stats.needs_review} failed={stats.failed} submitted={stats.submitted} dry_run={stats.dry_run}"
    )
    for n in dict.fromkeys(stats.notes):
        typer.secho(f"  ! {n}", fg="yellow")
    if getattr(p.llm, "tokens_used", None):
        typer.echo(f"groq tokens used: {p.llm.tokens_used}")


@app.command()
def worker(
    config: str = CONFIG,
    every_minutes: int = typer.Option(120, help="Full discover/prepare/submit run every N minutes"),
    poll_seconds: int = typer.Option(60, help="How often to pick up Apply clicks from the hosted dashboard"),
    verbose: bool = False,
):
    """Run forever: a full run every N minutes, plus hosted-dashboard clicks within a minute."""
    _setup_logging(verbose)
    p = _pipeline(config)
    last_run = 0.0
    while True:
        try:
            if time.time() - last_run >= every_minutes * 60:
                last_run = time.time()
                s = p.run()
                typer.echo(f"run: new={s.discovered_new} ready={s.ready} review={s.needs_review} submitted={s.submitted}")
            else:
                s = p.process_queue()
                if s.submitted or s.needs_review:
                    p.export()
                    typer.echo(f"queue: submitted={s.submitted} review={s.needs_review}")
        except Exception as e:  # keep the worker alive
            typer.secho(f"worker error: {e}", fg="red")
        time.sleep(poll_seconds)


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
