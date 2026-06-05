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
        deals.append({"title": g("title"), "link": g("link"), "desc": g("description")[:400]})
    return [d for d in deals if d["title"] and d["link"]]

def write_desc(deal):
    prompt = f"Write a 100-150 word UK deals site description for this offer. Friendly, helpful, no hype. Plain prose.\nDeal: {deal['title']}\nDetails: {deal['desc']}"
    body = json.dumps({"model": "claude-3-haiku-20240307", "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        return "".join(b.get("text","") for b in resp.get("content",[])).strip()
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8","ignore")
        raise Exception(f"HTTP {e.code}: {err[:300]}")

def make_page(deal, desc):
    t = html.escape(deal["title"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t} | Invisuale Deals</title>
<style>body{{font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:#f9f9f9}}
a.btn{{background:#e44;color:#fff;padding:10px 20px;border-radius:5px;text-decoration:none;display:inline-block;margin:15px 0}}
a.back{{color:#666;font-size:14px}}
</style>
</head>
<body>
<a class="back" href="/">← Back to all deals</a>
<h1>{t}</h1>
<p>{html.escape(desc)}</p>
<a class="btn" href="{deal['link']}" rel="nofollow sponsored" target="_blank">Get this deal →</a>
{SKIMLINKS}
</body>
</html>"""

def update_index(new_deals):
    try:
        with open("index.html") as f: content = f.read()
    except:
        content = ""
    
    cards = ""
    for d in new_deals:
        s = slug(d["title"])
        t = html.escape(d["title"])
        cards += f'<div class="deal"><h2><a href="/deals/{s}.html">{t}</a></h2><a href="/deals/{s}.html" class="btn">See deal →</a></div>\n'
    
    marker = '<div id="deals">'
    if marker in content:
        content = content.replace(marker, marker + "\n" + cards)
    else:
        content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invisuale — Best Deals Today</title>
<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#f9f9f9}}
h1{{color:#333}}.deal{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:20px;margin:15px 0}}
.deal h2{{margin:0 0 10px}}.deal h2 a{{color:#222;text-decoration:none}}
.btn{{display:inline-block;background:#e44;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;margin-top:10px}}
</style>
</head>
<body>
<h1>🔥 Best Deals Today</h1>
<p>Updated daily — hand-picked offers from across the web.</p>
<div id="deals">
{cards}
</div>
{SKIMLINKS}
</body>
</html>"""
    with open("index.html", "w") as f: f.write(content)

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
            print(f"✅ {deal['title'][:60]}")
            time.sleep(2)
        except Exception as e:
            print(f"❌ {e}: {deal['title'][:40]}")
    
    if new: update_index(new)
    save_posted(posted)
    print(f"Done. {count} deals added.")

if __name__ == "__main__":
    main()
