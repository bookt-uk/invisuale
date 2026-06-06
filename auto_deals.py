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

def resolve_merchant_url(hukd_url):
    """Fetch HUKD page: check not expired, return real merchant URL."""
    m = re.search(r'-(\d+)$', hukd_url.rstrip('/'))
    if not m: return "", False
    thread_id = m.group(1)
    try:
        req = urllib.request.Request(
            f"https://www.hotukdeals.com/deals/x-{thread_id}",
            headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        # Check expired/stale
        js = re.search(r'__INITIAL_STATE__ = (\{.*)', raw)
        if js:
            state = json.loads(js.group(1).rstrip().rstrip(';'))
            td = state.get("threadDetail", {})
            if td.get("isExpired") or td.get("stale"):
                return "", True  # expired
            visit = td.get("linkCloakedItemMainButton", "")
            if visit:
                req2 = urllib.request.Request(visit, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req2, timeout=15)
                final = resp.geturl()
                if "hotukdeals.com" not in final:
                    return final, False
        return "", False
    except:
        return "", False

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
        # Image
        img = ""
        mm = re.search(r'<media:content[^>]+url=["\']([^"\']+)["\']', block)
        if mm: img = mm.group(1)
        if not img:
            mm = re.search(r'<media:thumbnail[^>]+url=["\']([^"\']+)["\']', block)
            if mm: img = mm.group(1)
        if not img:
            im = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw)
            if im: img = im.group(1)
        if img:
            img = re.sub(r'/re/\d+x\d+/', '/re/300x300/', img)
        desc_text = html.unescape(re.sub(r"<[^>]+>", "", desc_raw)).strip()[:600]
        # Merchant name and price from pepper:merchant
        merchant = ""
        price = ""
        pm = re.search(r'<pepper:merchant[^>]+name=["\']([^"\']+)["\']', block)
        if pm: merchant = pm.group(1)
        pp = re.search(r'<pepper:merchant[^>]+price=["\']([^"\']+)["\']', block)
        if pp: price = pp.group(1)
        # Original/was price from description text
        orig_price = ""
        for pat in [r'[Ww]as[:\s]+[£$€](\d+[\d.,]*)', r'[Rr]{2}[Pp][:\s]+[£$€](\d+[\d.,]*)',
                    r'[Nn]ormally[:\s]+[£$€](\d+[\d.,]*)', r'[Uu]sually[:\s]+[£$€](\d+[\d.,]*)']:
            om = re.search(pat, desc_text)
            if om:
                orig_price = f"£{om.group(1)}"
                break
        hukd_url = g("link")
        deals.append({
            "title": g("title"),
            "link": hukd_url,
            "desc": desc_text,
            "image": img,
            "merchant": merchant,
            "price": price,
            "orig_price": orig_price,
        })
    return [d for d in deals if d["title"] and d["link"]]

def write_desc(deal):
    prompt = (
        f"Write content for a UK deals site card. Return EXACTLY this format, no extra text:\n"
        f"DESCRIPTION: <80-120 word friendly plain prose, no markdown>\n"
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
            features = [f.strip() for f in fm.group(1).split('|') if f.strip()][:3]
        desc = re.sub(r'^#+\s*', '', desc, flags=re.MULTILINE)
        desc = re.sub(r'\*\*(.*?)\*\*', r'\1', desc)
        return desc, features
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode('utf-8','ignore')[:300]}")

def make_page(deal, desc, features, merchant_url):
    t = html.escape(deal["title"])
    img_html = ""
    if deal.get("image"):
        # Use higher-res image for deal page
        big_img = re.sub(r'/re/\d+x\d+/', '/re/768x768/', deal["image"])
        img_html = f'<img src="{html.escape(big_img)}" alt="{t}" loading="lazy">'
    feat_html = ""
    if features:
        items = "".join(f"<li>{html.escape(f)}</li>" for f in features)
        feat_html = f'<ul class="features">{items}</ul>'
    price_html = ""
    if deal.get("price"):
        orig = f'<span class="orig-price">{html.escape(deal["orig_price"])}</span>' if deal.get("orig_price") else ""
        price_html = f'<div class="price-row"><span class="price">{html.escape(deal["price"])}</span>{orig}</div>'
    delivery_html = ""
    if deal.get("merchant"):
        delivery_html = f'<div class="delivery-row"><span class="free">🚚 Free delivery</span><span class="merchant-name">{html.escape(deal["merchant"])}</span></div>'
    cta_url = html.escape(merchant_url or deal["link"])
    # Metadata for index rebuilds
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="deal-price" content="{html.escape(deal.get('price',''))}">
<meta name="deal-orig-price" content="{html.escape(deal.get('orig_price',''))}">
<meta name="deal-merchant" content="{html.escape(deal.get('merchant',''))}">
<meta name="deal-features" content="{html.escape('|'.join(features))}">
<meta name="deal-image" content="{html.escape(deal.get('image',''))}">
<meta name="deal-url" content="{html.escape(merchant_url or '')}">
<title>{t} | Invisuale Deals</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--red:#ef4444;--navy:#0f172a;--green:#16a34a;--border:#e2e8f0;--muted:#64748b;--bg:#f4f4f4}}
body{{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:#1e293b}}
header{{background:var(--navy);padding:0 24px;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.3)}}
.header-inner{{max-width:1200px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;height:60px}}
.logo{{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:800;color:#fff;text-decoration:none}}
.logo span{{color:var(--red)}}
.header-nav{{display:flex;gap:4px}}
.hnav{{color:#94a3b8;text-decoration:none;font-size:13px;font-weight:700;padding:6px 12px;border-radius:6px}}
.hnav:hover{{color:#fff;background:rgba(255,255,255,.08)}}
.hnav.active{{color:#fff;border-bottom:2px solid var(--red);border-radius:0}}
main{{max-width:1100px;margin:0 auto;padding:28px 20px 64px}}
.breadcrumb{{font-size:13px;color:var(--muted);margin-bottom:24px;display:flex;align-items:center;gap:6px}}
.breadcrumb a{{color:var(--muted);text-decoration:none}}
.breadcrumb a:hover{{color:var(--red)}}
.breadcrumb span{{color:#94a3b8}}
.deal-layout{{display:grid;grid-template-columns:1fr 1fr;gap:32px;align-items:start}}
@media(max-width:700px){{.deal-layout{{grid-template-columns:1fr}}}}
.img-panel{{background:#f8f9fa;border-radius:14px;border:1px solid var(--border);padding:32px;display:flex;align-items:center;justify-content:center;min-height:320px}}
.img-panel img{{max-width:100%;max-height:340px;object-fit:contain;mix-blend-mode:multiply}}
.img-placeholder{{width:100%;height:320px;display:flex;align-items:center;justify-content:center;font-size:48px;color:#cbd5e1}}
.info-panel{{display:flex;flex-direction:column;gap:16px}}
.hot-badge{{display:inline-flex;align-items:center;gap:4px;background:var(--red);color:#fff;font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;padding:4px 10px;border-radius:100px;width:fit-content}}
h1{{font-family:'Barlow Condensed',sans-serif;font-size:clamp(24px,4vw,38px);font-weight:800;line-height:1.2;color:var(--navy)}}
.price-row{{display:flex;align-items:center;gap:12px}}
.price{{font-size:32px;font-weight:800;color:var(--red);line-height:1}}
.orig-price{{font-size:16px;color:var(--muted);text-decoration:line-through;font-weight:600}}
.features{{list-style:none;display:flex;flex-direction:column;gap:6px}}
.features li{{font-size:14px;color:#334155;display:flex;align-items:flex-start;gap:8px;line-height:1.4}}
.features li::before{{content:'✓';color:var(--green);font-weight:800;flex-shrink:0}}
.delivery-row{{display:flex;align-items:center;justify-content:space-between;font-size:13px;color:var(--muted);font-weight:600;padding:12px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}}
.merchant-name{{color:#334155;font-weight:700}}
.btn-cta{{display:flex;align-items:center;justify-content:center;gap:8px;background:var(--red);color:#fff;padding:15px 24px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px;transition:background .2s;width:100%}}
.btn-cta:hover{{background:#dc2626}}
.desc-section{{margin-top:32px;background:#fff;border-radius:12px;border:1px solid var(--border);padding:28px}}
.desc-section h2{{font-family:'Barlow Condensed',sans-serif;font-size:18px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;margin-bottom:16px;color:var(--navy)}}
.desc-section p{{font-size:15px;line-height:1.75;color:#334155}}
.trust-strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-top:32px}}
.trust-item{{background:#fff;padding:16px 20px;display:flex;align-items:center;gap:12px}}
.trust-icon{{font-size:22px;flex-shrink:0}}
.trust-text strong{{display:block;font-size:13px;font-weight:800}}
.trust-text span{{font-size:12px;color:var(--muted)}}
@media(max-width:700px){{.trust-strip{{grid-template-columns:repeat(2,1fr)}}}}
footer{{background:var(--navy);color:#64748b;text-align:center;padding:24px;font-size:13px;margin-top:48px}}
footer strong{{color:#fff}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <a href="/" class="logo">INVIS<span>UALE</span></a>
    <nav class="header-nav">
      <a href="/" class="hnav active">Hot Deals</a>
      <a href="#" class="hnav">Categories</a>
      <a href="#" class="hnav">Top Brands</a>
    </nav>
  </div>
</header>
<main>
  <div class="breadcrumb">
    <a href="/">Home</a><span>›</span>
    <a href="/">Hot Deals</a><span>›</span>
    <span>{t}</span>
  </div>
  <div class="deal-layout">
    <div class="img-panel">
      {img_html if img_html else '<div class="img-placeholder">🏷️</div>'}
    </div>
    <div class="info-panel">
      <span class="hot-badge">🔥 HOT DEAL</span>
      <h1>{t}</h1>
      {price_html}
      {feat_html}
      {delivery_html}
      <a href="{cta_url}" class="btn-cta" rel="nofollow sponsored" target="_blank">Get this deal &rarr;</a>
    </div>
  </div>
  <div class="desc-section">
    <h2>About this deal</h2>
    <p>{html.escape(desc)}</p>
  </div>
  <div class="trust-strip">
    <div class="trust-item"><span class="trust-icon">🔒</span><div class="trust-text"><strong>100% Secure</strong><span>Safe checkout guaranteed</span></div></div>
    <div class="trust-item"><span class="trust-icon">🚚</span><div class="trust-text"><strong>Free UK Delivery</strong><span>On thousands of deals</span></div></div>
    <div class="trust-item"><span class="trust-icon">🔄</span><div class="trust-text"><strong>Daily Updates</strong><span>New deals every day at 9am</span></div></div>
    <div class="trust-item"><span class="trust-icon">🏷️</span><div class="trust-text"><strong>Best Price Guarantee</strong><span>We find, you save</span></div></div>
  </div>
</main>
<footer><strong>Invisuale</strong> — Best UK Deals. Prices correct at time of posting.</footer>
{SKIMLINKS}
</body>
</html>"""

def build_card(fname, title, img_src, price, merchant, features):
    img_block = (
        f'<div class="card-img"><img src="{html.escape(img_src)}" alt="" loading="lazy"></div>'
        if img_src else
        '<div class="card-placeholder">🏷️</div>'
    )
    price_html = f'<div class="price-row"><span class="price">{html.escape(price)}</span></div>' if price else ""
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
        f'<h2><a href="/deals/{fname}">{html.escape(title)}</a></h2>'
        f'{price_html}'
        f'{feat_html}'
        f'{delivery_html}'
        f'<a href="/deals/{fname}" class="btn">View Deal</a>'
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
            mm = re.search(r'<meta name="deal-image" content="([^"]*)"', content)
            if mm: img_src = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-price" content="([^"]*)"', content)
            if mm: price = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-merchant" content="([^"]*)"', content)
            if mm: merchant = html.unescape(mm.group(1))
            mm = re.search(r'<meta name="deal-features" content="([^"]*)"', content)
            if mm:
                features = [f for f in html.unescape(mm.group(1)).split('|') if f.strip()]
            if not img_src:
                im = re.search(r'<img[^>]+src="([^"]+)"', content)
                if im: img_src = im.group(1)
        except: pass
        cards += build_card(fname, title, img_src, price, merchant, features)

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

def purge_old_deals(days=7):
    cutoff = time.time() - days * 86400
    removed = 0
    if not os.path.exists("deals"): return
    for fname in os.listdir("deals"):
        if not fname.endswith(".html"): continue
        fpath = f"deals/{fname}"
        if os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            print(f"purged: {fname}")
            removed += 1
    if removed:
        print(f"Purged {removed} expired deals.")

def main():
    os.makedirs("deals", exist_ok=True)
    purge_old_deals(days=7)
    posted = load_posted()
    deals = fetch_deals()
    new, count = [], 0
    for deal in deals:
        if count >= MAX_PER_RUN: break
        did = hashlib.md5((deal["title"]+deal["link"]).encode()).hexdigest()
        if did in posted: continue
        try:
            print(f"resolving merchant URL for: {deal['title'][:50]}")
            merchant_url, expired = resolve_merchant_url(deal["link"])
            if expired:
                print(f"skip (expired): {deal['title'][:50]}")
                posted.add(did)  # mark so we don't retry it
                continue
            desc, features = write_desc(deal)
            s = slug(deal["title"])
            with open(f"deals/{s}.html", "w") as f:
                f.write(make_page(deal, desc, features, merchant_url))
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
