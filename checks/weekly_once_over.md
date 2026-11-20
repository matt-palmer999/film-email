# Weekly once-over

A scheduled Claude session runs this every Monday morning, after the daily
pipeline. It is a **report-only** check: change nothing in the code, data or
site, and commit nothing. Write the findings and email them.

The rule that matters most: **a dubbed session shown as VOSE is the worst
mistake the site can make.** When in doubt, report it.

## 0. Before starting

- Work in `C:\Users\TV-watchers\film-email` on `main`, and `git pull` first.
- Data to check: `docs/data/films_cache.json` (every film, cinema and session),
  `docs/data/scraper_counts.json`, `docs/data/vose_counts.json`,
  `docs/data/cinema_last_ok.json`, and the cinema list in `CINEMA_META` at the
  top of `pipeline.py`.
- If many cinemas return captcha or 403 at once, the laptop VPN is probably
  on. Say so in the report and skip the live checks, rather than reporting
  them as cinema errors.

## 1. VOSE spot check (the most important part)

Pick about **15 sessions** from today to the next 3 days:

- about 8 VOSE sessions, weighted towards multiplexes and towards any cinema
  whose VOSE count changed noticeably (`vose_counts.json` vs this week's data);
- about 4 dubbed sessions of non-Spanish films at cinemas that also show VOSE;
- any sessions tagged English subtitles (`ensub`);
- at least one cinema from each city (Valencia, Barcelona, Alicante, Málaga).

For each one, open the session on the cinema's **own** site or booking page
(the session URL in the data, or the cinema's listings page) and read the
language label there. Record it as:
`cinema | film | date time | we show | cinema says | OK / MISMATCH`.

Any VOSE-vs-dubbed mismatch goes at the **top** of the report as URGENT, with
the scraper file that produced it and the raw label it got wrong.

Known label patterns (see `tests/test_vose.py` and the scrapers):
- Kinépolis puts "VOSE" in the titles of dubbed films; trust the session
  attributes ("English" vs "Spanish").
- Yelmo: ESPAÑOL = dubbed; SUB ESP / INGLÉS / JAPONÉS etc = VOSE.
- Verdi: CASTELLANO, CATALÁN and VARIOS are not VOSE.
- VOSI = original version with English subtitles. For a Spanish-language film
  that means Spanish audio, so it is not VOSE.

## 2. Once-over

Check each of these briefly, and report only what looks wrong or unusual:

1. **Scraper health:** each cinema's session count against `scraper_counts.json`
   and the last few weeks of git history for `docs/data/scraper_counts.json`.
   Look for cinemas drifting towards 0, stuck at the same number, or missing.
2. **VOSE check flags:** any VOSE_JUMP / ALL_VOSE / SPANISH_VOSE /
   that would fire on the current data. Flags already explained in
   `vose_reviewed.json` are fine. Run (UNKNOWN_LABEL only shows up during a
   real pipeline run):
   ```
   python -c "import json,pipeline,vose_check; f=json.load(open('docs/data/films_cache.json',encoding='utf-8')); print(vose_check.vose_audit(f, pipeline.CINEMA_META, None, reviewed=vose_check.load_reviewed())[1])"
   ```
3. **Labels the rules don't cover:** any new raw language or version strings in
   the scrapers' data.
4. **TMDB matches:** films whose poster, year or original language look wrong
   for the title (an old film matched to a new release, etc). A cheap test:
   compare the runtime the cinema gives with TMDB's runtime. A gap of more
   than about 10 minutes usually means the wrong film (that's how "La
   Violinista" and "El Niño" were caught on 16 Nov). Fixes go in
   `tmdb_overrides.json`.
5. **Duplicates:** the same film listed twice under different titles.
6. **Pages:** open whatson.movie and one city page per city, plus 3 or 4 film
   pages, as a visitor would. Try the VOSE / English filter. Look for empty
   days, broken layout, missing posters, and booking links that 404.
7. **Stale cinemas:** anything in `cinema_last_ok.json` older than 2 days.
   Those cinemas are being carried forward from an old scrape, and drop off
   the site after 3 days.
8. **Lost code:** run `git log --stat --since="8 days ago" -- '*.py'` and look
   for any commit that deletes far more code than its message suggests. On
   9 Nov a commit made from an old copy of `pipeline.py` silently removed
   the carry-forward and the cinema id check. Also confirm
   `python -m unittest discover -s tests` passes.

## 3. Report

Write the report to `reports/once_over_YYYY-MM-DD.md` (the folder is
gitignored), in this order:

1. **URGENT:** VOSE mismatches (or "None").
2. **Problems:** one line each, with the file or cinema and what I'd fix.
3. **Spot-check table** from step 1.
4. **All clear:** one line listing what was checked and passed.

Keep it short and in plain English. Then email it:

```
python send_report.py reports/once_over_YYYY-MM-DD.md "whatson.movie weekly once-over"
```

If the email fails (for example a Microsoft 365 sign-in error), say so
at the end of the session. The report file is still there.

## Adding to this checklist

When a new kind of mistake is found, add a line here so the next run looks
for it too.
