#!/usr/bin/env python3
"""Fetch real price/merchant/shipping from HUKD JSON state for deals missing metadata."""
import os, re, html, time, json, urllib.request
import auto_deals

def fetch_hukd_data(thread_id):
    try:
        url = f"https://www.hotukdeals.com/deals/x-{thread_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        m = re.search(r'__INITIAL_STATE__ = (\{.*)', raw)
        if not m: return {}
        state = json.loads(m.group(1).rstrip().rstrip(';'))
        td = state.get("threadDetail", {})
        price_val = td.get("price", 0)
        price = f"£{price_val:.2f}" if price_val else ""
        merch = td.get("merchant", {}).get("merchantName", "")
        is_free = td.get("shipping", {}).get("isFree", False)
        return {"price": price, "merchant": merch, "free_delivery": is_free}
    except Exception as e:
        print(f"  error: {e}")
        return {}

done = 0
for fname in sorted(os.listdir("deals")):
    if not fname.endswith(".html"): continue
    fpath = f"deals/{fname}"
    with open(fpath) as f: content = f.read()

    # Skip if already has price
    mm = re.search(r'<meta name="deal-price" content="([^"]*)"', content)
    if mm and mm.group(1): continue

    # Get threadId from image URL
    im = re.search(r'hotukdeals\.com/threads/raw/\w+/(\d+)_', content)
    if not im: continue
    thread_id = im.group(1)

    print(f"enriching {fname[:55]} (thread {thread_id})")
    data = fetch_hukd_data(thread_id)
    if not data.get("price") and not data.get("merchant"):
        print("  no data"); continue

    print(f"  price={data.get('price')} merchant={data.get('merchant')}")

    # Update meta tags
    for key, val in [("deal-price", data.get("price","")),
                     ("deal-merchant", data.get("merchant",""))]:
        if f'<meta name="{key}"' in content:
            content = re.sub(rf'<meta name="{key}" content="[^"]*"',
                             f'<meta name="{key}" content="{html.escape(val)}"', content)

    with open(fpath, "w") as f: f.write(content)
    done += 1
    time.sleep(1)

print(f"\nEnriched {done} deals. Regenerating pages...")

# Now regen all pages with updated meta data
for fname in sorted(os.listdir("deals")):
    if not fname.endswith(".html"): continue
    fpath = f"deals/{fname}"
    with open(fpath) as f: content = f.read()

    def ex(name):
        m = re.search(rf'<meta name="{name}" content="([^"]*)"', content)
        return html.unescape(m.group(1)) if m else ""

    title_m = re.search(r'<h1>(.*?)</h1>', content)
    title = html.unescape(title_m.group(1)) if title_m else fname.replace('.html','').replace('-',' ').title()
    image = ex("deal-image")
    if not image:
        im = re.search(r'<img[^>]+src="(https://images\.hotukdeals[^"]+)"', content)
        if im: image = im.group(1)
    price    = ex("deal-price")
    orig     = ex("deal-orig-price")
    merchant = ex("deal-merchant")
    features = [f for f in ex("deal-features").split("|") if f.strip()]
    merchant_url = ex("deal-url")

    desc = ""
    dm = re.search(r'<p>(.*?)</p>', content, re.S)
    if dm: desc = html.unescape(re.sub(r'<[^>]+>', '', dm.group(1))).strip()

    deal = {"title": title, "image": image, "price": price,
            "orig_price": orig, "merchant": merchant, "link": merchant_url or ""}
    new_html = auto_deals.make_page(deal, desc or "See deal for full details.", features, merchant_url)
    with open(fpath, "w") as f: f.write(new_html)

print("Rebuilding index...")
auto_deals.update_index([])
print("Done.")
