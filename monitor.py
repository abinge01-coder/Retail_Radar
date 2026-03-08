#!/usr/bin/env python3
"""
Retail Radar — New Arrival Monitor for Buttercloth & Evereve
Checks Shopify stores for new products and sends email notifications.
Designed to run daily via GitHub Actions.
"""

import json
import hashlib
import smtplib
import os
import sys
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── Configuration ──────────────────────────────────────────────────────────

RETAILERS = {
    "buttercloth": {
        "name": "Buttercloth",
        "base_url": "https://buttercloth.com",
        "collections": [
            "/collections/all/products.json",
        ],
        "color": "#1a1a2e",
        "emoji": "👔",
    },
    "evereve": {
        "name": "Evereve",
        "base_url": "https://evereve.com",
        "collections": [
            "/collections/all/products.json",
        ],
        "color": "#2d2d3f",
        "emoji": "👗",
    },
}

SEEN_FILE = Path("data/seen_products.json")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY = 2  # seconds between requests to be polite


# ─── Shopify Product Fetcher ────────────────────────────────────────────────

def fetch_shopify_products(base_url: str, endpoint: str) -> list[dict]:
    """
    Fetch all products from a Shopify store's public JSON endpoint.
    Handles pagination (250 products per page).
    """
    all_products = []
    page = 1

    while True:
        url = f"{base_url}{endpoint}?limit=250&page={page}"
        print(f"  Fetching: {url}")

        req = Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            print(f"  ⚠ HTTP {e.code} for {url} — skipping")
            break
        except (URLError, TimeoutError) as e:
            print(f"  ⚠ Network error for {url}: {e} — skipping")
            break

        products = data.get("products", [])
        if not products:
            break

        all_products.extend(products)
        print(f"  Got {len(products)} products (total: {len(all_products)})")

        if len(products) < 250:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    return all_products


def normalize_product(product: dict, retailer_key: str) -> dict:
    """Extract the fields we care about from a Shopify product."""
    retailer = RETAILERS[retailer_key]

    # Get the first available image
    images = product.get("images", [])
    image_url = images[0].get("src", "") if images else ""

    # Get price range from variants
    variants = product.get("variants", [])
    prices = [float(v.get("price", "0")) for v in variants if v.get("price")]
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    return {
        "id": f"{retailer_key}_{product['id']}",
        "shopify_id": product["id"],
        "retailer": retailer_key,
        "retailer_name": retailer["name"],
        "title": product.get("title", "Unknown"),
        "handle": product.get("handle", ""),
        "product_type": product.get("product_type", ""),
        "vendor": product.get("vendor", ""),
        "tags": product.get("tags", []) if isinstance(product.get("tags"), list) else product.get("tags", "").split(", "),
        "min_price": min_price,
        "max_price": max_price,
        "image_url": image_url,
        "url": f"{retailer['base_url']}/products/{product.get('handle', '')}",
        "created_at": product.get("created_at", ""),
        "updated_at": product.get("updated_at", ""),
        "published_at": product.get("published_at", ""),
        "first_seen": datetime.now(timezone.utc).isoformat(),
    }


# ─── State Management ───────────────────────────────────────────────────────

def load_seen() -> dict:
    """Load previously seen product IDs from JSON file."""
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            print("⚠ Could not read seen file, starting fresh")
    return {"products": {}, "last_run": None}


def save_seen(seen: dict):
    """Save seen product IDs to JSON file."""
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2, default=str)


# ─── Email Notification ─────────────────────────────────────────────────────

def build_email_html(new_products: list[dict], stats: dict) -> str:
    """Build a clean HTML email with new product listings."""

    product_rows = ""
    for p in new_products:
        retailer = RETAILERS[p["retailer"]]
        price_str = f"${p['min_price']:.0f}"
        if p["max_price"] > p["min_price"]:
            price_str += f"–${p['max_price']:.0f}"

        img_html = ""
        if p["image_url"]:
            img_html = f'''
            <td style="width:80px;padding:12px;">
                <img src="{p['image_url']}" alt="{p['title']}" 
                     style="width:72px;height:72px;object-fit:cover;border-radius:8px;" />
            </td>'''
        else:
            img_html = f'''
            <td style="width:80px;padding:12px;">
                <div style="width:72px;height:72px;border-radius:8px;background:#f0f0f0;
                            display:flex;align-items:center;justify-content:center;font-size:28px;">
                    {retailer['emoji']}
                </div>
            </td>'''

        product_rows += f'''
        <tr style="border-bottom:1px solid #f0f0f0;">
            {img_html}
            <td style="padding:12px;">
                <div style="font-size:15px;font-weight:600;color:#1a1a1a;margin-bottom:4px;">
                    <a href="{p['url']}" style="color:#1a1a1a;text-decoration:none;">{p['title']}</a>
                </div>
                <div style="font-size:12px;color:#888;margin-bottom:4px;">
                    {p['retailer_name']} · {p['product_type'] or p['vendor']}
                </div>
                <div style="font-size:16px;font-weight:700;color:#c97b5a;">{price_str}</div>
            </td>
            <td style="padding:12px;text-align:right;">
                <a href="{p['url']}" 
                   style="display:inline-block;padding:8px 16px;border-radius:6px;
                          background:#1a1a2e;color:#fff;text-decoration:none;font-size:12px;
                          font-weight:500;">
                    View →
                </a>
            </td>
        </tr>'''

    # Group counts by retailer
    by_retailer = {}
    for p in new_products:
        by_retailer.setdefault(p["retailer_name"], 0)
        by_retailer[p["retailer_name"]] += 1

    summary_parts = [f"{count} from {name}" for name, count in by_retailer.items()]
    summary = " · ".join(summary_parts)

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f7f7f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <div style="max-width:600px;margin:0 auto;padding:24px;">
        
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#1a1a2e,#2d2d3f);border-radius:12px;padding:28px 24px;margin-bottom:20px;">
            <div style="font-size:22px;font-weight:700;color:#fff;margin-bottom:4px;">
                📡 Retail Radar
            </div>
            <div style="font-size:13px;color:rgba(255,255,255,0.6);">
                {len(new_products)} new item{'s' if len(new_products) != 1 else ''} found · {summary}
            </div>
        </div>

        <!-- Products -->
        <div style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <table style="width:100%;border-collapse:collapse;">
                {product_rows}
            </table>
        </div>

        <!-- Footer -->
        <div style="text-align:center;padding:20px;font-size:11px;color:#aaa;">
            Scanned {stats['total_scanned']} products across {stats['retailer_count']} retailers<br>
            {datetime.now(timezone.utc).strftime('%B %d, %Y at %I:%M %p UTC')}
        </div>
    </div>
</body>
</html>'''

    return html


def send_email(new_products: list[dict], stats: dict):
    """Send notification email via SMTP (Gmail)."""
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    notify_email = os.environ.get("NOTIFY_EMAIL")

    if not all([smtp_user, smtp_pass, notify_email]):
        print("⚠ Email credentials not configured. Skipping email.")
        print("  Set SMTP_USER, SMTP_PASS, and NOTIFY_EMAIL environment variables.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📡 Retail Radar: {len(new_products)} new item{'s' if len(new_products) != 1 else ''} found"
    msg["From"] = smtp_user
    msg["To"] = notify_email

    # Plain text fallback
    text_lines = ["Retail Radar — New Arrivals\n"]
    for p in new_products:
        price_str = f"${p['min_price']:.0f}"
        text_lines.append(f"• {p['title']} ({p['retailer_name']}) — {price_str}")
        text_lines.append(f"  {p['url']}\n")

    msg.attach(MIMEText("\n".join(text_lines), "plain"))
    msg.attach(MIMEText(build_email_html(new_products, stats), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, notify_email, msg.as_string())
        print(f"✅ Email sent to {notify_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("📡 Retail Radar — New Arrival Monitor")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    seen = load_seen()
    all_new = []
    total_scanned = 0

    for retailer_key, config in RETAILERS.items():
        print(f"\n🔍 Scanning {config['name']}...")

        for endpoint in config["collections"]:
            products = fetch_shopify_products(config["base_url"], endpoint)
            total_scanned += len(products)

            for product in products:
                normalized = normalize_product(product, retailer_key)
                product_id = normalized["id"]

                if product_id not in seen["products"]:
                    # New product!
                    seen["products"][product_id] = {
                        "title": normalized["title"],
                        "first_seen": normalized["first_seen"],
                        "price": normalized["min_price"],
                    }
                    all_new.append(normalized)

            time.sleep(REQUEST_DELAY)

    # Sort new items: most expensive first, then alphabetically
    all_new.sort(key=lambda p: (-p["min_price"], p["title"]))

    print(f"\n{'=' * 60}")
    print(f"📊 Results: {len(all_new)} new items found ({total_scanned} total scanned)")

    if all_new:
        print("\nNew items:")
        for p in all_new:
            print(f"  🆕 {p['retailer_name']}: {p['title']} (${p['min_price']:.0f})")

        stats = {
            "total_scanned": total_scanned,
            "retailer_count": len(RETAILERS),
        }

        # Send email notification
        send_email(all_new, stats)
    else:
        print("No new items since last check.")

    # Save updated state
    save_seen(seen)
    print(f"\n💾 State saved ({len(seen['products'])} products tracked)")

    # On first run, don't send email (just baseline the catalog)
    if seen.get("last_run") is None and all_new:
        print("\n📋 First run — cataloged existing products. No email sent.")
        print("   Future runs will only notify on genuinely new items.")


if __name__ == "__main__":
    main()
