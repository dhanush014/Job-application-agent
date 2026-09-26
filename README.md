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
3. **Code fits the page, and fills it.** Every role keeps its `min_bullets`. The fitter then adds the highest-ranked bullets at 10pt, using binary search on the actual rendered PDF to find the most that still fit on one page. After that, it fills whatever space is left:
   - It enlarges the type, up to 11pt, but only if that doesn't make any bullet wrap onto a short dangling line.
   - It widens the gaps between bullets, entries and sections until the page is full.
   - It spreads any last sliver evenly between sections. It won't do this when a thin resume would end up with huge gaps; `check-resume` tells you to add bullets instead.

   If the required content doesn't fit at 10pt, the type can shrink to 9.5pt.
   The **job description's tools and technologies are bolded** wherever they appear in your bullets (up to three per bullet), and in the Skills lines, where they are also moved to the front. Bold is dropped from any bullet where it would cause a dangling line. Bullets the AI scores below `min_bullet_relevance` for the job are left out, so the page only shows your most relevant work.
4. **Locked template.** `resume/template.typ` uses fixed fonts, margins and spacing, and treats all content as plain strings, so no markup can be injected.
5. **Checks on the PDF itself:**
   - It has exactly one page, and the contact line and skill lines never wrap.
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
pip install -e ".[browser]"
playwright install chromium

jobagent init                          # creates config.yaml, companies.yaml, .env, profile/
# edit: profile/master_resume.yaml, profile/answers.yaml, companies.yaml, .env (GROQ_API_KEY)
jobagent check-resume                  # validate + preview your one-page master
jobagent run                           # dry run by default: fills forms, never clicks submit
jobagent serve                         # dashboard at http://127.0.0.1:8000
```

Groq's model line-up changes over time. If a run fails with a model error, run `jobagent check-llm`: it lists the models on your account and tests the two in `config.yaml`. Models that can't do strict JSON mode still work — the agent retries without it and extracts the JSON itself.

## Shortlist mode: no browser, you apply

The simplest way to use this, and the most reliable, because nothing has to drive a web form:

```bash
jobagent shortlist                     # 10 best matches from companies.yaml
jobagent shortlist <job-link> <job-link>   # or specific postings
```

It writes a folder (`data/shortlist/<date>/`) containing a **resume tailored to each posting** and an `index.html` listing every job with:
- the link to the posting, and its fit score and summary
- its tailored resume as **PDF and editable Word**, named `<you>_resume_<company>`
- the answers to copy into the form, with anything it can't answer flagged
- a **Yes, I applied** button; the page remembers what you've done and shows your progress

Open `index.html`, work down the list, apply. No Playwright, no CAPTCHAs, nothing submitted on your behalf. If you only ever use this command, you can install without the browser: `pip install -e .`

**Try it on two jobs first.** Pick two postings you like (Greenhouse or Ashby links) and run:
```bash
jobagent try https://job-boards.greenhouse.io/<company>/jobs/<id> https://jobs.ashbyhq.com/<company>/<id>
```
For each posting it:
- scores the fit
- builds the tailored resume and cover letter
- fills the application in a **visible** browser, then stops before submitting so you can inspect the form (close the window to continue)

It prints the file paths, and `jobagent serve` shows everything. When you're happy, submit those same two for real with `--live`. Use `jobagent run --limit 2` to trial the automatic pipeline on 2 discovered jobs.

When the dry runs look right, set `apply.dry_run: false`. From then on, `jobagent run` (from cron) or `jobagent worker` (runs continuously) applies to up to `daily_limit` jobs a day, 50 by default.

Example cron entry, every 2 hours from 8am to 8pm:
```
0 8-20/2 * * * cd /path/to/Job-application-agent && .venv/bin/jobagent run >> data/run.log 2>&1
```

## Cover letters

By default (`apply.cover_letter: when_asked`), whenever an application form has a cover letter field, required or optional, the agent writes a 180–260 word letter from your tailored bullets and the job description.
- For an upload field, it renders a one-page PDF with the same header as your resume.
- For a text box, it pastes the letter in.

Like everything else the LLM writes, the letter may only use facts from your profile. You can edit it in the dashboard, and the PDF is re-rendered when you save. It's stored with the application and appears in the Excel export.

## The record of every application

Every application is stored with its **job description, the exact resume content sent (JSON plus the uploaded PDF), every question and answer, the cover letter (text plus the uploaded PDF), a screenshot of the confirmation page, and the date applied**.
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

## Hosting the dashboard (Vercel + Supabase, both free)

The work is split into two parts because of what each one can run:

| Part | Runs on | Why |
|---|---|---|
| **Dashboard** (review, edit, Apply) | Vercel | Quick web requests, no browser needed |
| **Worker** (discover, tailor, fill forms, submit) | Your laptop or a free VM | Needs a real Chrome and minutes per run; Vercel functions time out and can't fit Chrome |
| **Database + PDFs** | Supabase | Shared by both |

When you click **Apply** on the hosted dashboard, the application is *queued* and the worker submits it within about a minute. If the worker hits a CAPTCHA, the application comes back to **Needs review**. You can then apply on the posting yourself, using the resume and cover letter PDFs linked on the page, and click **Mark as applied**.

**1. Supabase**
- Create a free project.
- Run `supabase/schema.sql` in the SQL editor.
- Create a **private** storage bucket named `applications`.

**2. Vercel**
- Choose **Add New → Project** and import this GitHub repo. Framework preset: *Other*. Vercel deploys the repo's default branch, so merge this branch first or set it as the production branch.
- Add these environment variables:

| Variable | Value |
|---|---|
| `DASHBOARD_PASSWORD` | A long password. Your browser asks for it; any username works |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase → Project Settings → API, using the `service_role` key |
| `JOBAGENT_CONFIG` | The full contents of your `config.yaml`, with `storage: {backend: supabase}` |
| `JOBAGENT_MASTER_RESUME` | The full contents of `profile/master_resume.yaml` |

- Deploy. If a variable is missing, the site tells you which one instead of failing silently.

**3. Worker**

Use the same `config.yaml` (with `storage.backend: supabase`) and a `.env` containing `GROQ_API_KEY`, `SUPABASE_URL` and `SUPABASE_KEY`, then run:
```bash
jobagent worker        # a full run every 2 hours + picks up dashboard clicks every minute
```
- **Your laptop** works whenever it's on, and a home IP triggers the fewest CAPTCHAs.
- To run around the clock for free, an **Oracle Cloud "Always Free" VM** (Ampere/ARM) has enough memory for Chrome. Run the worker there as a `systemd` service.
- GitHub Actions is *not* recommended as the worker. Its terms limit Actions to work on the repo's own software, and its datacenter IPs get a lot of CAPTCHAs.

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
