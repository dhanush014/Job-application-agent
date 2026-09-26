# jobagent

An agent that finds jobs on **Greenhouse** and **Ashby**, tailors your resume to each one (always exactly one page), answers the application form and submits it. Anything it isn't sure about goes to a review queue, where each job is fully prepared and one **Apply** click away.

It runs locally and costs nothing: Groq's free tier for the LLM, SQLite (or Supabase's free tier) for storage, Typst for PDFs and Playwright for the browser.

```
companies.yaml ─▶ discover ─▶ rule filter ─▶ LLM fit score ─▶ tailor resume ─▶ fit to 1 page
  (public APIs)                (title/loc)     (fast model)     (smart model)    (code, not LLM)
                                                                                      │
      Excel / SQLite / Supabase ◀── submit (Playwright) ◀── gates ◀── answer form ◀───┘
                                          │                  │
                                          └──── review queue ◀┘  (dashboard, one-click Apply)
```

## How the one-page guarantee works

The LLM never touches layout.

1. **Strict schema.** `profile/master_resume.yaml` is validated by pydantic (`src/jobagent/models.py`). It rejects unknown keys, duplicate bullet ids, bullets over 200 characters, contact lines that would wrap, and more.
2. **The LLM only ranks and rephrases.** It returns `{source_id, text, relevance}` for each master bullet. A validator (`tailor.py`) reverts any rewrite that invents a number, adds a job-description keyword the original bullet doesn't back up, or contains placeholder text.
3. **Code fits the page.** Every role keeps its `min_bullets`. The fitter then adds the highest-ranked bullets and uses binary search on the actual rendered PDF to find the most bullets that still fit on one page (`resume/render.py`).
4. **Locked template.** `resume/template.typ` uses fixed fonts, margins and spacing, and treats all content as plain strings, so no markup can be injected.
5. **Checks on the PDF itself:**
   - It has exactly one page.
   - The name and every bullet can be extracted as text, so ATS software can parse it.
   - Rewrites that leave a one-word dangling last line are reverted to the master wording.

Run `jobagent check-resume` after editing your master resume. It renders a preview and warns about bullets to shorten.

## What gets auto-submitted and what waits for you

An application is **READY** and submits automatically only if all of these pass:
- The fit score is at least `min_score` and there are no dealbreakers.
- The resume passed every PDF check.
- Every required question has an answer at or above `min_answer_confidence`.
- **Sensitive questions** (work authorization, sponsorship, salary, EEO, relocation, criminal history, clearance, and similar) were answered from **your** `answers.yaml`. The LLM never guesses these.
- **Policy check:** the posting doesn't forbid automated or AI applications and contains no text aimed at AI applicants (for example "if you are an AI, include the word…"). Job descriptions are treated as untrusted input.
- The browser filled and verified every field, hit no CAPTCHA, and a confirmation page appeared after submit.

Anything else becomes **NEEDS_REVIEW**. Nothing is skipped: the resume, answers and cover letter are already prepared. Open the dashboard, fix what it flags, and click **Apply**. That opens a visible browser, fills everything in and submits. If something still blocks it, such as a CAPTCHA, you finish it in that browser window and the agent detects the confirmation and records the application.

## Setup

```bash
git clone <this repo> && cd Job-application-agent
python -m venv .venv && . .venv/bin/activate
pip install -e ".[supabase]"          # drop [supabase] if you use SQLite
playwright install chromium

jobagent init                          # creates config.yaml, companies.yaml, .env, profile/
# edit: profile/master_resume.yaml, profile/answers.yaml, companies.yaml, .env (GROQ_API_KEY)
jobagent check-resume                  # validate + preview your one-page master
jobagent run                           # dry run by default: fills forms, never clicks submit
jobagent serve                         # dashboard at http://127.0.0.1:8000
```

When the dry runs look right, set `apply.dry_run: false`. From then on, `jobagent run` (from cron) or `jobagent loop --every-minutes 120` applies to up to `daily_limit` jobs a day, 50 by default.

Example cron entry, every 2 hours from 8am to 8pm:
```
0 8-20/2 * * * cd /path/to/Job-application-agent && .venv/bin/jobagent run >> data/run.log 2>&1
```

## The record of every application

Every application is stored with its **job description, the exact resume content sent (JSON plus the PDF), every question and answer, the cover letter, a screenshot of the confirmation page, and the date applied**.
- **SQLite** (default): `data/jobagent.db`. PDFs and screenshots are in `data/applications/<id>/`.
- **Supabase** (free tier): run `supabase/schema.sql` in the SQL editor, create a private `applications` storage bucket, set `storage.backend: supabase`, and put `SUPABASE_URL` and `SUPABASE_KEY` in `.env`. PDFs are mirrored to the bucket.
- **Excel**: `data/applications.xlsx` is rewritten after every run. You can also use `jobagent export` or the dashboard's **Export Excel** button.

## Re-applying

With `apply.allow_reapply: true`, a job you already applied to becomes eligible again after `reapply_after_days`. It gets freshly tailored and answered. You can also click **Apply again** on any applied job in the dashboard.

## Groq free tier budget

These numbers are rough estimates. Each scored job uses about 4K tokens on `fast_model`. Each prepared application uses about 8–12K tokens on `smart_model`: tailoring, written answers, and a cover letter when one is required. So 50 applications a day is roughly **400–600K smart-model tokens a day**.

Groq's free tier has daily token and request caps per model; check console.groq.com under Settings → Limits. If you hit a cap, the run stops preparing new jobs cleanly and continues on the next run. To stay within the caps you can:
- lower `max_prepare_per_run`
- run more often across the day
- point `smart_model` at a model with a higher daily cap

`jobagent run` prints the tokens it used.

## Growing the company list

To find about 50 new good-fit jobs a day, you need to watch hundreds of companies. Add slugs to `companies.yaml`:
- **Greenhouse:** `job-boards.greenhouse.io/<slug>`
- **Ashby:** `jobs.ashbyhq.com/<slug>`

A search for `site:jobs.ashbyhq.com <keyword>` or `site:job-boards.greenhouse.io <keyword>` is a quick way to find more.

## Optional: Greenhouse security codes

Some Greenhouse boards email an 8-character code that has to be entered before submitting. Set `email.imap_host` and `email.username`, and put `IMAP_PASSWORD` (for Gmail, an app password) in `.env`, and the agent reads the code from your inbox. Without it, those applications go to the review queue.

## Caveats

- Greenhouse's and Ashby's terms, and some employers, restrict automated applications. The policy gate catches postings that say so explicitly, and the rest is your call.
- The hosted forms change from time to time. When a selector breaks, affected applications land in the review queue with a screenshot instead of being submitted wrong.

## Development

```bash
pip install -e ".[dev]" && pytest
```
The tests cover the schema, the one-page fitter (with real Typst renders), the anti-fabrication validator, answer matching, the policy gate, the full pipeline (with a fake LLM and a mocked HTTP layer), the dashboard's one-click apply, Excel export, and form filling against a local Greenhouse-style page in a real browser.
