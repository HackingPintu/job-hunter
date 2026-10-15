<div align="center">

# 🎯 JobHunter

**Stop searching. Start applying.**

Upload your CV, set your experience and expectations, and get a ranked list of **live openings pulled straight from 300+ company career sites**. No sign-up, no ads, no tracking. It runs on your machine.

[![CI](https://github.com/HackingPintu/job-hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/HackingPintu/job-hunter/actions)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)

</div>

---

## Why

Job boards are noisy, stale and full of re-posts. Company career pages are the source of truth, but nobody has time to check 300 of them every day. JobHunter checks them for you, throws away what does not fit your seniority, skills and city, and shows what is left ranked by fit.

## Features

- **CV-aware**: drop a PDF, DOCX or TXT and it detects your skills, experience and likely role. The file is parsed in memory and never stored.
- **Your inputs decide the results**: years of experience, skills (top 3 weigh most), roles, country, cities, expected CTC, company tiers, freshness, must-have and avoid skills.
- **Seniority-aware**: a 2-year profile does not see "Staff Engineer" or "Principal Architect", and a 9-year profile does not see "SDE-1".
- **Transparent scoring** (0-100) with a "Why" line on each job (skills matched, title match, location, experience required).
- **300+ companies in 4 tiers**: Dream, Stretch, Target, Safe. About 108 are read through their public career-site APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Amazon, Atlassian), and 200+ more get one-click search links.
- **10 countries**: India, United States, United Kingdom, Germany, Netherlands, Canada, Singapore, UAE, Australia, Ireland.
- **Application tracker**: star and mark-as-applied (stored in your browser), CSV export, dark mode.
- **Automation**: a CLI with a daily scheduler (Windows Task Scheduler or cron) and optional Telegram or email alerts for new matches.
- **Private by design**: runs locally, talks only to public company career endpoints, stores nothing on a server.

## Quick start

### Option 1: Python

```bash
git clone https://github.com/HackingPintu/job-hunter.git
cd job-hunter
pip install -e .
python -m jobhunter web
```

Your browser opens at <http://127.0.0.1:8000>. Upload your CV or fill in the form, click **Find my jobs**, and wait about a minute or two while it scans the career sites.

### Option 2: Docker

```bash
docker build -t jobhunter .
docker run --rm -p 8000:8000 jobhunter
```

## What you can pass in

| Input | Where | Effect |
|---|---|---|
| **CV** (PDF / DOCX / TXT) | UI upload, or `--cv resume.pdf` | Auto-detects skills, experience and role family |
| **Years of experience** | `yoe` / `--yoe` | Filters out titles too senior or too junior, and jobs that ask for much more experience than you have (yoe + 2 by default) |
| **Skills** | `skills` / `--skills "java,spring boot"` | Matched in the job description. The first 3 weigh most |
| **Roles** | `roles` / `--roles backend,devops` | backend, fullstack, frontend, mobile, data, ml, devops, qa, security, embedded |
| **Country and cities** | `country`, `cities` | Strict (only these cities) or preferred (ranked higher) |
| **Expected CTC** | `expected_lpa` / `--lpa 20` | India only. Hides company tiers that rarely pay that much (indicative) |
| **Company tiers** | `tiers` / `--tiers A,B,C` | A dream, B stretch, C target, D safe / high volume |
| **Must have / Avoid** | `must_have`, `avoid` | Hard requirement and soft penalty on skills |
| **Freshness / min score** | `freshness_days`, `min_score` | How old a posting can be, and how good a match must be to show |

See [`profile.example.yaml`](profile.example.yaml) for every option.

## Command line

```bash
# From a profile file
cp profile.example.yaml profile.yaml        # edit it
python -m jobhunter run --profile profile.yaml --open

# Or entirely from flags
python -m jobhunter run --cv resume.pdf --yoe 2 --country India \
    --cities Bengaluru,Pune --roles backend --skills "java,spring boot,python" --lpa 20

# Quick test on a few companies
python -m jobhunter run --yoe 2 --skills java --only Amazon,Atlassian,Mastercard
```

Output: `output/report.html` (same UI as the web app, as a single file) and `output/jobs.csv`. The CLI remembers what it has seen, so the next run flags **NEW** postings.

### Schedule it

Windows (daily at 9 AM and 6 PM):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_schedule.ps1
```

Linux / macOS: see [`scripts/cron.txt`](scripts/cron.txt).

### Alerts (optional)

Set environment variables and add `--notify`. Nothing is sent unless there is a new or apply-now match.

| Channel | Variables |
|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_EMAIL` |

## How scoring works

Each job gets 0-100 based on: title fit for your roles and seniority, weighted skill matches in the description, location (your cities rank higher), required years vs. yours, company tier, and freshness. Jobs that fail a hard rule (wrong seniority, wrong city in strict mode, too much experience required, missing a must-have skill) are dropped. Every card shows a **Why** line, so you can judge it.

The tier and pay bands are **rough, India-centric and indicative**. Always check the real posting.

## Add or fix companies

Companies live in [`jobhunter/data/companies.yaml`](jobhunter/data/companies.yaml). To add a company that uses Greenhouse, Lever, Ashby or SmartRecruiters:

```yaml
- {name: Acme, tier: B, type: greenhouse, token: acme}
```

To find new ones automatically, add them to `tools/candidates.txt` (`tier|Company|slug1,slug2`) and run:

```bash
python tools/discover.py
```

It probes each candidate and merges the ones with a live API. Companies without a public API go under `manual_boards`.

## Project layout

```
jobhunter/
  engine.py        fetchers, filtering, scoring, ranking
  profile.py       CV parsing and profile normalisation
  webapp.py        Flask API + local server
  cli.py           command line
  notify.py        Telegram / email alerts
  templates/       single-file UI (web app and static report)
  data/            companies, skills vocabulary, countries
tools/discover.py  find companies with a working career-site API
tests/             offline unit tests
```

## Limitations

- Only public career-site APIs are used. Companies that hide jobs behind logins or bot walls appear as search links instead.
- Skill detection is keyword-based, not semantic.
- Scanned (image-only) PDFs cannot be read. Use a text PDF or DOCX.
- Postings can disappear or change after we read them.

## Ethics

JobHunter helps you **find** jobs. It never auto-applies, scrapes behind logins, or bypasses rate limits. Please be kind to company servers: results are cached for 3 hours.

## Contributing

PRs are welcome: new companies, new countries (`regions.yaml`), new skills (`skills.yaml`), better scoring, translations. Run `pip install -e ".[dev]" && pytest` before opening a PR.

## License

[MIT](LICENSE)
