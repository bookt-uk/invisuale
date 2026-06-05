#!/usr/bin/env python3
import json, os, re, html, time, hashlib, urllib.request, urllib.error
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FEED_URL = "https://www.hotukdeals.com/rss/deals"
MAX_PER_RUN = 10
STATE_FILE = "posted.json"
SKIMLINKS = '<script type="text/javascript" src="https://s.skimresources.com/js/304253X1792420.skimlinks.js"></script>'

def load_posted():
    try:
        with open(STATE_FILE) as f: return set(json.load(f))
    except: return set()

def save_posted(p):
    with open(STATE_FILE, "w") as f: json.dump(sorted(p), f)

def slug(title):
    return re.sub(r'[^a-z0-9]+', '-', title.lower())[:60].strip('-')

def fetch_deals():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    items = re.findall(r"<item>(.*?)</item>", raw, re.S)
    deals = []
    for block in items:
        def g(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
            if not m: return ""
            v = m.group(1)
            v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
            return html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
        desc_raw = ""
        dm = re.search(r"<description[^>]*>(.*?)</description>", block, re.S)
        if dm:
            desc_raw = dm.group(1)
            desc_raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", desc_raw, flags=re.S)
        # Image: prefer media:content / media:thumbnail
        img = ""
        mm = re.search(r'<media:content[^>]+url=["\']([^"\']+)["\']', block)
        if mm: img = mm.group(1)
        if not img:
            mm = re.search(r'<media:thumbnail[^>]+url=["\']([^"\']+)["\']', block)
            if mm: img = mm.group(1)
        if not img:
            im = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw)
            if im: img = im.group(1)
        if not img:
            em = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', block)
            if em: img = em.group(1)
        if img:
            img = re.sub(r'/re/\d+x\d+/', '/re/300x300/', img)
        desc_text = html.unescape(re.sub(r"<[^>]+>", "", desc_raw)).strip()[:400]
        # Merchant name and price from pepper:merchant
        merchant = ""
        price = ""
        pm = re.search(r'<pepper:merchant[^>]+name=["\']([^"\']+)["\']', block)
        if pm: merchant = pm.group(1)
        pp = re.search(r'<pepper:merchant[^>]+price=["\']([^"\']+)["\']', block)
        if pp: price = pp.group(1)
        deals.append({
            "title": g("title"),
            "link": g("link"),
            "desc": desc_text,
            "image": img,
            "merchant": merchant,
            "price": price,
        })
    return [d for d in deals if d["title"] and d["link"]]

def write_desc(deal):
    prompt = (
        f"Write content for a UK deals site card. Return EXACTLY this format, no extra text:\n"
        f"DESCRIPTION: <80-120 word friendly description, plain prose, no markdown>\n"
        f"FEATURES: <feature 1, max 7 words> | <feature 2, max 7 words> | <feature 3, max 7 words>\n\n"
        f"Deal: {deal['title']}\n"
        f"Price: {deal['price'] or 'unknown'}\n"
        f"Retailer: {deal['merchant'] or 'unknown'}\n"
        f"Details: {deal['desc']}"
    )
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        text = "".join(b.get("text","") for b in resp.get("content",[])).strip()
        desc = ""
        features = []
        dm = re.search(r'DESCRIPTION:\s*(.*?)(?=FEATURES:|$)', text, re.S)
        if dm: desc = dm.group(1).strip()
        fm = re.search(r'FEATURES:\s*(.*)', text, re.S)
        if fm:
            raw = fm.group(1).strip()
            features = [f.strip() for f in raw.split('|') if f.strip()][:4]
        desc = re.sub(r'^#+\s*', '', desc, flags=re.MULTILINE)
        desc = re.sub(r'\*\*(.*?)\*\*', r'\1', desc)
        return desc, features
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8","ignore")
        raise Exception(f"HTTP {e.code}: {err[:300]}")

def stars_html(fname):
    h = int(hashlib.md5(fname.encode()).hexdigest()[:4], 16)
    rating = 3.5 + (h % 16) / 16.0
    reviews = 50 + (h % 1150)
    full = int(rating)
    half = 1 if rating - full >= 0.5 else 0
    empty = 5 - full - half
    stars = '★' * full + ('½' if half else '') + '☆' * empty
    return f'<div class="stars-row"><span class="stars">{stars}</span><span class="star-count">({reviews:,})</span></div>'

def ends_ts(fname):
    h = int(hashlib.md5(fname.encode()).hexdigest()[4:8], 16)
    try: mtime = int(os.path.getmtime(f"deals/{fname}"))
    except: mtime = int(time.time())
    hours = 18 + (h % 54)
    return mtime + hours * 3600

def make_page(deal, desc, features):
    t = html.escape(deal["title"])
    img_html = ""
    if deal.get("image"):
        img_html = f'<div class="deal-img"><img src="{html.escape(deal["image"])}" alt="{t}" loading="lazy"></div>'
    feat_html = ""
    if features:
        items = "".join(f"<li>{html.escape(f)}</li>" for f in features)
        feat_html = f'<ul class="features">{items}</ul>'
    price_html = ""
    if deal.get("price"):
        price_html = f'<div class="price-display"><span class="price">{html.escape(deal["price"])}</span></div>'
    merchant_html = ""
    if deal.get("merchant"):
        merchant_html = f'<div class="merchant-tag">from <strong>{html.escape(deal["merchant"])}</strong></div>'
    # Metadata for index rebuilds
    meta_price = html.escape(deal.get("price",""))
    meta_merchant = html.escape(deal.get("merchant",""))
    meta_features = html.escape("|".join(features))
    meta_img = html.escape(deal.get("image",""))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="deal-price" content="{meta_price}">
<meta name="deal-merchant" content="{meta_merchant}">
<meta name="deal-features" content="{meta_features}">
<meta name="deal-image" content="{meta_img}">
<title>{t} | Invisuale Deals</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Nunito Sans',sans-serif;background:#f4f4f4;color:#1e293b}}
header{{background:#0f172a;padding:0 24px;position:sticky;top:0;z-index:100}}
.header-inner{{max-width:1200px;margin:0 auto;display:flex;align-items:center;height:60px}}
.logo{{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:800;color:#fff;text-decoration:none}}
.logo span{{color:#ef4444}}
main{{max-width:820px;margin:0 auto;padding:36px 20px}}
.back{{color:#64748b;text-decoration:none;font-size:14px;font-weight:600;display:inline-flex;align-items:center;gap:4px;margin-bottom:24px}}
.back:hover{{color:#ef4444}}
.deal-img{{margin-bottom:28px;border-radius:16px;overflow:hidden;background:#f8f9fa;border:1px solid #e2e8f0;display:flex;align-items:center;justify-content:center;padding:32px;min-height:260px}}
.deal-img img{{max-width:100%;max-height:280px;width:auto;height:auto;object-fit:contain;mix-blend-mode:multiply}}
h1{{font-family:'Barlow Condensed',sans-serif;font-size:clamp(26px,5vw,42px);font-weight:800;line-height:1.2;margin-bottom:16px;color:#0f172a}}
.price-display{{margin-bottom:12px}}
.price{{font-size:28px;font-weight:800;color:#ef4444}}
.merchant-tag{{font-size:14px;color:#64748b;font-weight:600;margin-bottom:16px}}
.merchant-tag strong{{color:#334155}}
.features{{list-style:none;display:flex;flex-direction:column;gap:6px;margin-bottom:20px}}
.features li{{font-size:14px;color:#334155;display:flex;align-items:flex-start;gap:8px;line-height:1.4}}
.features li::before{{content:'✓';color:#16a34a;font-weight:800;flex-shrink:0}}
.desc{{font-size:16px;line-height:1.7;color:#334155;background:#fff;border-radius:12px;padding:24px;border:1px solid #e2e8f0;margin-bottom:24px}}
.btn{{display:inline-flex;align-items:center;gap:10px;background:#ef4444;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px;transition:background .2s;width:100%;justify-content:center}}
.btn:hover{{background:#dc2626}}
footer{{background:#0f172a;color:#64748b;text-align:center;padding:24px;font-size:13px;margin-top:64px}}
</style>
</head>
<body>
<header><div class="header-inner"><a href="/" class="logo">INVIS<span>UALE</span></a></div></header>
<main>
<a href="/" class="back">← Back to all deals</a>
{img_html}
<h1>{t}</h1>
{price_html}
{merchant_html}
{feat_html}
<div class="desc">{html.escape(desc)}</div>
<a href="{deal['link']}" class="btn" rel="nofollow sponsored" target="_blank">Get this deal &rarr;</a>
</main>
<footer>Invisuale - Best UK Deals. Prices correct at time of posting.</footer>
{SKIMLINKS}
</body>
</html>"""

def build_card(fname, title, img_src, price, merchant, features, ends):
    img_block = (
        f'<div class="card-img"><img src="{html.escape(img_src)}" alt="" loading="lazy"></div>'
        if img_src else
        '<div class="card-placeholder">🏷️</div>'
    )
    price_html = ""
    if price:
        price_html = f'<div class="price-row"><span class="price">{html.escape(price)}</span></div>'
    feat_html = ""
    if features:
        items = "".join(f"<li>{html.escape(f)}</li>" for f in features[:3])
        feat_html = f'<ul class="features">{items}</ul>'
    delivery_html = ""
    if merchant:
        delivery_html = f'<div class="delivery-row"><span class="free-delivery">🚚 Free delivery</span><span class="merchant">{html.escape(merchant)}</span></div>'
    return (
        f'<div class="deal">'
        f'<div class="hot-badge">🔥 HOT DEAL</div>'
        f'{img_block}'
        f'<div class="card-body">'
        f'{stars_html(fname)}'
        f'<h2><a href="/deals/{fname}">{html.escape(title)}</a></h2>'
        f'{price_html}'
        f'{feat_html}'
        f'{delivery_html}'
        f'<a href="/deals/{fname}" class="btn">View Deal</a>'
        f'<div class="countdown">Deal ends in <span class="timer" data-ends="{ends}"></span></div>'
        f'</div>'
        f'</div>\n'
    )

def update_index(new_deals):
    all_files = sorted(os.listdir('deals')) if os.path.exists('deals') else []
    cards = ""
    for fname in reversed(all_files):
        if not fname.endswith('.html'): continue
        title = fname.replace('.html','').replace('-',' ').title()
        img_src = price = merchant = ""
        features = []
        try:
            with open(f'deals/{fname}') as f: content = f.read()
            m = re.search(r'<h1>(.*?)</h1>', content)
            if m: title = html.unescape(m.group(1))
            # New-style meta tags
            mm = re.search(r'<meta name="deal-image" content="([^"]*)"', content)
            if mm: img_src = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-price" content="([^"]*)"', content)
            if mm: price = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-merchant" content="([^"]*)"', content)
            if mm: merchant = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-features" content="([^"]*)"', content)
            if mm:
                raw = html.unescape(mm.group(1))
                features = [f for f in raw.split('|') if f.strip()]
            # Fallback: read img from deal-img div for old pages
            if not img_src:
                im = re.search(r'<img[^>]+src="([^"]+)"', content)
                if im: img_src = im.group(1)
        except: pass
        ends = ends_ts(fname)
        cards += build_card(fname, title, img_src, price, merchant, features, ends)

    try:
        with open("index.html") as f: base = f.read()
    except: base = ""
    marker_start = '<div id="deals">'
    marker_end = '<!--/deals-->'
    if marker_start in base and marker_end in base:
        start = base.index(marker_start) + len(marker_start)
        end = base.index(marker_end, start)
        base = base[:start] + '\n' + cards + base[end:]
    with open("index.html", "w") as f: f.write(base)

def make_sitemap():
    pages = [''] + [f'deals/{f}' for f in os.listdir('deals') if f.endswith('.html')]
    urls = '\n'.join([f'  <url><loc>https://invisuale.com/{p}</loc></url>' for p in pages])
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>'
    with open('sitemap.xml', 'w') as f: f.write(xml)

def main():
    os.makedirs("deals", exist_ok=True)
    posted = load_posted()
    deals = fetch_deals()
    new, count = [], 0
    for deal in deals:
        if count >= MAX_PER_RUN: break
        did = hashlib.md5((deal["title"]+deal["link"]).encode()).hexdigest()
        if did in posted: continue
        try:
            desc, features = write_desc(deal)
            s = slug(deal["title"])
            with open(f"deals/{s}.html", "w") as f: f.write(make_page(deal, desc, features))
            new.append(deal)
            posted.add(did)
            count += 1
            print(f"done: {deal['title'][:60]}")
            time.sleep(2)
        except Exception as e:
            print(f"skip ({e}): {deal['title'][:40]}")
    update_index(new)
    make_sitemap()
    save_posted(posted)
    print(f"Done. {count} deals added.")

if __name__ == "__main__":
    main()
