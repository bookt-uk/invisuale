#!/usr/bin/env python3
"""Regenerate all deal pages with new 2-col layout + real merchant URLs."""
import os, re, html, time, urllib.request
import auto_deals

def resolve(hukd_url):
    m = re.search(r'-(\d+)$', hukd_url.rstrip('/'))
    if not m: return ""
    try:
        req = urllib.request.Request(
            f"https://www.hotukdeals.com/visit/threadmain/{m.group(1)}",
            headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        final = resp.geturl()
        return "" if "hotukdeals.com" in final else final
    except: return ""

def extract(content, name):
    m = re.search(rf'<meta name="{name}" content="([^"]*)"', content)
    return html.unescape(m.group(1)) if m else ""

done = 0
for fname in sorted(os.listdir("deals")):
    if not fname.endswith(".html"): continue
    fpath = f"deals/{fname}"
    with open(fpath) as f: content = f.read()

    # Extract stored data
    title_m = re.search(r'<h1>(.*?)</h1>', content)
    title = html.unescape(title_m.group(1)) if title_m else fname.replace('.html','').replace('-',' ').title()
    image  = extract(content, "deal-image")
    price  = extract(content, "deal-price")
    orig   = extract(content, "deal-orig-price")
    merch  = extract(content, "deal-merchant")
    feats  = [f for f in extract(content, "deal-features").split("|") if f.strip()]
    merchant_url = extract(content, "deal-url")

    # Fallback: old image from img tag
    if not image:
        im = re.search(r'<img[^>]+src="(https://images\.hotukdeals[^"]+)"', content)
        if im: image = im.group(1)

    # Extract description from old desc div or new desc-section
    desc = ""
    dm = re.search(r'<div class="desc">([^<]+)', content)
    if dm: desc = html.unescape(dm.group(1)).strip()
    if not desc:
        dm = re.search(r'<p>(.*?)</p>', content, re.S)
        if dm: desc = html.unescape(re.sub(r'<[^>]+>','',dm.group(1))).strip()

    # Get old HUKD link to resolve if needed
    if not merchant_url or "hotukdeals.com" in merchant_url:
        old_link_m = re.search(r'href="(https://www\.hotukdeals\.com/deals/[^"]+)"', content)
        if old_link_m:
            hukd_url = old_link_m.group(1)
            print(f"resolving {fname[:50]}...")
            merchant_url = resolve(hukd_url)
            if not merchant_url:
                print(f"  403/failed — keeping HUKD link")
                merchant_url = hukd_url
            else:
                print(f"  → {merchant_url[:70]}")
            time.sleep(0.8)

    deal = {"title": title, "image": image, "price": price,
            "orig_price": orig, "merchant": merch, "link": merchant_url or ""}
    new_html = auto_deals.make_page(deal, desc or "See deal for full details.", feats, merchant_url)
    with open(fpath, "w") as f: f.write(new_html)
    done += 1

print(f"\nRegenerated {done} deal pages.")
print("Rebuilding index...")
auto_deals.update_index([])
print("Done.")
