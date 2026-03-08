# 📡 Retail Radar

A zero-cost new arrival monitor for **Buttercloth** and **Evereve**. Runs daily via GitHub Actions, checks for new products, and sends you an email digest.

## How It Works

1. **Scans** Shopify's public product JSON endpoint for each retailer
2. **Compares** against a list of previously seen products (stored in `data/seen_products.json`)
3. **Emails** you a clean HTML digest with any new items, images, prices, and direct links
4. **Commits** the updated product list back to the repo so it persists between runs

No API keys needed for the retailers — Shopify exposes product data publicly via `/products.json`.

## Setup (10 minutes)

### 1. Create a GitHub Repository

```bash
# Clone or copy this folder to a new repo
git init retail-radar
cd retail-radar
# Copy all files into this folder, then:
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/retail-radar.git
git push -u origin main
```

### 2. Create a Gmail App Password

You need a Gmail "App Password" (not your regular password):

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
4. Create a new app password (select "Mail" and "Other", name it "Retail Radar")
5. Copy the 16-character password

### 3. Add GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these three secrets:

| Secret Name    | Value                                    |
|----------------|------------------------------------------|
| `SMTP_USER`    | Your Gmail address (e.g. `you@gmail.com`)|
| `SMTP_PASS`    | The 16-character App Password from step 2|
| `NOTIFY_EMAIL` | Email to receive notifications           |

`SMTP_USER` and `NOTIFY_EMAIL` can be the same address.

### 4. Enable GitHub Actions

Go to your repo → **Actions** tab → Enable workflows if prompted.

### 5. Run It!

- **Manual run**: Actions tab → "Retail Radar — Daily Scan" → "Run workflow"
- **Automatic**: Runs daily at 7:00 AM Central Time

## First Run

The first run catalogs all existing products without sending an email — this establishes a baseline. Starting from the second run, you'll only get notified about genuinely new items.

## Schedule

The default schedule is **daily at 7:00 AM Central Time**. To change it, edit the cron expression in `.github/workflows/scan.yml`:

```yaml
schedule:
  - cron: '0 13 * * *'  # 13:00 UTC = 7:00 AM CT
```

Some handy alternatives:
- `'0 12 * * *'` → 6:00 AM CT
- `'0 14 * * *'` → 8:00 AM CT  
- `'0 13,1 * * *'` → 7:00 AM and 7:00 PM CT (twice daily)

## Adding More Retailers

Any Shopify store can be added. Edit the `RETAILERS` dict in `monitor.py`:

```python
RETAILERS = {
    # ... existing retailers ...
    "newstore": {
        "name": "Store Name",
        "base_url": "https://storename.com",
        "collections": [
            "/collections/all/products.json",
        ],
        "color": "#hexcolor",
        "emoji": "🛍️",
    },
}
```

**How to check if a store is on Shopify**: Visit `https://storename.com/products.json` — if you get JSON back, it's Shopify and this monitor will work.

## Files

```
retail-radar/
├── monitor.py                      # Main scraper + emailer
├── data/
│   └── seen_products.json          # Auto-generated product state
├── .github/
│   └── workflows/
│       └── scan.yml                # GitHub Actions schedule
├── .gitignore
└── README.md
```

## Troubleshooting

**No email received?**
- Check the Actions tab for run logs
- Verify your Gmail App Password is correct
- Check spam/junk folder
- Make sure all 3 secrets are set

**Too many notifications on first real run?**
- The first run baselines everything. If you delete `data/seen_products.json`, the next run re-baselines.

**Want to reset and start fresh?**
- Delete `data/seen_products.json` and push. The next run will re-catalog everything.

## Cost

**$0.** GitHub Actions provides 2,000 free minutes/month for public repos and 500 for private repos. This workflow uses ~30 seconds per run, so even daily runs use less than 15 minutes/month.
