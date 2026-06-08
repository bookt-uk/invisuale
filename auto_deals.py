#!/usr/bin/env python3
import json, os, re, html, time, hashlib, urllib.request, urllib.error, urllib.parse
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FEED_URL = "https://www.hotukdeals.com/rss/deals"
MAX_PER_RUN = 25
STATE_FILE = "posted.json"

# Affiliate config. When AWIN_PUBLISHER_ID is set in env, merchant URLs are wrapped
# in Awin's deeplink format for retailers we have a programme with. AWIN_MERCHANT_MAP
# maps lower-cased retailer name -> Awin advertiser ID (mid). Add entries as you get
# approved by each merchant on darwin.awin.com.
AWIN_PUBLISHER_ID = os.environ.get("AWIN_PUBLISHER_ID", "")
AWIN_MERCHANT_MAP = {
    "bunches": "488",
    "bunches.co.uk": "488",
    # Add more as Awin merchants approve us:
    # "currys": "1599",
    # "john lewis": "6395",
    # "virgin media": "...",
    # "ee mobile": "...",
}
AMAZON_TAG = os.environ.get("AMAZON_ASSOCIATES_TAG", "")  # e.g. "invisuale-21"
AWIN_API_TOKEN = os.environ.get("AWIN_API_TOKEN", "")  # OAuth token for Awin Publisher API

def awin_fetch_joined_programmes():
    """Pull all joined Awin programmes via the API. Returns list of dicts with
    {id, name, logo, displayUrl, sector, region, deeplink, kpi}.
    Auto-discovers new merchants as they approve — no manual map updates needed."""
    if not (AWIN_API_TOKEN and AWIN_PUBLISHER_ID):
        return []
    out = []
    try:
        req = urllib.request.Request(
            f"https://api.awin.com/publishers/{AWIN_PUBLISHER_ID}/programmes/?relationship=joined",
            headers={"Authorization": f"Bearer {AWIN_API_TOKEN}", "Accept": "application/json"})
        r = urllib.request.urlopen(req, timeout=15)
        progs = json.loads(r.read())
    except Exception as e:
        print(f"Awin programmes fetch failed: {e}")
        return []
    for p in progs:
        mid = p.get("id")
        if not mid: continue
        # Pull richer details (logo, KPI, commission) per merchant
        detail = {}
        try:
            dreq = urllib.request.Request(
                f"https://api.awin.com/publishers/{AWIN_PUBLISHER_ID}/programmedetails?advertiserId={mid}",
                headers={"Authorization": f"Bearer {AWIN_API_TOKEN}", "Accept": "application/json"})
            detail = json.loads(urllib.request.urlopen(dreq, timeout=10).read())
        except Exception:
            pass
        info = detail.get("programmeInfo", {})
        kpi = detail.get("kpi", {})
        comm = detail.get("commissionRange", [{}])[0] if detail.get("commissionRange") else {}
        out.append({
            "id": mid,
            "name": info.get("name") or p.get("name") or "",
            "logo": info.get("logoUrl") or "",
            "displayUrl": info.get("displayUrl") or p.get("displayUrl") or "",
            "sector": info.get("primarySector") or "",
            "description": info.get("description") or "",
            "deeplink": f"https://www.awin1.com/cread.php?awinmid={mid}&awinaffid={AWIN_PUBLISHER_ID}",
            "commission": (f"{comm.get('min','')}{('%' if comm.get('type')=='percentage' else '£') if comm.get('min') is not None else ''}" if comm else ""),
            "approval_rate": kpi.get("approvalPercentage"),
            "epc": kpi.get("epc"),
        })
        # Auto-populate the merchant map so deals from this merchant get wrapped too
        AWIN_MERCHANT_MAP[info.get("name","").lower().strip()] = str(mid)
        if info.get("displayUrl"):
            domain = info["displayUrl"].replace("https://","").replace("http://","").replace("www.","").rstrip("/")
            AWIN_MERCHANT_MAP[domain.lower()] = str(mid)
    return out

def affiliate_wrap(merchant_url, merchant_name):
    """Wrap a raw merchant URL with the appropriate affiliate tracking, if available."""
    if not merchant_url:
        return merchant_url
    name = (merchant_name or "").lower().strip()
    # Amazon Associates UK tag injection.
    # NOTE: HotUKDeals injects its own affiliate tag (e.g. tag=pepperugc03-21) into
    # Amazon URLs before we see them. We must STRIP any existing tag (and HUKD's
    # ascsubtag tracking) and replace with ours, otherwise HUKD keeps the commission.
    if AMAZON_TAG and ("amazon.co.uk" in merchant_url or "amazon.com" in merchant_url):
        parts = urllib.parse.urlsplit(merchant_url)
        query_pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                       if k.lower() not in ("tag", "ascsubtag", "linkcode", "creative", "creativeasin", "ref_", "ref")]
        query_pairs.append(("tag", AMAZON_TAG))
        new_query = urllib.parse.urlencode(query_pairs)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    # Awin deeplink wrapping for approved merchants
    if AWIN_PUBLISHER_ID and name in AWIN_MERCHANT_MAP:
        mid = AWIN_MERCHANT_MAP[name]
        encoded = urllib.parse.quote(merchant_url, safe="")
        return f"https://www.awin1.com/cread.php?awinmid={mid}&awinaffid={AWIN_PUBLISHER_ID}&ued={encoded}"
    return merchant_url

# Google Analytics 4. Swap G-4ZW1TWSHM7 for your real Measurement ID once
# (one global find/replace activates it on every page, static + generated).
ANALYTICS = ('<script async src="https://www.googletagmanager.com/gtag/js?id=G-4ZW1TWSHM7"></script>\n'
             '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
             'gtag(\'js\',new Date());gtag(\'config\',\'G-4ZW1TWSHM7\');</script>\n')

HEADER_CSS = """
header{background:#0f172a;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.3)}
.header-inner{max-width:1400px;margin:0 auto;padding:0 16px;display:flex;align-items:center;height:60px;gap:12px}
.logo{font-family:'Barlow Condensed',sans-serif;font-size:26px;font-weight:800;color:#fff;letter-spacing:-.5px;text-decoration:none;flex-shrink:0}
.logo span{color:#ef4444}
nav{display:flex;align-items:center;gap:2px;flex:1}
.nav-link{color:#94a3b8;text-decoration:none;font-size:13px;font-weight:700;letter-spacing:.3px;padding:7px 12px;border-radius:6px;transition:color .15s,background .15s;white-space:nowrap;display:flex;align-items:center;gap:4px}
.nav-link:hover{color:#fff;background:rgba(255,255,255,.08)}
.nav-link.active{color:#fff;border-bottom:2px solid #ef4444;border-radius:0;padding-bottom:5px}
.cat-dropdown{position:relative}
.cat-dropdown .nav-link{cursor:pointer;user-select:none}
.cat-menu{display:none;position:absolute;top:100%;left:0;background:#fff;border:1px solid #e2e8f0;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.15);min-width:220px;z-index:200;padding:14px 0 6px;overflow:hidden}
.cat-dropdown:hover .cat-menu,.cat-dropdown:focus-within .cat-menu{display:block}
.cat-menu a{display:flex;align-items:center;gap:10px;padding:9px 16px;font-size:13px;font-weight:700;color:#1e293b;text-decoration:none;transition:background .12s}
.cat-menu a:hover{background:#f1f5f9;color:#ef4444}
.header-right{display:flex;align-items:center;gap:8px;flex-shrink:0}
.country-pill{display:flex;align-items:center;gap:5px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);border-radius:6px;padding:5px 10px;color:#fff;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.header-search{display:flex;align-items:center;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:0 12px;height:36px;gap:8px;width:220px;transition:width .3s}
.header-search input{background:none;border:none;outline:none;color:#fff;font-size:16px;font-family:inherit;width:100%}
.header-search input::placeholder{color:#64748b;font-size:14px}
.search-icon-btn{display:none;background:none;border:none;cursor:pointer;color:#94a3b8;padding:6px;line-height:0}
@media(max-width:960px){
  nav{gap:0}.nav-link{font-size:12px;padding:5px 8px}.cat-menu{left:-10px}
  .header-search{display:none}
  .search-icon-btn{display:flex;align-items:center;justify-content:center}
  .header-search.open{display:flex;position:absolute;top:60px;left:0;right:0;width:100%;border-radius:0;border-left:none;border-right:none;border-top:none;padding:0 16px;height:44px;background:#0f172a;border-bottom:1px solid rgba(255,255,255,.1)}
}
"""

HEADER_HTML = """<header>
  <div class="header-inner" style="position:relative">
    <a href="/" class="logo">IN<span>VISUALE</span></a>
    <nav>
      <a href="/" class="nav-link">Hot Deals</a>
      <a href="/codes/" class="nav-link">Codes</a>
      <a href="/guides/" class="nav-link">Guides</a>
      <div class="cat-dropdown">
        <span class="nav-link">Categories &#9660;</span>
        <div class="cat-menu">
          <a href="/categories/electronics.html">&#x1F4BB; Electronics</a>
          <a href="/categories/gaming.html">&#x1F3AE; Gaming</a>
          <a href="/categories/groceries.html">&#x1F6D2; Groceries</a>
          <a href="/categories/fashion-accessories.html">&#x1F457; Fashion &amp; Accessories</a>
          <a href="/categories/health-beauty.html">&#x1F484; Health &amp; Beauty</a>
          <a href="/categories/home-living.html">&#x1F3E0; Home &amp; Living</a>
          <a href="/categories/garden-do-it-yourself.html">&#x1F331; Garden &amp; DIY</a>
          <a href="/categories/family-kids.html">&#x1F476; Family &amp; Kids</a>
          <a href="/categories/car-motorcycle.html">&#x1F697; Car &amp; Motorcycle</a>
          <a href="/categories/broadband-phone-contracts.html">&#x1F4F1; Broadband &amp; Phone</a>
          <a href="/categories/services-contracts.html">&#x1F4CB; Services</a>
          <a href="/categories/">View All &#8594;</a>
        </div>
      </div>
    </nav>
    <div class="header-right">
      <div class="header-search" id="hdrSearch">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input type="text" placeholder="Search deals...">
      </div>
      <button class="search-icon-btn" onclick="var s=document.getElementById('hdrSearch');s.classList.toggle('open');if(s.classList.contains('open'))s.querySelector('input').focus()" aria-label="Search">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      </button>
<script>
function initSearch(){
  var inp=document.querySelector('.header-search input');
  if(!inp)return;
  inp.addEventListener('input',function(){
    var q=this.value.trim().toLowerCase();
    var cards=document.querySelectorAll('#deals .deal');
    if(!cards.length)return;
    cards.forEach(function(c){
      var txt=(c.querySelector('h2')||c).textContent.toLowerCase();
      c.style.display=(!q||txt.includes(q))?'':'none';
    });
  });
  inp.addEventListener('keydown',function(e){
    if(e.key==='Escape'){this.value='';this.dispatchEvent(new Event('input'));document.getElementById('hdrSearch').classList.remove('open');}
  });
}
document.readyState==='loading'?document.addEventListener('DOMContentLoaded',initSearch):initSearch();
</script>
      <div class="country-pill">&#x1F1EC;&#x1F1E7; UK &#9660;</div>
    </div>
  </div>
</header>"""

def load_posted():
    try:
        with open(STATE_FILE) as f: return set(json.load(f))
    except: return set()

def save_posted(p):
    with open(STATE_FILE, "w") as f: json.dump(sorted(p), f)

def slug(title):
    return re.sub(r'[^a-z0-9]+', '-', title.lower())[:60].strip('-')

def resolve_merchant_url(hukd_url):
    """Fetch HUKD page: check not expired, return (merchant_url, expired, shipping_label)."""
    m = re.search(r'-(\d+)$', hukd_url.rstrip('/'))
    if not m: return "", False, ""
    thread_id = m.group(1)
    try:
        req = urllib.request.Request(
            f"https://www.hotukdeals.com/deals/x-{thread_id}",
            headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        js = re.search(r'__INITIAL_STATE__ = (\{.*)', raw)
        if js:
            state = json.loads(js.group(1).rstrip().rstrip(';'))
            td = state.get("threadDetail", {})
            if td.get("isExpired") or td.get("stale"):
                return "", True, ""
            # Shipping
            shipping = td.get("shipping") or {}
            if shipping.get("isFree"):
                shipping_label = "free"
            else:
                cost = shipping.get("cost")
                shipping_label = f"£{float(cost):.2f}" if cost else ""
            visit = td.get("linkCloakedItemMainButton", "")
            if visit:
                req2 = urllib.request.Request(visit, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req2, timeout=15)
                final = resp.geturl()
                if "hotukdeals.com" not in final:
                    return final, False, shipping_label
        return "", False, ""
    except:
        return "", False, ""

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
        category = g("category")
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
            "category": category,
        })
    return [d for d in deals if d["title"] and d["link"]]

CATEGORY_LIST = "Electronics|Gaming|Groceries|Fashion & Accessories|Health & Beauty|Home & Living|Garden & Do It Yourself|Family & Kids|Car & Motorcycle|Broadband & Phone Contracts|Services & Contracts"

def write_desc(deal):
    prompt = (
        f"Write content for a UK deals site card. Return EXACTLY this format, no extra text:\n"
        f"DESCRIPTION: <80-120 word friendly plain prose, no markdown>\n"
        f"FEATURES: <feature 1, max 7 words> | <feature 2, max 7 words> | <feature 3, max 7 words>\n"
        f"CATEGORY: <one of: {CATEGORY_LIST}>\n\n"
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
        ai_category = ""
        dm = re.search(r'DESCRIPTION:\s*(.*?)(?=FEATURES:|CATEGORY:|$)', text, re.S)
        if dm: desc = dm.group(1).strip()
        fm = re.search(r'FEATURES:\s*(.*?)(?=CATEGORY:|$)', text, re.S)
        if fm:
            features = [f.strip() for f in fm.group(1).split('|') if f.strip()][:3]
        cm = re.search(r'CATEGORY:\s*(.*)', text)
        if cm: ai_category = cm.group(1).strip()
        desc = re.sub(r'^#+\s*', '', desc, flags=re.MULTILINE)
        desc = re.sub(r'\*\*(.*?)\*\*', r'\1', desc)
        return desc, features, ai_category
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode('utf-8','ignore')[:300]}")

DEAL_PAGE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{--red:#ef4444;--navy:#0f172a;--green:#16a34a;--border:#e2e8f0;--muted:#64748b;--bg:#f4f4f4}
body{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:#1e293b}
""" + HEADER_CSS + """
main{max-width:1100px;margin:0 auto;padding:28px 20px 64px}
.breadcrumb{font-size:13px;color:var(--muted);margin-bottom:24px;display:flex;align-items:center;gap:6px}
.breadcrumb a{color:var(--muted);text-decoration:none}
.breadcrumb a:hover{color:var(--red)}
.breadcrumb span{color:#94a3b8}
.deal-layout{display:grid;grid-template-columns:1fr 1fr;gap:32px;align-items:start}
@media(max-width:700px){.deal-layout{grid-template-columns:1fr}}
.img-panel{background:#f8f9fa;border-radius:14px;border:1px solid var(--border);padding:32px;display:flex;align-items:center;justify-content:center;min-height:320px}
.img-panel img{max-width:100%;max-height:340px;object-fit:contain;mix-blend-mode:multiply}
.img-placeholder{width:100%;height:320px;display:flex;align-items:center;justify-content:center;font-size:48px;color:#cbd5e1}
.info-panel{display:flex;flex-direction:column;gap:16px}
.hot-badge{display:inline-flex;align-items:center;gap:4px;background:var(--red);color:#fff;font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;padding:4px 10px;border-radius:100px;width:fit-content}
h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(24px,4vw,38px);font-weight:800;line-height:1.2;color:var(--navy)}
.price-row{display:flex;align-items:center;gap:12px}
.price{font-size:32px;font-weight:800;color:var(--red);line-height:1}
.orig-price{font-size:16px;color:var(--muted);text-decoration:line-through;font-weight:600}
.features{list-style:none;display:flex;flex-direction:column;gap:6px}
.features li{font-size:14px;color:#334155;display:flex;align-items:flex-start;gap:8px;line-height:1.4}
.features li::before{content:'✓';color:var(--green);font-weight:800;flex-shrink:0}
.delivery-row{display:flex;align-items:center;justify-content:space-between;font-size:13px;color:var(--muted);font-weight:600;padding:12px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.merchant-name{color:#334155;font-weight:700}
.btn-cta{display:flex;align-items:center;justify-content:center;gap:8px;background:var(--red);color:#fff;padding:15px 24px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px;transition:background .2s;width:100%}
.btn-cta:hover{background:#dc2626}
.desc-section{margin-top:32px;background:#fff;border-radius:12px;border:1px solid var(--border);padding:28px}
.desc-section h2{font-family:'Barlow Condensed',sans-serif;font-size:18px;font-weight:800;text-transform:uppercase;letter-spacing:.5px;margin-bottom:16px;color:var(--navy)}
.desc-section p{font-size:15px;line-height:1.75;color:#334155}
.trust-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:12px;overflow:hidden;margin-top:32px}
.trust-item{background:#fff;padding:16px 20px;display:flex;align-items:center;gap:12px}
.trust-icon{font-size:22px;flex-shrink:0}
.trust-text strong{display:block;font-size:13px;font-weight:800}
.trust-text span{font-size:12px;color:var(--muted)}
@media(max-width:700px){.trust-strip{grid-template-columns:repeat(2,1fr)}}
footer{background:var(--navy);color:#64748b;text-align:center;padding:24px;font-size:13px;margin-top:48px}
footer strong{color:#fff}
"""

def price_to_number(price_str):
    """Extract a numeric price like '34.99' from '£34.99' / '£1,299' etc. Returns '' if none."""
    if not price_str:
        return ""
    m = re.search(r'(\d[\d,]*\.?\d*)', price_str.replace(",", ""))
    return m.group(1) if m else ""

def make_page(deal, desc, features, merchant_url):
    t = html.escape(deal["title"])
    page_url = f'https://invisuale.com/deals/{slug(deal["title"])}.html'
    meta_desc = (desc[:155] + "…") if len(desc) > 156 else desc
    if not meta_desc:
        meta_desc = f'{deal["title"]} — deal from {deal.get("merchant","a UK retailer")}. Updated daily on Invisuale.'
    # --- JSON-LD structured data (Product + Breadcrumb) for Google rich results ---
    price_num = price_to_number(deal.get("price", ""))
    schema = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": deal["title"],
        "description": desc or f'Deal on {deal["title"]}',
    }
    if deal.get("image"):
        schema["image"] = deal["image"]
    if deal.get("merchant"):
        schema["brand"] = {"@type": "Brand", "name": deal["merchant"]}
    if price_num:
        schema["offers"] = {
            "@type": "Offer",
            "price": price_num,
            "priceCurrency": "GBP",
            "availability": "https://schema.org/InStock",
            "url": page_url,
        }
        if deal.get("merchant"):
            schema["offers"]["seller"] = {"@type": "Organization", "name": deal["merchant"]}
    breadcrumb = {
        "@context": "https://schema.org/",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://invisuale.com/"},
            {"@type": "ListItem", "position": 2, "name": "Hot Deals", "item": "https://invisuale.com/"},
            {"@type": "ListItem", "position": 3, "name": deal["title"], "item": page_url},
        ],
    }
    jsonld = (
        '<script type="application/ld+json">' + json.dumps(schema) + '</script>\n'
        '<script type="application/ld+json">' + json.dumps(breadcrumb) + '</script>\n'
    )
    img_html = ""
    if deal.get("image"):
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
        ship = deal.get("shipping", "")
        if ship == "free":
            ship_label = "🚚 Free delivery"
        elif ship:
            ship_label = f"🚚 +{ship} delivery"
        else:
            ship_label = "🚚 Check delivery"
        delivery_html = f'<div class="delivery-row"><span class="free">{ship_label}</span><span class="merchant-name">{html.escape(deal["merchant"])}</span></div>'
    cta_url = html.escape(merchant_url or deal["link"])
    img_panel = img_html if img_html else '<div class="img-placeholder">🏷️</div>'
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<meta name="description" content="{html.escape(meta_desc)}">\n'
        f'<link rel="canonical" href="{html.escape(page_url)}">\n'
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
        f'<meta property="og:title" content="{html.escape(deal["title"])}">\n'
        f'<meta property="og:description" content="{html.escape(meta_desc)}">\n'
        f'<meta property="og:image" content="{html.escape(deal.get("image","") or "https://invisuale.com/og-image.svg")}">\n'
        f'<meta property="og:url" content="{html.escape(page_url)}">\n'
        '<meta property="og:type" content="product">\n'
        '<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="deal-price" content="{html.escape(deal.get("price",""))}">\n'
        f'<meta name="deal-orig-price" content="{html.escape(deal.get("orig_price",""))}">\n'
        f'<meta name="deal-merchant" content="{html.escape(deal.get("merchant",""))}">\n'
        f'<meta name="deal-features" content="{html.escape("|".join(features))}">\n'
        f'<meta name="deal-image" content="{html.escape(deal.get("image",""))}">\n'
        f'<meta name="deal-url" content="{html.escape(merchant_url or "")}">\n'
        f'<meta name="deal-hukd-url" content="{html.escape(deal.get("link",""))}">\n'
        f'<meta name="deal-category" content="{html.escape(deal.get("category",""))}">\n'
        f'<meta name="deal-shipping" content="{html.escape(deal.get("shipping",""))}">\n'
        f'<title>{t} | Invisuale Deals</title>\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">\n'
        '<style>' + DEAL_PAGE_CSS + '</style>\n'
        + jsonld + ANALYTICS +
        '</head>\n<body>\n'
        + HEADER_HTML +
        f'\n<main>\n'
        f'  <div class="breadcrumb"><a href="/">Home</a><span>›</span><a href="/">Hot Deals</a><span>›</span><span>{t}</span></div>\n'
        f'  <div class="deal-layout">\n'
        f'    <div class="img-panel">{img_panel}</div>\n'
        f'    <div class="info-panel">\n'
        f'      <span class="hot-badge">🔥 HOT DEAL</span>\n'
        f'      <h1>{t}</h1>\n'
        f'      {price_html}\n'
        f'      {feat_html}\n'
        f'      {delivery_html}\n'
        f'      <a href="{cta_url}" class="btn-cta" rel="nofollow sponsored" target="_blank">Get this deal &rarr;</a>\n'
        f'    </div>\n  </div>\n'
        f'  <div class="desc-section"><h2>About this deal</h2><p>{html.escape(desc)}</p></div>\n'
        '  <div class="trust-strip">'
        '<div class="trust-item"><span class="trust-icon">✅</span><div class="trust-text"><strong>Community-Rated</strong><span>Sourced from real UK shoppers</span></div></div>'
        '<div class="trust-item"><span class="trust-icon">🛒</span><div class="trust-text"><strong>Buy Direct</strong><span>Straight to the retailer</span></div></div>'
        '<div class="trust-item"><span class="trust-icon">🔄</span><div class="trust-text"><strong>Daily Updates</strong><span>New deals every day at 9am</span></div></div>'
        '<div class="trust-item"><span class="trust-icon">🏷️</span><div class="trust-text"><strong>Always Free</strong><span>No fees, no sign-up</span></div></div>'
        '</div>\n'
        '</main>\n'
        '<footer style="background:#0f172a;color:#64748b;text-align:center;padding:28px 24px;font-size:13px;font-weight:600">'
        '<p><strong style="color:#fff">Invisuale</strong> — Best UK Deals. Prices correct at time of posting.</p>'
        '<p style="margin-top:12px;display:flex;align-items:center;justify-content:center;gap:16px;flex-wrap:wrap">'
        '<a href="/" style="color:#94a3b8;text-decoration:none">Hot Deals</a>'
        '<a href="/discount-codes.html" style="color:#94a3b8;text-decoration:none">Discount Codes</a>'
        '<a href="/guides/" style="color:#94a3b8;text-decoration:none">Guides</a>'
        '<a href="/about.html" style="color:#94a3b8;text-decoration:none">About</a>'
        '<a href="/privacy.html" style="color:#94a3b8;text-decoration:none">Privacy</a>'
        '</p>'
        '<p style="margin-top:10px;color:#64748b;font-size:11px">We may earn a commission when you buy through links on our site. As an Amazon Associate we earn from qualifying purchases.</p>'
        '</footer>\n'
        + '\n</body>\n</html>'
    )

def build_card(fname, title, img_src, price, merchant, features, shipping=""):
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
        if shipping == "free":
            ship_label = "🚚 Free delivery"
        elif shipping:
            ship_label = f"🚚 +{shipping} delivery"
        else:
            ship_label = "🚚 Check delivery"
        delivery_html = f'<div class="delivery-row"><span class="free-delivery">{ship_label}</span><span class="merchant">{html.escape(merchant)}</span></div>'
    return (
        f'<div class="deal">'
        f'<div class="hot-badge">🔥 HOT DEAL</div>'
        f'{img_block}'
        f'<div class="card-body">'
        f'<h2><a href="/deals/{fname}">{html.escape(title)}</a></h2>'
        f'{price_html}'
        f'{feat_html}'
        f'{delivery_html}'
        f'<a href="/deals/{fname}" class="btn" style="display:block;background:#ef4444;color:#ffffff;padding:10px;border-radius:8px;text-align:center;text-decoration:none;font-weight:700;font-size:13px;margin-top:auto;width:100%;box-sizing:border-box">View Deal →</a>'
        f'</div>'
        f'</div>\n'
    )

def update_index(new_deals):
    all_files = sorted(os.listdir('deals')) if os.path.exists('deals') else []
    cards = ""
    for fname in reversed(all_files):
        if not fname.endswith('.html'): continue
        title = fname.replace('.html','').replace('-',' ').title()
        img_src = price = merchant = shipping = ""
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
            mm = re.search(r'<meta name="deal-shipping" content="([^"]*)"', content)
            if mm: shipping = html.unescape(mm.group(1))
            if not img_src:
                im = re.search(r'<img[^>]+src="([^"]+)"', content)
                if im: img_src = im.group(1)
        except: pass
        cards += build_card(fname, title, img_src, price, merchant, features, shipping)

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

CATEGORY_ICONS = {
    "Gaming": "🎮", "Electronics": "💻", "Groceries": "🛒",
    "Fashion & Accessories": "👗", "Health & Beauty": "💄",
    "Home & Living": "🏠", "Garden & Do It Yourself": "🌱",
    "Family & Kids": "👶", "Car & Motorcycle": "🚗",
    "Services & Contracts": "📋", "Broadband & Phone Contracts": "📱",
}

def cat_slug(cat):
    return re.sub(r'[^a-z0-9]+', '-', cat.lower()).strip('-')

def make_category_pages():
    if not os.path.exists("deals"): return
    # Seed all known categories so pages always exist
    cats = {cat: [] for cat in CATEGORY_ICONS}
    for fname in os.listdir("deals"):
        if not fname.endswith(".html"): continue
        try:
            with open(f"deals/{fname}") as f: content = f.read()
            mm = re.search(r'<meta name="deal-category" content="([^"]*)"', content)
            cat = html.unescape(mm.group(1)) if mm else "Other"
            if not cat: cat = "Other"
            title_m = re.search(r'<h1>(.*?)</h1>', content)
            title = html.unescape(title_m.group(1)) if title_m else fname.replace('.html','').replace('-',' ').title()
            def ex(n): m=re.search(rf'<meta name="{n}" content="([^"]*)"',content); return html.unescape(m.group(1)) if m else ""
            img = ex("deal-image"); price = ex("deal-price")
            features = [f for f in ex("deal-features").split("|") if f.strip()][:3]
            merchant = ex("deal-merchant"); shipping = ex("deal-shipping")
            cats.setdefault(cat, []).append((fname, title, img, price, features, merchant, shipping))
        except: pass

    os.makedirs("categories", exist_ok=True)
    for cat, deals in cats.items():
        icon = CATEGORY_ICONS.get(cat, "🏷️")
        cards = ""
        for fname, title, img, price, features, merchant, shipping in deals:
            img_block = f'<div class="card-img"><img src="{html.escape(img)}" alt="" loading="lazy"></div>' if img else '<div class="card-placeholder">🏷️</div>'
            price_html = f'<div class="price-row"><span class="price">{html.escape(price)}</span></div>' if price else ""
            feat_html = f'<ul class="features">{"".join(f"<li>{html.escape(f)}</li>" for f in features)}</ul>' if features else ""
            if shipping == "free": ship_label = "🚚 Free delivery"
            elif shipping: ship_label = f"🚚 +{shipping} delivery"
            else: ship_label = "🚚 Check delivery"
            delivery_html = f'<div class="delivery-row"><span class="free-delivery">{ship_label}</span><span class="merchant">{html.escape(merchant)}</span></div>' if merchant else ""
            cards += f'<div class="deal"><div class="hot-badge">🔥 HOT DEAL</div>{img_block}<div class="card-body"><h2><a href="/deals/{fname}">{html.escape(title)}</a></h2>{price_html}{feat_html}{delivery_html}<a href="/deals/{fname}" style="display:block;background:#ef4444;color:#fff;padding:10px;border-radius:8px;text-align:center;text-decoration:none;font-weight:700;font-size:13px;margin-top:auto">View Deal →</a></div></div>\n'
        if not cards:
            cards = '<div style="grid-column:1/-1;text-align:center;padding:60px 20px;color:#64748b"><div style="font-size:48px;margin-bottom:16px">' + CATEGORY_ICONS.get(cat,"🏷️") + '</div><p style="font-size:16px;font-weight:700">No deals right now</p><p style="font-size:13px;margin-top:8px">New deals are added daily at 9am — check back soon!</p><a href="/" style="display:inline-block;margin-top:20px;background:#ef4444;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700">Browse all deals</a></div>'

        slug = cat_slug(cat)
        cat_css = (
            "*{box-sizing:border-box;margin:0;padding:0}\n"
            ":root{--navy:#0f172a;--red:#ef4444;--bg:#f4f4f4;--white:#fff;--text:#1e293b;--muted:#64748b;--border:#e2e8f0;--green:#16a34a;--shadow:0 1px 4px rgba(0,0,0,.08);--shadow-hover:0 8px 24px rgba(0,0,0,.14)}\n"
            "body{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:var(--text)}\n"
            + HEADER_CSS +
            ".page-hero{background:linear-gradient(135deg,var(--navy) 0%,#1e3a5f 100%);padding:32px 24px;text-align:center}\n"
            ".page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(32px,6vw,56px);font-weight:800;color:#fff;letter-spacing:-1px}\n"
            ".page-hero p{color:#94a3b8;font-size:14px;margin-top:8px;font-weight:600}\n"
            "main{max-width:1400px;margin:0 auto;padding:28px 24px 64px}\n"
            "#deals{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}\n"
            "@media(max-width:600px){#deals{grid-template-columns:repeat(2,1fr);gap:10px}}\n"
            ".deal{background:var(--white);border-radius:12px;border:1px solid var(--border);box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s;display:flex;flex-direction:column;position:relative;overflow:hidden}\n"
            ".deal:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover)}\n"
            ".hot-badge{position:absolute;top:9px;left:9px;z-index:2;display:flex;align-items:center;gap:3px;background:var(--red);color:#fff;font-size:10px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;padding:3px 8px;border-radius:100px}\n"
            ".card-img{background:#f8f9fa;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:1px solid var(--border);padding:14px;height:170px;flex-shrink:0}\n"
            ".card-img img{max-width:100%;max-height:100%;object-fit:contain;mix-blend-mode:multiply}\n"
            ".card-placeholder{width:100%;height:170px;display:flex;align-items:center;justify-content:center;background:#f1f5f9;font-size:32px;color:#cbd5e1;border-bottom:1px solid var(--border);flex-shrink:0}\n"
            ".card-body{padding:12px;display:flex;flex-direction:column;gap:8px;flex:1}\n"
            ".deal h2{font-size:13px;font-weight:700;line-height:1.4;color:var(--text);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:54px}\n"
            ".deal h2 a{color:inherit;text-decoration:none}\n"
            ".deal h2 a:hover{color:var(--red)}\n"
            ".price-row{display:flex;align-items:center;gap:7px}\n"
            ".price{font-size:20px;font-weight:800;color:var(--red);line-height:1}\n"
            ".features{list-style:none;display:flex;flex-direction:column;gap:3px}\n"
            ".features li{font-size:11px;color:#475569;display:flex;align-items:flex-start;gap:5px;line-height:1.35}\n"
            ".features li::before{content:'✓';color:var(--green);font-weight:800;font-size:11px;flex-shrink:0}\n"
            ".delivery-row{display:flex;align-items:center;justify-content:space-between;font-size:11px;color:var(--muted);font-weight:600;border-top:1px solid var(--border);padding-top:8px;margin-top:auto}\n"
            ".free-delivery{display:flex;align-items:center;gap:4px}\n"
            "footer{background:var(--navy);color:#64748b;text-align:center;padding:24px;font-size:13px}\n"
            "footer strong{color:#fff}\n"
        )
        page = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<title>{icon} {html.escape(cat)} Deals | Invisuale</title>\n'
            f'<meta name="description" content="Best UK {html.escape(cat)} deals updated daily.">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">\n'
            '<style>' + cat_css + '</style>\n' + ANALYTICS +
            '</head>\n<body>\n'
            + HEADER_HTML +
            f'\n<div class="page-hero"><h1>{icon} {html.escape(cat)}</h1><p>Best UK {html.escape(cat)} deals updated daily</p></div>\n'
            f'<main><div id="deals">{cards}</div></main>\n'
            '<footer><strong>Invisuale</strong> — Best UK Deals. Prices correct at time of posting.</footer>\n'
            '</body></html>'
        )
        with open(f"categories/{slug}.html", "w") as f: f.write(page)

    # Categories index page
    cat_cards = ""
    for cat, deals in sorted(cats.items()):
        icon = CATEGORY_ICONS.get(cat, "🏷️")
        slug = cat_slug(cat)
        cat_cards += f'<a href="/categories/{slug}.html" class="cat-card"><span class="cat-icon">{icon}</span><span class="cat-name">{html.escape(cat)}</span><span class="cat-count">{len(deals)} deals</span></a>\n'

    idx_css = (
        "*{box-sizing:border-box;margin:0;padding:0}\n"
        ":root{--navy:#0f172a;--red:#ef4444;--bg:#f4f4f4;--white:#fff;--border:#e2e8f0;--muted:#64748b}\n"
        "body{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:#1e293b}\n"
        + HEADER_CSS +
        ".page-hero{background:linear-gradient(135deg,var(--navy) 0%,#1e3a5f 100%);padding:32px 24px;text-align:center}\n"
        ".page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(32px,6vw,56px);font-weight:800;color:#fff}\n"
        "main{max-width:1000px;margin:0 auto;padding:32px 24px 64px}\n"
        ".cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}\n"
        ".cat-card{background:var(--white);border:1px solid var(--border);border-radius:12px;padding:24px 16px;display:flex;flex-direction:column;align-items:center;gap:8px;text-decoration:none;transition:transform .15s,box-shadow .15s;box-shadow:0 1px 4px rgba(0,0,0,.08)}\n"
        ".cat-card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.12)}\n"
        ".cat-icon{font-size:36px}\n"
        ".cat-name{font-size:13px;font-weight:800;color:#1e293b;text-align:center}\n"
        ".cat-count{font-size:11px;color:var(--muted);font-weight:600}\n"
        "footer{background:var(--navy);color:#64748b;text-align:center;padding:24px;font-size:13px}\n"
        "footer strong{color:#fff}\n"
    )
    index = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Deal Categories | Invisuale</title>\n'
        '<meta name="description" content="Browse UK deals by category — Gaming, Electronics, Groceries and more.">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">\n'
        '<style>' + idx_css + '</style>\n' + ANALYTICS +
        '</head>\n<body>\n'
        + HEADER_HTML +
        '\n<div class="page-hero"><h1>Browse by Category</h1></div>\n'
        f'<main><div class="cat-grid">{cat_cards}</div></main>\n'
        '<footer><strong>Invisuale</strong> — Best UK Deals. Prices correct at time of posting.</footer>\n'
        '</body></html>'
    )
    with open("categories/index.html", "w") as f: f.write(index)
    print(f"Built {len(cats)} category pages.")

def make_codes_pages(merchants):
    """Generate /codes/ index + per-brand pages from joined Awin merchants.
    Updates automatically as new merchants approve."""
    if not merchants:
        return
    os.makedirs("codes", exist_ok=True)
    css = HEADER_CSS + """
body{font-family:'Nunito Sans',sans-serif;background:#f4f4f4;color:#1e293b;margin:0}
.page-hero{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:48px 24px;text-align:center;color:#fff}
.page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(30px,6vw,46px);font-weight:800;line-height:1.1}
.page-hero p{color:#94a3b8;font-size:15px;margin-top:14px;font-weight:600;max-width:640px;margin-left:auto;margin-right:auto}
main{max-width:1200px;margin:0 auto;padding:36px 20px 64px}
.brand-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px;margin-top:24px}
.brand-card{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:22px;display:flex;flex-direction:column;align-items:center;text-align:center;text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s,border-color .15s}
.brand-card:hover{transform:translateY(-3px);box-shadow:0 10px 28px rgba(15,23,42,.1);border-color:#cbd5e1}
.brand-logo{width:120px;height:60px;object-fit:contain;margin-bottom:14px}
.brand-name{font-size:16px;font-weight:800;color:#0f172a;margin-bottom:4px}
.brand-meta{font-size:12px;color:#16a34a;font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.brand-comm{font-size:11px;color:#64748b;font-weight:700;margin-top:6px}
.brand-page-hero{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:32px;display:flex;align-items:center;gap:24px;margin-bottom:24px;flex-wrap:wrap}
.brand-page-hero img{width:140px;height:80px;object-fit:contain}
.brand-page-hero .info{flex:1;min-width:200px}
.brand-page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:34px;font-weight:800;color:#0f172a;margin-bottom:6px}
.brand-page-hero p{color:#475569;font-size:14px;line-height:1.6}
.cta-row{display:flex;gap:12px;margin-top:20px;flex-wrap:wrap}
.btn-primary{display:inline-block;background:#ef4444;color:#fff;padding:13px 24px;border-radius:8px;font-weight:800;text-decoration:none;font-size:14px}
.btn-primary:hover{background:#dc2626}
.btn-secondary{display:inline-block;background:#f1f5f9;color:#0f172a;padding:13px 24px;border-radius:8px;font-weight:800;text-decoration:none;font-size:14px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-top:16px}
.stat{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:14px;text-align:center}
.stat strong{display:block;font-size:18px;color:#0f172a;font-weight:800}
.stat span{font-size:11px;color:#64748b;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.seo-block{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:28px;margin-top:24px;font-size:14px;line-height:1.7;color:#334155}
.seo-block h2{font-family:'Barlow Condensed',sans-serif;font-size:20px;font-weight:800;color:#0f172a;margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px}
.back-link{display:inline-block;margin-bottom:18px;color:#64748b;font-size:13px;font-weight:700;text-decoration:none}
.back-link:hover{color:#ef4444}
footer{background:#0f172a;color:#64748b;text-align:center;padding:28px 24px;font-size:13px;font-weight:600;margin-top:48px}
footer a{color:#94a3b8;text-decoration:none;margin:0 8px}
"""
    footer_html = (
        '<footer><p><strong style="color:#fff">Invisuale</strong> — Best UK Deals, updated daily.</p>'
        '<p style="margin-top:8px;font-size:11px">We may earn a commission when you buy through links. As an Amazon Associate we earn from qualifying purchases.</p>'
        '<p style="margin-top:12px"><a href="/">Hot Deals</a><a href="/codes/">Codes</a><a href="/guides/">Guides</a>'
        '<a href="/about.html">About</a><a href="/privacy.html">Privacy</a></p></footer>'
    )

    # --- Per-brand pages ---
    for m in merchants:
        slug_b = re.sub(r'[^a-z0-9]+', '-', m["name"].lower()).strip('-')
        cta_link = m["deeplink"]
        stats = ""
        if m.get("approval_rate"):
            stats += f'<div class="stat"><strong>{m["approval_rate"]:.0f}%</strong><span>Approval Rate</span></div>'
        if m.get("epc") is not None:
            stats += f'<div class="stat"><strong>£{m["epc"]:.2f}</strong><span>Avg Earnings/Click</span></div>'
        if m.get("commission"):
            stats += f'<div class="stat"><strong>{m["commission"]}</strong><span>Commission</span></div>'
        if m.get("sector"):
            stats += f'<div class="stat"><strong>{html.escape(m["sector"])}</strong><span>Sector</span></div>'

        # Schema for brand page
        schema = json.dumps({
            "@context":"https://schema.org","@type":"Organization",
            "name": m["name"],
            "url": m.get("displayUrl",""),
            "logo": m.get("logo",""),
        })
        bc = json.dumps({"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
            {"@type":"ListItem","position":1,"name":"Home","item":"https://invisuale.com/"},
            {"@type":"ListItem","position":2,"name":"Discount Codes","item":"https://invisuale.com/codes/"},
            {"@type":"ListItem","position":3,"name":f"{m['name']} Offers","item":f"https://invisuale.com/codes/{slug_b}.html"},
        ]})

        page = (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(m["name"])} Discount Codes & Offers (Verified Daily) | Invisuale</title>'
            f'<meta name="description" content="Verified {html.escape(m["name"])} offers and discounts. Direct link to {html.escape(m["name"])}\'s official sale page — updated daily on Invisuale.">'
            f'<link rel="canonical" href="https://invisuale.com/codes/{slug_b}.html">'
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
            f'<meta property="og:title" content="{html.escape(m["name"])} Discount Codes | Invisuale">'
            f'<meta property="og:description" content="Verified {html.escape(m["name"])} offers, updated daily.">'
            f'<meta property="og:image" content="{html.escape(m.get("logo",""))}">'
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">'
            f'<script type="application/ld+json">{schema}</script>'
            f'<script type="application/ld+json">{bc}</script>'
            f'<style>{css}</style>' + ANALYTICS +
            '</head><body>' + HEADER_HTML +
            f'<div class="page-hero"><h1>{html.escape(m["name"])} Discount Codes & Offers</h1>'
            f'<p>Live offers from {html.escape(m["name"])} — verified through our official Awin partnership. We link straight to their sale page.</p></div>'
            '<main>'
            '<a href="/codes/" class="back-link">&larr; All retailers</a>'
            '<div class="brand-page-hero">'
            + (f'<img src="{html.escape(m["logo"])}" alt="{html.escape(m["name"])} logo">' if m.get("logo") else '')
            + f'<div class="info"><h1>{html.escape(m["name"])}</h1>'
            + (f'<p>{html.escape(m["description"])}</p>' if m.get("description") else "")
            + f'<div class="cta-row"><a href="{cta_link}" class="btn-primary" rel="nofollow sponsored" target="_blank">Visit {html.escape(m["name"])} →</a>'
            + (f'<a href="{html.escape(m["displayUrl"])}" class="btn-secondary" target="_blank">Direct site</a>' if m.get("displayUrl") else "")
            + '</div></div></div>'
            + (f'<div class="stats">{stats}</div>' if stats else "")
            + '<div class="seo-block"><h2>About this offer page</h2>'
            f'<p>This page links directly to {html.escape(m["name"])}\'s current offers via our verified Awin partnership. Rather than listing individual codes that often expire within hours, we send you straight to {html.escape(m["name"])}\'s official sale and offers pages where the live discounts are guaranteed to work.</p>'
            f'<p>Browse <a href="/codes/">all our retailer offer pages</a>, check today\'s <a href="/">hot deals</a>, or read our <a href="/guides/spot-a-real-deal-vs-fake-discount.html">guide on spotting fake discounts</a>.</p>'
            '</div></main>' + footer_html + '</body></html>'
        )
        with open(f"codes/{slug_b}.html", "w") as f: f.write(page)

    # --- Codes index page ---
    cards = ""
    for m in sorted(merchants, key=lambda x: x["name"].lower()):
        slug_b = re.sub(r'[^a-z0-9]+', '-', m["name"].lower()).strip('-')
        logo = f'<img src="{html.escape(m["logo"])}" alt="{html.escape(m["name"])} logo" class="brand-logo" loading="lazy">' if m.get("logo") else '<div class="brand-logo" style="display:flex;align-items:center;justify-content:center;background:#f1f5f9;border-radius:8px;font-weight:800;color:#475569">' + html.escape(m["name"][:2].upper()) + '</div>'
        meta = "Verified Partner"
        comm = f'<div class="brand-comm">Up to {m["commission"]} commission</div>' if m.get("commission") else ""
        cards += (
            f'<a href="/codes/{slug_b}.html" class="brand-card">'
            f'{logo}<div class="brand-name">{html.escape(m["name"])}</div>'
            f'<div class="brand-meta">{meta}</div>{comm}</a>'
        )
    idx_schema = json.dumps({"@context":"https://schema.org","@type":"CollectionPage","name":"UK Discount Codes & Voucher Codes",
        "description":f"Verified UK discount codes and offers from {len(merchants)} retailers.","url":"https://invisuale.com/codes/"})
    idx = (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>UK Discount Codes & Voucher Codes (Verified Daily) | Invisuale</title>'
        f'<meta name="description" content="Verified UK discount codes and offer pages from {len(merchants)} retailers. No fake codes — direct links to official sale pages, updated daily.">'
        '<link rel="canonical" href="https://invisuale.com/codes/">'
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        '<meta property="og:title" content="UK Discount Codes & Voucher Codes | Invisuale">'
        '<meta property="og:description" content="Verified UK discount codes, updated daily. No fake codes.">'
        '<meta property="og:image" content="https://invisuale.com/og-image.svg">'
        '<meta property="og:url" content="https://invisuale.com/codes/">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">'
        f'<script type="application/ld+json">{idx_schema}</script>'
        f'<style>{css}</style>' + ANALYTICS +
        '</head><body>' + HEADER_HTML +
        f'<div class="page-hero"><h1>UK Discount Codes & Voucher Codes</h1>'
        f'<p>We don\'t list fake or expired codes. Each link below takes you straight to the retailer\'s official offers page — where the real savings live. Currently {len(merchants)} verified partner{"s" if len(merchants)!=1 else ""}, growing weekly.</p></div>'
        '<main>'
        '<div class="seo-block" style="margin-top:0;margin-bottom:8px"><h2>Why no fake codes?</h2>'
        '<p>Most "voucher code" websites are stuffed with codes that expired months ago or never worked. We take a different approach: every retailer here is a <strong>verified partner</strong> through Awin, and we link straight to their official offers — where the discounts are always live.</p>'
        '<p>Looking for everyday deals instead? Browse <a href="/">today\'s hot deals</a> or our <a href="/categories/">category pages</a>.</p></div>'
        f'<div class="brand-grid">{cards}</div>'
        '</main>' + footer_html + '</body></html>'
    )
    with open("codes/index.html", "w") as f: f.write(idx)
    print(f"Built /codes/ with {len(merchants)} brand pages.")

def make_sitemap():
    cat_pages = [f'categories/{f}' for f in os.listdir('categories') if f.endswith('.html')] if os.path.exists('categories') else []
    guide_pages = [f'guides/{f}' for f in os.listdir('guides') if f.endswith('.html')] if os.path.exists('guides') else []
    code_pages = [f'codes/{f}' for f in os.listdir('codes') if f.endswith('.html') and f != 'index.html'] if os.path.exists('codes') else []
    static_pages = ['', 'about.html', 'privacy.html', 'discount-codes.html', 'guides/', 'codes/']
    pages = static_pages + [f'deals/{f}' for f in os.listdir('deals') if f.endswith('.html')] + cat_pages + [g for g in guide_pages if g != 'guides/index.html'] + code_pages
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

def purge_expired_deals():
    """Check all existing deal files against HUKD and delete any that are now expired."""
    if not os.path.exists("deals"): return
    removed = 0
    files = [f for f in os.listdir("deals") if f.endswith(".html")]
    print(f"Checking {len(files)} existing deals for expiry...")
    for fname in files:
        fpath = f"deals/{fname}"
        try:
            with open(fpath) as f: content = f.read()
            # Get HUKD URL from meta tag
            m = re.search(r'<meta name="deal-hukd-url" content="([^"]*)"', content)
            if not m:
                # Fallback: try to find thread ID from image URL
                im = re.search(r'hotukdeals\.com/threads/raw/\w+/(\d+)_', content)
                if not im: continue
                hukd_url = f"https://www.hotukdeals.com/deals/x-{im.group(1)}"
            else:
                hukd_url = html.unescape(m.group(1))
            _, expired, _shipping = resolve_merchant_url(hukd_url)
            if expired:
                os.remove(fpath)
                print(f"expired+removed: {fname}")
                removed += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"expiry check error {fname}: {e}")
    if removed:
        print(f"Removed {removed} expired deals.")

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
            merchant_url, expired, shipping_label = resolve_merchant_url(deal["link"])
            if expired:
                print(f"skip (expired): {deal['title'][:50]}")
                posted.add(did)
                continue
            # Wrap with affiliate tracking when configured (Amazon Associates / Awin)
            merchant_url = affiliate_wrap(merchant_url, deal.get("merchant", ""))
            deal["shipping"] = shipping_label
            desc, features, ai_category = write_desc(deal)
            if not deal.get("category") and ai_category:
                deal["category"] = ai_category
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
    purge_expired_deals()
    # Pull joined Awin merchants and build auto-updating /codes/ pages
    # (also auto-populates AWIN_MERCHANT_MAP so all merchants get affiliate-wrapped)
    merchants = awin_fetch_joined_programmes()
    if merchants:
        print(f"Awin: {len(merchants)} joined programmes — building codes pages")
        make_codes_pages(merchants)
    update_index(new)
    make_category_pages()
    make_sitemap()
    save_posted(posted)
    print(f"Done. {count} deals added.")

if __name__ == "__main__":
    main()
