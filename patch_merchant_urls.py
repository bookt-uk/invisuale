#!/usr/bin/env python3
"""Backfill real merchant URLs into existing deal pages that still link to hotukdeals."""
import os, re, html, time, urllib.request

def resolve_merchant_url(hukd_url):
    m = re.search(r'-(\d+)$', hukd_url.rstrip('/'))
    if not m:
        return ""
    thread_id = m.group(1)
    visit_url = f"https://www.hotukdeals.com/visit/threadmain/{thread_id}"
    try:
        req = urllib.request.Request(visit_url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        final = resp.geturl()
        if "hotukdeals.com" in final:
            return ""
        return final
    except Exception as e:
        print(f"  error: {e}")
        return ""

patched = 0
for fname in sorted(os.listdir("deals")):
    if not fname.endswith(".html"):
        continue
    fpath = f"deals/{fname}"
    with open(fpath) as f:
        content = f.read()

    # Already has a real merchant URL stored?
    mm = re.search(r'<meta name="deal-url" content="([^"]*)"', content)
    if mm and mm.group(1) and "hotukdeals" not in mm.group(1):
        continue  # already fixed

    # Find the current CTA link
    lm = re.search(r'href="([^"]+)" (?:class="btn-cta"|rel="nofollow sponsored")', content)
    current_link = lm.group(1) if lm else ""

    if not current_link or "hotukdeals.com" not in current_link:
        continue  # already points to merchant or missing

    print(f"patching: {fname}")
    merchant_url = resolve_merchant_url(current_link)

    if not merchant_url:
        print(f"  no redirect found")
        continue

    print(f"  → {merchant_url[:80]}")

    # Update the CTA href
    new_content = content.replace(
        f'href="{current_link}" class="btn-cta"',
        f'href="{html.escape(merchant_url)}" class="btn-cta"'
    )
    # Also update the older btn format
    new_content = new_content.replace(
        f'href="{current_link}" rel="nofollow sponsored"',
        f'href="{html.escape(merchant_url)}" rel="nofollow sponsored"'
    )
    # Store/update deal-url meta tag
    if '<meta name="deal-url"' in new_content:
        new_content = re.sub(
            r'<meta name="deal-url" content="[^"]*"',
            f'<meta name="deal-url" content="{html.escape(merchant_url)}"',
            new_content
        )
    else:
        new_content = new_content.replace(
            '</head>',
            f'<meta name="deal-url" content="{html.escape(merchant_url)}">\n</head>',
            1
        )

    with open(fpath, "w") as f:
        f.write(new_content)
    patched += 1
    time.sleep(0.5)

print(f"\nDone. {patched} deals updated with real merchant URLs.")
