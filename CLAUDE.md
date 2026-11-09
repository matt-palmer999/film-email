# whatson.movie — Claude Code guidance

## Branch rules

**All work goes to `main`.** No long-lived feature branches.

The daily pipeline auto-commits listings to `main`. Any session that makes code changes
in a worktree must merge/push those changes back to `main` before the session ends.
Never let a branch diverge from `main` for more than a day or two.

Branches to avoid:
- `feat/city-pages` — fully merged 2026-11-09, do not use
- `dev` — dead since 2026-08-04, do not use

## Running the pipeline manually

Always load the `.env` file before running `pipeline.py`:

```powershell
Get-Content "C:\Users\TV-watchers\film-email\.env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
}
python pipeline.py
```

If running from a worktree, `cd` to the worktree directory first (or use an absolute path
to `pipeline.py` there). The pipeline commits to whichever branch the working directory
is on — make sure that is `main`, or push to `main` immediately after.

## Architecture

- **Language**: Python 3.12
- **Site**: Static HTML on GitHub Pages (`docs/`)
- **Pipeline**: `pipeline.py` — scrapes all cinemas, enriches via TMDB, writes `docs/`
- **Scrapers**: `scrapers/*.py` — one file per cinema/group
- **Email**: GitHub Actions weekly workflow (not Task Scheduler — that caused duplicates)
- **Auth/email verification**: Supabase Edge Function + Resend

## Key facts

- VPN on laptop causes WAF blocks (many cinemas return 0 at once + captcha/403 errors)
- TMDB lookup credentials come from `.env` — without it, all TMDB lookups silently fail
- ABC cinemas use the reservaentradas JSON API (not the old subdomain HTML scraper)
- Babel needs Playwright (Cloudflare WAF blocks plain requests)
