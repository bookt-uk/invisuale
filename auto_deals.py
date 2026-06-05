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
        # Extract image from description HTML
        desc_raw = ""
        dm = re.search(r"<description[^>]*>(.*?)</description>", block, re.S)
        if dm:
            desc_raw = dm.group(1)
            desc_raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", desc_raw, flags=re.S)
        img = ""
        im = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_raw)
        if im: img = im.group(1)
        # Also try enclosure
        em = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', block)
        if em and not img: img = em.group(1)
        desc_text = html.unescape(re.sub(r"<[^>]+>", "", desc_raw)).strip()[:400]
        deals.append({
            "title": g("title"),
            "link": g("link"),
            "desc": desc_text,
            "image": img
        })
    return [d for d in deals if d["title"] and d["link"]]

def write_desc(deal):
    prompt = f"Write a 100-150 word UK deals site description for this offer. Friendly, helpful, no hype. Plain prose only, no markdown, no bullet points, no headers.\nDeal: {deal['title']}\nDetails: {deal['desc']}"
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        text = "".join(b.get("text","") for b in resp.get("content",[])).strip()
        # Strip any markdown artifacts
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        return text
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8","ignore")
        raise Exception(f"HTTP {e.code}: {err[:300]}")

def make_page(deal, desc):
    t = html.escape(deal["title"])
    img_html = ""
    if deal.get("image"):
        img_html = f'<div class="deal-img"><img src="{html.escape(deal["image"])}" alt="{t}" loading="lazy"></div>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t} | Invisuale Deals</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Nunito Sans',sans-serif;background:#f8f7f4;color:#1e293b}}
header{{background:#0f172a;padding:0 24px;position:sticky;top:0;z-index:100}}
.header-inner{{max-width:1200px;margin:0 auto;display:flex;align-items:center;height:64px}}
.logo{{font-family:'Barlow Condensed',sans-serif;font-size:28px;font-weight:800;color:#fff;text-decoration:none}}
.logo span{{color:#ef4444}}
main{{max-width:800px;margin:0 auto;padding:40px 20px}}
.back{{color:#64748b;text-decoration:none;font-size:14px;font-weight:600;display:inline-block;margin-bottom:24px}}
.back:hover{{color:#ef4444}}
.deal-img{{margin-bottom:28px;border-radius:16px;overflow:hidden;background:#fff;border:1px solid #e2e8f0;text-align:center;padding:20px}}
.deal-img img{{max-width:100%;max-height:300px;object-fit:contain;border-radius:8px}}
h1{{font-family:'Barlow Condensed',sans-serif;font-size:clamp(28px,5vw,44px);font-weight:800;line-height:1.2;margin-bottom:20px;color:#0f172a}}
.desc{{font-size:17px;line-height:1.7;color:#334155;background:#fff;border-radius:12px;padding:24px;border:1px solid #e2e8f0;margin-bottom:24px}}
.btn{{display:inline-flex;align-items:center;gap:10px;background:#ef4444;color:#fff;padding:16px 32px;border-radius:12px;text-decoration:none;font-weight:700;font-size:16px;transition:background .2s}}
.btn:hover{{background:#dc2626}}
footer{{background:#0f172a;color:#64748b;text-align:center;padding:24px;font-size:13px;margin-top:64px}}
</style>
</head>
<body>
<header><div class="header-inner"><a href="/" class="logo">INVIS<span>UALE</span></a></div></header>
<main>
<a href="/" class="back">Back to all deals</a>
{img_html}
<h1>{t}</h1>
<div class="desc">{html.escape(desc)}</div>
<a href="{deal['link']}" class="btn" rel="nofollow sponsored" target="_blank">Get this deal &rarr;</a>
</main>
<footer>Invisuale - Best UK Deals. Prices correct at time of posting.</footer>
{SKIMLINKS}
</body>
</html>"""

def update_index(new_deals):
    all_files = sorted(os.listdir('deals')) if os.path.exists('deals') else []
    cards = ""
    for fname in reversed(all_files):
        if not fname.endswith('.html'): continue
        title = fname.replace('.html','').replace('-',' ').title()
        img_html = ""
        try:
            with open(f'deals/{fname}') as f: content = f.read()
            m = re.search(r'<h1>(.*?)</h1>', content)
            if m: title = html.unescape(m.group(1))
            im = re.search(r'<img[^>]+src="([^"]+)"', content)
            if im: img_html = f'<div class="card-img"><img src="{im.group(1)}" alt="" loading="lazy"></div>'
        except: pass
        cards += f'''<div class="deal">{img_html}<h2><a href="/deals/{fname}">{html.escape(title)}</a></h2><a href="/deals/{fname}" class="btn">See deal &rarr;</a></div>\n'''
    try:
        with open("index.html") as f: base = f.read()
    except: base = ""
    marker_start = '<div id="deals">'
    marker_end = '</div>'
    if marker_start in base:
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
            desc = write_desc(deal)
            s = slug(deal["title"])
            with open(f"deals/{s}.html", "w") as f: f.write(make_page(deal, desc))
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
