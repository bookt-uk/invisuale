#!/usr/bin/env python3
"""One-shot: backfill images into deal HTML files that are missing them."""
import os, re, html, time, urllib.request, urllib.error

def fetch_og_image(url):
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        # og:image is most reliable
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', raw)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', raw)
        if m:
            return m.group(1)
        # fallback: first product image
        m = re.search(r'<img[^>]+src=["\'](https://images\.hotukdeals\.com[^"\']+)["\']', raw)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  fetch error: {e}")
    return ""

def inject_image(fpath, img_url):
    with open(fpath) as f:
        content = f.read()
    t_m = re.search(r'<title>(.*?)\|', content)
    title = html.unescape(t_m.group(1)).strip() if t_m else ""
    img_html = (
        f'<div class="deal-img"><img src="{html.escape(img_url)}" '
        f'alt="{html.escape(title)}" loading="lazy"></div>'
    )
    # Insert before <h1>
    new_content = content.replace('<h1>', img_html + '\n<h1>', 1)
    with open(fpath, "w") as f:
        f.write(new_content)

def update_index():
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
            if im:
                img_html = f'<div class="card-img"><img src="{im.group(1)}" alt="" loading="lazy"></div>'
        except: pass
        if img_html:
            img_block = img_html
        else:
            img_block = '<div class="card-placeholder" style="aspect-ratio:1/1;max-height:220px;display:flex;align-items:center;justify-content:center;background:#f1f5f9;border-radius:10px;border:1px dashed #e2e8f0;font-size:40px;color:#cbd5e1">🏷️</div>'
        cards += f'<div class="deal">{img_block}<h2><a href="/deals/{fname}">{html.escape(title)}</a></h2><a href="/deals/{fname}" class="btn">See deal &rarr;</a></div>\n'

    with open("index.html") as f: base = f.read()
    marker_start = '<div id="deals">'
    marker_end = '<!--/deals-->'
    if marker_start in base and marker_end in base:
        start = base.index(marker_start) + len(marker_start)
        end = base.index(marker_end, start)
        base = base[:start] + '\n' + cards + base[end:]
    with open("index.html", "w") as f: f.write(base)
    print("index.html rebuilt")

deals_dir = "deals"
patched = 0
for fname in sorted(os.listdir(deals_dir)):
    if not fname.endswith('.html'): continue
    fpath = f"{deals_dir}/{fname}"
    with open(fpath) as f: content = f.read()
    if '<img' in content:
        continue  # already has image

    # get the source link
    lm = re.search(r'href="([^"]+)" rel="nofollow sponsored"', content)
    link = lm.group(1) if lm else ""
    print(f"patching: {fname}")

    img_url = fetch_og_image(link) if link else ""
    if img_url:
        inject_image(fpath, img_url)
        print(f"  ✓ {img_url[:80]}")
        patched += 1
    else:
        print(f"  no image found (link: {link[:60] if link else 'none'})")
    time.sleep(1)

print(f"\n{patched} files patched. Rebuilding index...")
update_index()
