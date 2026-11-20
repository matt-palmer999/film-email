# Cartelera Valencia — Weekly Cinema Newsletter

Scrapes current listings from Valencia cinemas every Thursday evening and sends
a bilingual (ES/EN) HTML email to your recipient list.

---

## How it works

1. **`scraper.py`** fetches each cinema's page from mabuse.es, extracts film titles,
   VOSE availability, ratings and poster images, then builds the HTML email.
2. **GitHub Actions** runs the script every Thursday at 19:00 Valencia time on
   GitHub's free infrastructure — no server needed.
3. The email is sent through Microsoft 365 (Graph API) from the whatson.movie mailbox (`mailer.py`).

---

## Setup — step by step

### 1. Fork / create the repo

Create a new **private** GitHub repository and push these files to it:

```
valencia-cinema/
├── scraper.py
├── requirements.txt
└── .github/
    └── workflows/
        └── newsletter.yml
```

Make it **private** so your secrets are not exposed.

---

### 2. Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add each of the following:

| Secret name    | Example value                        | Notes                                      |
|----------------|--------------------------------------|--------------------------------------------|
| `MS_TENANT_ID` | (Entra tenant id)                    | Microsoft 365 tenant                       |
| `MS_CLIENT_ID` | (app id)                             | Entra app "whatson.movie mailer"           |
| `MS_CLIENT_SECRET` | (app secret)                     | That app's client secret (Mail.Send)       |
| `FROM_ADDRESS` | `cartelera@yourdomain.com`           | The From: address recipients will see      |
| `FROM_NAME`    | `Cartelera Valencia`                 | The From: display name                     |
| `RECIPIENTS`   | `alice@example.com,bob@example.com`  | Comma-separated list of recipient emails   |

---

### 3. Test it manually

Once secrets are set, go to:
**Actions** → **Valencia Cinema Weekly Newsletter** → **Run workflow** → **Run workflow**

Watch the logs — if everything is green the email will arrive within a minute or two.

---

### 4. Automatic schedule

The workflow runs automatically every **Thursday at 17:00 UTC** (19:00 CET / 18:00 BST).
This is defined in `.github/workflows/newsletter.yml` as a cron expression.

To change the time, edit the cron line:
```yaml
- cron: "0 17 * * 4"
#        │  │  │ │ └─ 4 = Thursday
#        │  └──┘ └─── any day/month
#        └─────────── minute 0, hour 17 UTC
```

Use https://crontab.guru to calculate the UTC time for your preferred local time.

---

## Adding or removing recipients

Edit the `RECIPIENTS` secret in GitHub — it's a comma-separated string:
```
alice@example.com, bob@example.com, carol@example.com
```

No code changes needed.

---

## Running locally (for development)

```bash
pip install -r requirements.txt

export MS_TENANT_ID=...
export MS_CLIENT_ID=...
export MS_CLIENT_SECRET=...
export FROM_ADDRESS=cartelera@yourdomain.com
export FROM_NAME="Cartelera Valencia"
export RECIPIENTS=you@example.com
export OUTPUT_FILE=output.html   # optional: also saves HTML to disk

python scraper.py
```

Setting `OUTPUT_FILE` lets you preview the generated HTML in a browser before it's emailed.

---

## Troubleshooting

**No films appearing for a cinema?**
Mabuse.es occasionally changes their HTML structure. Check the cinema's page manually
and compare with the CSS selectors in `scraper.py` — the `fetch_cinema()` function
is where to look.

**Email not arriving?**
- Check GitHub Actions logs for "Microsoft sign-in failed" or "Microsoft 365 send failed"
- An expired client secret in Entra is the usual cause; make a new one and update `MS_CLIENT_SECRET`

**Wrong week dates?**
The script anchors on the next Friday from the day it runs. If run on a Thursday
it will show Friday–Thursday of that same week. This is correct behaviour.
