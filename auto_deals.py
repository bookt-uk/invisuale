#!/usr/bin/env python3
import json, os, re, html, time, hashlib, urllib.request, urllib.error, urllib.parse
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
FEED_URL = "https://www.hotukdeals.com/rss/deals"
MAX_PER_RUN = 25
MAX_HOMEPAGE = 60  # cap deal cards shown on the homepage (mobile weight); categories + sitemap still list all
STATE_FILE = "posted.json"

# Affiliate config. When AWIN_PUBLISHER_ID is set in env, merchant URLs are wrapped
# in Awin's deeplink format for retailers we have a programme with. AWIN_MERCHANT_MAP
# maps lower-cased retailer name -> Awin advertiser ID (mid). Add entries as you get
# approved by each merchant on darwin.awin.com.
AWIN_PUBLISHER_ID = os.environ.get("AWIN_PUBLISHER_ID", "")
AWIN_MERCHANT_MAP = {
    "bunches": "488",
    "bunches.co.uk": "488",
    "aatu": "17135",
    "aatu.co.uk": "17135",
    "8wines": "106707",
    "8wines.com": "106707",
    "game over": "37282",
    "gameover": "37282",
    "compare parking prices": "118401",
    "compareparkingprices": "118401",
    "compareparkingprices.co.uk": "118401",
    "mystery box shop": "45189",
    "mysteryboxshop": "45189",
    "sedley": "47609",
    "sedley.com": "47609",
    "cold town beer": "87545",
    "coldtownbeer": "87545",
    "morish snacks": "126437",
    "morish": "126437",
    "morishsnacks": "126437",
    "buy me once": "54235",
    "buymeonce": "54235",
    "buymeonce.co.uk": "54235",
    "brickzonehub": "121692",
    "brick zone hub": "121692",
    # Add more as Awin merchants approve us:
    # "currys": "1599",
    # "john lewis": "6395",
    # "virgin media": "...",
    # "ee mobile": "...",
}
AMAZON_TAG = os.environ.get("AMAZON_ASSOCIATES_TAG", "")  # e.g. "invisuale-21"
AWIN_API_TOKEN = os.environ.get("AWIN_API_TOKEN", "")  # OAuth token for Awin Publisher API

# Real voucher codes per merchant, keyed by lower-cased Awin programme name (or a
# substring of it). The Awin REST API does NOT expose promotion/voucher data on our
# plan, so verified codes are added here by hand from each merchant's programme page.
# When a merchant here matches a joined programme, make_codes_pages() renders a rich
# codes page (with copy buttons) at the given slug instead of the generic offers page,
# and the /codes/ index shows an "N Codes Available" badge.
# Local logo cache, keyed the same way as MERCHANT_CODES/AWIN_MERCHANT_MAP.
# Used as fallback when the Awin API returns no logoUrl (or a broken one) so
# /codes/ cards and per-brand pages always show real branding.
LOCAL_LOGOS = {
    "aatu": "/images/aatu-logo.jpg",
    "8wines": "/images/8wines-logo.jpg",
    "bunches": "/images/bunches-logo.jpg",
    "game over": "/images/gameover-logo.jpg",
    "compare parking prices": "/images/cpp-logo.jpg",
    "mystery box shop": "/images/mysterybox-logo.jpg",
    "sedley": "/images/sedley-logo.jpg",
    "morish snacks": "/images/morish-logo.jpg",
    "morish": "/images/morish-logo.jpg",
    "brickzonehub": "/images/brickzonehub-logo.jpg",
    "brick zone hub": "/images/brickzonehub-logo.jpg",
    "buy me once": "/images/buymeonce-logo.jpg",
}

# Single Awin Create-a-Feed URL bundling multiple advertisers' products in one CSV.
# Generated via Awin Toolbox → Create-a-Feed. fetch_product_feed() downloads it
# once per scraper run; products are filtered per brand using merchant_id below.
AWIN_FEED_URL = ("https://productdata.awin.com/datafeed/download/apikey/41436bc2f70215f1f5ddb5dbec0c83a4/"
                 "language/en/fid/488,100644/rid/0/hasEnhancedFeeds/0/columns/"
                 "aw_deep_link,product_name,merchant_image_url,search_price,merchant_id,"
                 "aw_image_url,store_price,rrp_price,base_price,product_price_old,savings_percent,"
                 "merchant_deep_link/format/csv/delimiter/%2C/compression/gzip/adultcontent/1/")
MAX_FEED_PRODUCTS = 12  # cards per brand page
_feed_cache = None  # populated once per scraper run

def fetch_product_feed():
    """Download the multi-merchant Awin CSV feed once. Returns list of dicts
    with merchant_id so callers can filter per brand. Cached for the run."""
    global _feed_cache
    if _feed_cache is not None: return _feed_cache
    _feed_cache = []
    if not AWIN_FEED_URL: return _feed_cache
    try:
        import csv, io
        req = urllib.request.Request(AWIN_FEED_URL, headers={"User-Agent":"Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=120).read()
        if raw[:2] == b'\x1f\x8b':
            import gzip
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", "ignore")
        for r in csv.DictReader(io.StringIO(text)):
            name = (r.get("product_name") or "").strip()
            link = (r.get("aw_deep_link") or r.get("merchant_deep_link") or "").strip()
            img = (r.get("aw_image_url") or r.get("merchant_image_url") or "").strip()
            price = (r.get("search_price") or r.get("store_price") or "").strip()
            orig = (r.get("rrp_price") or r.get("base_price") or r.get("product_price_old") or "").strip()
            mid = (r.get("merchant_id") or "").strip()
            if not (name and link and mid): continue
            _feed_cache.append({"name":name,"link":link,"image":img,"price":price,
                                "orig":orig,"merchant_id":mid})
        print(f"Awin feed: loaded {len(_feed_cache)} products")
    except Exception as e:
        print(f"product feed failed: {e}")
    return _feed_cache

def products_for_merchant(merchant_id, limit=MAX_FEED_PRODUCTS):
    """Pick up to N *genuinely discounted* products for a merchant (orig > price).
    Returns [] when the merchant's feed carries no discount data, so the grid
    only ever shows real deals — never a misleading full-price catalogue."""
    if not merchant_id: return []
    target = str(merchant_id)
    out = []
    for p in fetch_product_feed():
        if p["merchant_id"] != target: continue
        try:
            sp = float(p["price"]); op = float(p["orig"])
            if op > sp > 0:
                out.append(p)
        except (ValueError, TypeError):
            continue
        if len(out) >= limit: break
    return out

def render_feed_products(merchant_name, products):
    """Build a 'Featured Products' grid HTML block for a brand page."""
    if not products: return ""
    cards = ""
    for p in products:
        img = (f'<img src="{html.escape(p["image"])}" alt="" loading="lazy">'
               if p["image"] else '<div class="fp-ph">🏷️</div>')
        price_html = ""
        if p["price"]:
            try:
                pn = float(p["price"])
                orig_html = ""
                if p["orig"]:
                    try:
                        on = float(p["orig"])
                        if on > pn:
                            orig_html = f'<s>£{on:.2f}</s>'
                    except: pass
                price_html = f'<div class="fp-price">£{pn:.2f} {orig_html}</div>'
            except: pass
        cards += (
            f'<a href="{html.escape(p["link"])}" rel="nofollow sponsored" target="_blank" class="fp-card">'
            f'<div class="fp-img">{img}</div>'
            f'<div class="fp-name">{html.escape(p["name"][:80])}</div>'
            f'{price_html}</a>'
        )
    return (
        f'<h2 style="font-family:\'Barlow Condensed\',sans-serif;font-size:24px;font-weight:800;color:#0f172a;'
        f'margin:32px 0 16px">Featured products from {html.escape(merchant_name)}</h2>'
        f'<div class="fp-grid">{cards}</div>'
    )

def local_logo(merchant_name):
    n = (merchant_name or "").lower().strip()
    if n in LOCAL_LOGOS: return LOCAL_LOGOS[n]
    for key, val in LOCAL_LOGOS.items():
        if key in n: return val
    return ""

MERCHANT_CODES = {
    "aatu": {
        "slug": "aatu-co-uk",
        # Buyer-relevant facts shown as the stats bar (not affiliate KPIs).
        "facts": [("Free", "Delivery over £30"), ("80%", "Meat or fish"), ("New & subs", "Codes apply")],
        "codes": [
            ("HELLO10", "10% off your first order", "New customers — 10% off your first AATU order."),
            ("FIRSTSUB", "30% off your first subscription", "30% off your first AATU Subscribe & Save order."),
        ],
    },
    "8wines": {
        "slug": "8wines",
        # offers_url overrides the deeplink target so buyers land directly on the sale page.
        "offers_url": "https://8wines.com/wines?am_on_sale=1&product_list_order=sale_percent",
        "facts": [("£8 off", "New customers"), ("Free ship", "Orders £400+"), ("Gold", "7 yrs running")],
        "codes": [],  # No public voucher codes
        # Code-less promotions (entered by hand from the advertiser's Awin offers).
        # Rendered as offer cards with a Shop button — applied automatically via our link.
        "offers": [
            ("£8 off your first order", "New customers get £8 off orders over £88 — applied through our link, no code needed."),
            ("Free UK shipping on orders over £400", "Stock up for the cellar and pay nothing for delivery."),
        ],
    },
}

def lookup_codes(merchant_name):
    """Return the MERCHANT_CODES entry for a merchant name (exact or substring match)."""
    n = (merchant_name or "").lower().strip()
    if n in MERCHANT_CODES:
        return MERCHANT_CODES[n]
    for key, val in MERCHANT_CODES.items():
        if key in n:
            return val
    return None

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
        # Use `or {}` (not a default arg) so an explicit null in the JSON — which
        # some newly-approved programmes return for kpi/programmeInfo — becomes {}
        # rather than None (which would crash on .get()).
        info = detail.get("programmeInfo") or {}
        kpi = detail.get("kpi") or {}
        comm_list = detail.get("commissionRange") or []
        comm = (comm_list[0] or {}) if comm_list else {}
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
        nm = (info.get("name") or "").lower().strip()
        if nm:
            AWIN_MERCHANT_MAP[nm] = str(mid)
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
.menu-toggle{display:none;background:none;border:none;cursor:pointer;color:#94a3b8;padding:6px;line-height:0}
.chev{display:inline-block;transition:transform .25s}
.nav-backdrop{display:none}
@media(max-width:860px){
  .header-right{margin-left:auto}
  .header-inner nav{display:flex;position:fixed;top:0;right:0;bottom:0;height:100vh;width:80%;max-width:330px;flex-direction:column;align-items:stretch;background:#0f172a;padding:70px 0 28px;gap:0;overflow-y:auto;transform:translateX(100%);transition:transform .3s ease;box-shadow:-12px 0 34px rgba(0,0,0,.5);z-index:400}
  .header-inner nav.open{transform:translateX(0)}
  .header-inner nav .nav-link{font-size:16px;padding:15px 24px;border-radius:0;width:100%;border-bottom:1px solid rgba(255,255,255,.06)}
  .menu-toggle{display:flex;align-items:center;justify-content:center}
  .cat-dropdown{position:static}
  .cat-dropdown>.cat-toggle{display:flex;align-items:center;justify-content:space-between;cursor:pointer}
  .cat-dropdown.cat-open .chev{transform:rotate(180deg)}
  .cat-menu{display:none;position:static;box-shadow:none;border:none;background:rgba(0,0,0,.25);padding:0;min-width:0}
  .cat-dropdown.cat-open .cat-menu{display:block}
  .cat-menu a{color:#94a3b8;padding:12px 34px;font-size:14px;border-bottom:1px solid rgba(255,255,255,.04)}
  .nav-backdrop{display:block;position:fixed;inset:0;background:rgba(0,0,0,.55);opacity:0;pointer-events:none;transition:opacity .3s;z-index:399}
  .nav-backdrop.open{opacity:1;pointer-events:auto}
  .header-search{display:none}
  .search-icon-btn{display:flex;align-items:center;justify-content:center}
  .header-search.open{display:flex;position:absolute;top:60px;left:0;right:0;width:100%;border-radius:0;border-left:none;border-right:none;border-top:none;padding:0 16px;height:44px;background:#0f172a;border-bottom:1px solid rgba(255,255,255,.1);z-index:401}
  .country-pill{padding:5px 8px;font-size:12px}
}
"""

FOOTER_HTML = """<footer style="background:#0f172a;color:#64748b;text-align:center;padding:28px 24px;font-size:13px;font-weight:600">
<p><strong style="color:#fff">Invisuale</strong> &mdash; Best UK Deals. Prices correct at time of posting.</p>
<p style="margin-top:12px;display:flex;align-items:center;justify-content:center;gap:16px;flex-wrap:wrap">
<a href="/" style="color:#94a3b8;text-decoration:none">Hot Deals</a>
<a href="/codes/" style="color:#94a3b8;text-decoration:none">Codes</a>
<a href="/brands/" style="color:#94a3b8;text-decoration:none">Brands</a>
<a href="/categories/" style="color:#94a3b8;text-decoration:none">Categories</a>
<a href="/guides/" style="color:#94a3b8;text-decoration:none">Guides</a>
<a href="/about.html" style="color:#94a3b8;text-decoration:none">About</a>
<a href="/privacy.html" style="color:#94a3b8;text-decoration:none">Privacy</a>
</p>
<p style="margin-top:10px;color:#64748b;font-size:11px">We may earn a commission when you buy through links on our site. As an Amazon Associate we earn from qualifying purchases.</p>
</footer>"""

HEADER_HTML = """<header>
  <div class="header-inner" style="position:relative">
    <a href="/" class="logo">IN<span>VISUALE</span></a>
    <nav>
      <a href="/" class="nav-link">Hot Deals</a>
      <a href="/codes/" class="nav-link">Codes</a>
      <a href="/brands/" class="nav-link">Brands</a>
      <a href="/guides/" class="nav-link">Guides</a>
      <div class="cat-dropdown">
        <span class="nav-link cat-toggle" onclick="toggleCats(event)">Categories <span class="chev">&#9660;</span></span>
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
      <div class="country-pill">&#x1F1EC;&#x1F1E7; UK &#9660;</div>
      <button class="menu-toggle" onclick="toggleNav()" aria-label="Menu">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>
    </div>
  </div>
  <div class="nav-backdrop" onclick="closeNav()"></div>
<script>
function toggleNav(){
  var n=document.querySelector('.header-inner nav');
  var b=document.querySelector('.nav-backdrop');
  var open=n.classList.toggle('open');
  if(b)b.classList.toggle('open',open);
  document.body.style.overflow=open?'hidden':'';
}
function closeNav(){
  var n=document.querySelector('.header-inner nav');
  var b=document.querySelector('.nav-backdrop');
  if(n)n.classList.remove('open');
  if(b)b.classList.remove('open');
  var cd=document.querySelector('.cat-dropdown');if(cd)cd.classList.remove('cat-open');
  document.body.style.overflow='';
}
function toggleCats(e){
  if(window.innerWidth>860)return;
  e.preventDefault();e.stopPropagation();
  e.currentTarget.parentElement.classList.toggle('cat-open');
}
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
        + FOOTER_HTML
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

AWIN_AFFID = "2926769"  # Awin publisher ID (same as AWIN_PUBLISHER_ID but hardcoded for featured card)

# Homepage featured brand cards (the white ".ff" design — styling lives in index.html
# <style>, which update_index() preserves). Data-driven: add a dict here and it shows
# on the homepage automatically. Each card spans 2 grid columns and pins to the top.
#   chips = list of (bold, small) — works for codes (HELLO10 / "10% off") or offers (£8 OFF / "...")
FEATURED_CARDS = [
    {
        "logo": "/images/aatu-logo.jpg", "name": "AATU", "label": "Pet Food", "cat": None,
        "verified": "Verified codes",
        "head": "Up to 30% off RRP + free delivery on your first AATU order",
        "feats": ["80% meat or fish", "No fillers or grains", "Free delivery over £30"],
        "chips": [("HELLO10", "10% off your first order"), ("FIRSTSUB", "30% off your first subscription")],
        "cta": "https://www.awin1.com/cread.php?awinmid=17135&awinaffid=2926769&ued=https%3A%2F%2Fwww.aatu.co.uk%2F",
    },
    {
        "logo": "/images/8wines-logo.jpg", "name": "8WINES", "label": "Wine", "cat": "Groceries",
        "verified": "Verified offers",
        "head": "£8 off your first order + free UK shipping over £400",
        "feats": ["Award-winning wines", "Gold 7 years running", "Delivered across the UK"],
        "chips": [("£8 OFF", "New customers, orders £88+"), ("FREE SHIP", "On orders over £400")],
        "cta": "https://www.awin1.com/cread.php?awinmid=106707&awinaffid=2926769&ued=https%3A%2F%2F8wines.com%2Fwines%3Fam_on_sale%3D1",
    },
    {
        "logo": "/images/bunches-logo.jpg", "name": "BUNCHES", "label": "Flowers", "cat": "Garden & Do It Yourself",
        "verified": "Verified partner",
        "head": "Fresh flowers delivered anywhere in the UK",
        "feats": ["From £20.25", "Letterbox & bouquets", "Trusted UK florist"],
        "chips": [("FROM £20.25", "Flowers by post"), ("UK-WIDE", "Any UK address")],
        "cta": "https://www.awin1.com/cread.php?awinmid=488&awinaffid=2926769&ued=https%3A%2F%2Fwww.bunches.co.uk%2F",
    },
    {
        "logo": "/images/cpp-logo.jpg", "name": "COMPARE PARKING PRICES", "label": "Travel", "cat": "Travel",
        "verified": "Verified partner",
        "head": "Compare UK airport parking & save up to 60% — book ahead this summer",
        "feats": ["All major UK airports", "Meet & Greet, Park & Ride", "Pre-book beats turn-up prices"],
        "chips": [("UP TO 60% OFF", "vs on-the-day prices"), ("ALL UK AIRPORTS", "Heathrow, Gatwick, Manchester +")],
        "cta": "https://www.awin1.com/cread.php?awinmid=118401&awinaffid=2926769&ued=https%3A%2F%2Fwww.compareparkingprices.co.uk%2F",
    },
    {
        "logo": "/images/mysterybox-logo.jpg", "name": "MYSTERY BOX SHOP", "label": "Gifts", "cat": None, "home": True,
        "verified": "Verified partner",
        "head": "Surprise mystery gift boxes — more inside than you pay for",
        "feats": ["Birthday & occasion boxes", "Value beats the price tag", "UK warehouse, fast dispatch"],
        "chips": [("MYSTERY BOXES", "More value than you pay"), ("UK DISPATCH", "Fast UK delivery")],
        "cta": "https://www.awin1.com/cread.php?awinmid=45189&awinaffid=2926769&ued=https%3A%2F%2Fmysteryboxshop.com%2F",
    },
    {
        "logo": "/images/sedley-logo.jpg", "name": "SEDLEY", "label": "Menswear", "cat": "Fashion & Accessories", "home": False,
        "verified": "Verified partner",
        "head": "Well-crafted menswear essentials at excellent value",
        "feats": ["Easy-to-wear staples", "Clothing & footwear", "Excellent value UK brand"],
        "chips": [("MENSWEAR", "Everyday essentials"), ("UK BRAND", "Quality basics")],
        "cta": "https://www.awin1.com/cread.php?awinmid=47609&awinaffid=2926769&ued=https%3A%2F%2Fwww.sedley.com%2F",
    },
    {
        "logo": "/images/morish-logo.jpg", "name": "MORISH", "label": "Healthy Snacks", "cat": "Groceries", "home": False,
        "verified": "Verified partner",
        "head": "Snacks with benefits — high protein, fibre & low-carb",
        "feats": ["No added sugar", "High protein & fibre", "Crispy & moreish"],
        "chips": [("LOW-CARB", "Snacks with benefits"), ("NO ADDED SUGAR", "Guilt-free snacking")],
        "cta": "https://www.awin1.com/cread.php?awinmid=126437&awinaffid=2926769&ued=https%3A%2F%2Fmorishsnacks.co.uk%2F",
    },
    {
        "logo": "/images/brickzonehub-logo.jpg", "name": "BRICKZONEHUB", "label": "LEGO Display", "cat": "Family & Kids", "home": False,
        "verified": "Verified partner",
        "head": "Premium UK display frames & cases for your LEGO collection",
        "feats": ["For Technic, Icons & Speed Champions", "Wall frames & acrylic cases", "Fast UK delivery"],
        "chips": [("LEGO FRAMES", "Show off your builds"), ("UK DELIVERY", "Made for collectors")],
        "cta": "https://www.awin1.com/cread.php?awinmid=121692&awinaffid=2926769&ued=https%3A%2F%2Fbrickzonehub.co.uk%2F",
    },
    {
        "logo": "/images/buymeonce-logo.jpg", "name": "BUY ME ONCE", "label": "Buy It For Life", "cat": "Home & Living", "home": False,
        "verified": "Verified partner",
        "head": "Long-lasting homeware on sale — built to be bought once",
        "feats": ["Durability-tested products", "Kitchen, home & lifestyle", "Sustainable — less waste"],
        "chips": [("SALE ON NOW", "Shop discounted items"), ("BUY IT FOR LIFE", "Built to last")],
        "cta": "https://www.awin1.com/cread.php?awinmid=54235&awinaffid=2926769&ued=https%3A%2F%2Fwww.buymeonce.co.uk%2Fcollections%2Fsale",
    },
]

def render_featured_card(c):
    feats = "".join(f'<span>&#10003; {html.escape(f)}</span>' for f in c["feats"])
    chips = "".join(f'<div class="ff-chip"><b>{html.escape(b)}</b><small>{html.escape(s)}</small></div>'
                    for b, s in c["chips"])
    return (
        '<div class="deal featured ff">'
        f'<div class="ff-brand"><img src="{c["logo"]}" alt="{html.escape(c["name"])} logo">'
        f'<span class="ff-pet">{html.escape(c["label"])}</span></div>'
        '<div class="ff-body">'
        f'<div class="ff-top"><span class="ff-tag">FEATURED</span>'
        f'<span class="ff-verified">&#10003; {html.escape(c["verified"])}</span></div>'
        f'<div class="ff-head">{html.escape(c["head"])}</div>'
        f'<div class="ff-feats">{feats}</div>'
        f'<div class="ff-chips">{chips}</div>'
        f'<a class="ff-cta" href="{c["cta"]}" rel="nofollow sponsored" target="_blank">Shop {html.escape(c["name"])} &rarr;</a>'
        '</div></div>\n'
    )

FEATURED_CARD_HTML = "".join(render_featured_card(c) for c in FEATURED_CARDS)

# Featured-card styling. The homepage carries this inline in index.html; category
# pages need it too when a brand card is injected, so it's defined here for reuse.
FEATURED_CSS = (
    ".deal.featured{grid-column:span 2}\n"
    ".ff{background:#fff;border:1px solid var(--border);flex-direction:row;padding:0;align-items:stretch}\n"
    ".ff .ff-brand{flex:0 0 142px;background:linear-gradient(160deg,#0f172a,#1e3a5f);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:16px;position:relative;overflow:hidden}\n"
    ".ff .ff-brand::before{content:'';position:absolute;inset:0;background:radial-gradient(circle at 50% 25%,rgba(212,175,55,.22),transparent 60%)}\n"
    ".ff .ff-brand img{width:118px;height:64px;object-fit:contain;background:#fff;padding:10px;border-radius:12px;box-shadow:0 6px 18px rgba(0,0,0,.4);position:relative}\n"
    ".ff .ff-pet{font-size:9px;letter-spacing:2px;color:#cbd5e1;font-weight:800;text-transform:uppercase;position:relative}\n"
    ".ff .ff-body{flex:1;padding:15px 18px 12px;display:flex;flex-direction:column;gap:10px;min-width:0}\n"
    ".ff .ff-top{display:flex;align-items:center;gap:10px}\n"
    ".ff .ff-tag{font-size:9px;font-weight:800;letter-spacing:1px;background:#ef4444;color:#fff;padding:4px 9px;border-radius:5px}\n"
    ".ff .ff-verified{font-size:10px;font-weight:800;color:#16a34a;text-transform:uppercase;letter-spacing:.5px}\n"
    ".ff .ff-head{font-family:'Barlow Condensed',sans-serif;font-size:21px;font-weight:800;color:#0f172a;line-height:1.12}\n"
    ".ff .ff-feats{display:flex;flex-wrap:wrap;gap:3px 14px}\n"
    ".ff .ff-feats span{font-size:11px;color:#16a34a;font-weight:700}\n"
    ".ff .ff-chips{display:flex;flex-direction:column;gap:6px;margin-top:auto;margin-bottom:auto}\n"
    ".ff .ff-chip{display:flex;align-items:center;gap:8px;background:#fdf9ec;border:1px solid #ecd9a0;border-radius:7px;padding:7px 11px}\n"
    ".ff .ff-chip b{font-size:13px;font-weight:800;color:#a07820;letter-spacing:1px}\n"
    ".ff .ff-chip small{font-size:11px;color:#64748b;font-weight:600}\n"
    ".ff .ff-cta{display:block;background:#ef4444;color:#fff;font-weight:700;font-size:13px;padding:10px;border-radius:8px;text-decoration:none;text-align:center}\n"
    ".ff .ff-cta:hover{background:#dc2626}\n"
    "@media(max-width:600px){.ff{flex-direction:column}"
    ".ff .ff-brand{flex:0 0 auto;flex-direction:row;justify-content:flex-start;gap:14px;padding:12px 16px}"
    ".ff .ff-brand img{width:96px;height:46px;padding:7px;border-radius:9px}"
    ".ff .ff-pet{font-size:11px}.ff .ff-chips{margin:6px 0}}\n"
)

def update_index(new_deals):
    if os.path.exists('deals'):
        all_files = [f for f in os.listdir('deals') if f.endswith('.html')]
        # Newest first by file mtime (when the bot wrote it), then cap for mobile
        # page weight. Category pages + sitemap iterate the folder separately, so
        # every deal stays discoverable — only the homepage is trimmed.
        all_files.sort(key=lambda f: os.path.getmtime(f'deals/{f}'), reverse=True)
        all_files = all_files[:MAX_HOMEPAGE]
    else:
        all_files = []
    deal_cards = []
    for fname in all_files:
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
        deal_cards.append(build_card(fname, title, img_src, price, merchant, features, shipping))

    # Blend featured brand cards into the deal grid (not stacked at the top).
    # Spread them out: first after a few deals, then evenly apart.
    # Only cards flagged home=True (default) appear on the homepage; the rest are
    # surfaced on their matching category page instead, to keep the homepage lean.
    featured = [render_featured_card(c) for c in FEATURED_CARDS if c.get("home", True)]
    slots = [3 + i * 6 for i in range(len(featured))]  # e.g. positions 3, 9, 15
    out = []
    fi = 0
    for i, dc in enumerate(deal_cards):
        if fi < len(featured) and i == slots[fi]:
            out.append(featured[fi]); fi += 1
        out.append(dc)
    while fi < len(featured):  # any leftovers if there were few deals
        out.append(featured[fi]); fi += 1
    cards = "".join(out)

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
    "Travel": "✈️", "Culture & Leisure": "🎭",
    "Sports & Outdoors": "⚽", "Other": "📦",
}

def cat_slug(cat):
    return re.sub(r'[^a-z0-9]+', '-', cat.lower()).strip('-')

# Unique intro copy per category — gives Google real text to rank for "X deals UK"
CATEGORY_SEO = {
    "Gaming": ("Gaming Deals UK", "The best UK gaming deals today — discounted consoles, PS5 and Xbox games, Nintendo Switch bundles, PC gaming gear and accessories. Every deal is hand-picked from across UK retailers and refreshed daily, so you only see offers that are genuinely live."),
    "Electronics": ("Electronics Deals UK", "Today's best UK electronics deals — laptops, TVs, headphones, smart home tech and more at genuine discounts. We check prices across major UK retailers every day and only list offers that beat the usual price."),
    "Groceries": ("Grocery Deals & Offers UK", "Save on your weekly shop with the best UK grocery deals — supermarket offers, multibuys, household essentials and food cupboard bargains, updated every morning."),
    "Fashion & Accessories": ("Fashion Deals UK", "The best UK fashion deals today — discounted clothing, trainers, watches and accessories from trusted UK retailers. New markdowns added daily."),
    "Health & Beauty": ("Health & Beauty Deals UK", "Today's best UK health and beauty deals — skincare, fragrance, grooming and wellness offers at real discounts, checked and refreshed every day."),
    "Home & Living": ("Home & Living Deals UK", "The best UK home deals today — furniture, kitchen appliances, bedding and homeware at genuine discounts from major UK retailers, updated daily."),
    "Garden & Do It Yourself": ("Garden & DIY Deals UK", "Today's best UK garden and DIY deals — power tools, garden furniture, BBQs and outdoor gear at real discounts, refreshed every morning."),
    "Family & Kids": ("Family & Kids Deals UK", "The best UK deals for families — toys, baby essentials, kids' clothing and days out at genuine discounts, hand-picked and updated daily."),
    "Car & Motorcycle": ("Car & Motorcycle Deals UK", "Today's best UK motoring deals — car accessories, dash cams, tools and motorcycle gear at real discounts from trusted UK retailers."),
    "Broadband & Phone Contracts": ("Broadband & Phone Deals UK", "Compare today's best UK broadband and phone contract deals — SIM-only offers, fibre broadband discounts and handset bundles, updated daily."),
    "Services & Contracts": ("Services & Contracts Deals UK", "The best UK deals on services and contracts — insurance, subscriptions, utilities and more, checked daily so you never overpay."),
    "Travel": ("Travel Deals UK", "Today's best UK travel deals — airport parking, hotels, flights, city breaks and holiday extras at genuine discounts. Compare and book ahead to save, updated daily."),
    "Culture & Leisure": ("Culture & Leisure Deals UK", "The best UK deals on days out, cinema, attractions, events and experiences — hand-picked and refreshed every day."),
    "Sports & Outdoors": ("Sports & Outdoors Deals UK", "Today's best UK sports and outdoor deals — fitness gear, camping, cycling, activewear and equipment at real discounts, updated daily."),
}

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

        # Inject any featured brand card whose "cat" matches, pinned to the top of the grid.
        feat_for_cat = "".join(render_featured_card(c) for c in FEATURED_CARDS if c.get("cat") == cat)
        cards = feat_for_cat + cards

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
            "#deals{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px;grid-auto-flow:row dense}\n"
            "@media(max-width:600px){#deals{grid-template-columns:repeat(2,1fr);gap:10px}}\n"
            + FEATURED_CSS +
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
        seo_title, seo_intro = CATEGORY_SEO.get(cat, (f"{cat} Deals UK", f"The best UK {cat} deals today, hand-picked and updated daily."))
        today = time.strftime("%-d %B %Y")
        n = len(deals)
        meta_desc = f"{seo_intro[:120].rsplit(' ',1)[0]}… {n} live deals today." if n else seo_intro[:150]
        itemlist = json.dumps({
            "@context": "https://schema.org", "@type": "ItemList",
            "name": seo_title, "numberOfItems": n,
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": f"https://invisuale.com/deals/{d[0]}", "name": d[1]}
                for i, d in enumerate(deals[:30])
            ]})
        breadcrumb = json.dumps({
            "@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://invisuale.com/"},
                {"@type": "ListItem", "position": 2, "name": "Categories", "item": "https://invisuale.com/categories/"},
                {"@type": "ListItem", "position": 3, "name": cat, "item": f"https://invisuale.com/categories/{slug}.html"},
            ]})
        page = (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
            '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<title>{html.escape(seo_title)} — Today\'s Best Offers | Invisuale</title>\n'
            f'<meta name="description" content="{html.escape(meta_desc)}">\n'
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
            f'<link rel="canonical" href="https://invisuale.com/categories/{slug}.html">\n'
            f'<script type="application/ld+json">{itemlist}</script>\n'
            f'<script type="application/ld+json">{breadcrumb}</script>\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">\n'
            '<style>' + cat_css +
            ".cat-intro{max-width:760px;margin:0 auto;color:#cbd5e1;font-size:14px;line-height:1.6;margin-top:12px}\n"
            '</style>\n' + ANALYTICS +
            '</head>\n<body>\n'
            + HEADER_HTML +
            f'\n<div class="page-hero"><h1>{icon} {html.escape(seo_title)}</h1>'
            f'<p>{n} live deals · Updated {today}</p>'
            f'<p class="cat-intro">{html.escape(seo_intro)}</p></div>\n'
            f'<main><div id="deals">{cards}</div></main>\n'
            + FOOTER_HTML +
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
        '<title>UK Deals by Category — Gaming, Electronics, Groceries & More | Invisuale</title>\n'
        '<meta name="description" content="Browse today\'s best UK deals by category — Gaming, Electronics, Groceries, Fashion, Home and more. Hand-picked offers updated every day.">\n'
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">\n'
        '<link rel="canonical" href="https://invisuale.com/categories/">\n'
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
.fp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}
.fp-card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;text-decoration:none;color:#1e293b;display:flex;flex-direction:column;transition:transform .15s,box-shadow .15s}
.fp-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.08)}
.fp-img{background:#f8f9fa;height:160px;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:1px solid #e2e8f0;padding:10px}
.fp-img img{max-width:100%;max-height:100%;object-fit:contain;mix-blend-mode:multiply}
.fp-ph{font-size:28px;color:#cbd5e1}
.fp-name{padding:10px 12px 4px;font-size:12px;font-weight:700;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:50px}
.fp-price{padding:0 12px 12px;font-size:15px;font-weight:800;color:#ef4444}
.fp-price s{color:#94a3b8;font-weight:600;font-size:11px;margin-left:6px}
@media(max-width:600px){.fp-grid{grid-template-columns:repeat(2,1fr);gap:10px}.fp-img{height:120px}.fp-name{font-size:11px}}
.codes-grid{display:flex;flex-direction:column;gap:16px;margin-bottom:24px}
.code-card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;display:flex;align-items:stretch}
.code-card .accent{width:6px;background:linear-gradient(180deg,#d4af37,#a07820);flex-shrink:0}
.code-card .body{padding:20px 22px;flex:1}
.code-type{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:#16a34a;margin-bottom:6px}
.code-title{font-family:'Barlow Condensed',sans-serif;font-size:22px;font-weight:800;color:#0f172a;line-height:1.2;margin-bottom:8px}
.code-desc{font-size:13px;color:#475569;line-height:1.55;margin-bottom:14px}
.code-box-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.code-box{background:#fefce8;border:2px dashed #d4af37;border-radius:8px;padding:10px 18px;font-size:18px;font-weight:800;color:#92400e;letter-spacing:2px;user-select:all}
.copy-btn{background:#ef4444;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-weight:800;font-size:13px;cursor:pointer;transition:background .15s}
.copy-btn:hover{background:#dc2626}
.shop-btn{display:inline-block;margin-top:16px;background:#0f172a;color:#fff;padding:11px 22px;border-radius:8px;font-weight:800;font-size:14px;text-decoration:none}
.shop-btn:hover{background:#1e293b}
@media(max-width:600px){.code-box-row{flex-direction:column;align-items:flex-start}}
footer{background:#0f172a;color:#64748b;text-align:center;padding:28px 24px;font-size:13px;font-weight:600;margin-top:48px}
footer a{color:#94a3b8;text-decoration:none;margin:0 8px}
"""
    copy_js = ("<script>function copyCode(c,b){navigator.clipboard.writeText(c).then(function(){"
               "var x=document.getElementById(b),o=x.textContent;x.textContent='Copied!';"
               "x.style.background='#16a34a';setTimeout(function(){x.textContent=o;x.style.background='';},2000);});}</script>")
    footer_html = FOOTER_HTML

    # --- Per-brand pages ---
    for m in merchants:
        mc = lookup_codes(m["name"])
        slug_b = (mc["slug"] if mc else re.sub(r'[^a-z0-9]+', '-', m["name"].lower()).strip('-'))
        # If MERCHANT_CODES entry supplies offers_url, deeplink wraps the sale page
        # (not the homepage) — buyers land directly on the live discounts.
        if mc and mc.get("offers_url"):
            cta_link = f"https://www.awin1.com/cread.php?awinmid={m['id']}&awinaffid={AWIN_PUBLISHER_ID}&ued={urllib.parse.quote(mc['offers_url'], safe='')}"
        else:
            cta_link = m["deeplink"]
        # Build offers/codes block. Codes have a copy button; offers don't (they
        # apply automatically via our affiliate link).
        codes_block = ""
        cc = ""
        if mc and mc.get("codes"):
            for i, (code, short, desc) in enumerate(mc["codes"]):
                bid = f"cd{i}"
                cc += (
                    '<div class="code-card"><div class="accent"></div><div class="body">'
                    '<div class="code-type">✅ Verified Code</div>'
                    f'<div class="code-title">{html.escape(short)}</div>'
                    f'<div class="code-desc">{html.escape(desc)} Use code <strong>{html.escape(code)}</strong> at checkout.</div>'
                    '<div class="code-box-row">'
                    f'<span class="code-box" id="{bid}">{html.escape(code)}</span>'
                    f'<button class="copy-btn" onclick="copyCode(\'{html.escape(code)}\',\'{bid}\')">Copy Code</button>'
                    '</div>'
                    f'<a href="{cta_link}" class="shop-btn" rel="nofollow sponsored" target="_blank">Shop {html.escape(m["name"])} →</a>'
                    '</div></div>'
                )
        if mc and mc.get("offers"):
            for title, desc in mc["offers"]:
                cc += (
                    '<div class="code-card"><div class="accent"></div><div class="body">'
                    '<div class="code-type">🎁 Verified Offer</div>'
                    f'<div class="code-title">{html.escape(title)}</div>'
                    f'<div class="code-desc">{html.escape(desc)}</div>'
                    f'<a href="{cta_link}" class="shop-btn" rel="nofollow sponsored" target="_blank">Shop {html.escape(m["name"])} →</a>'
                    '</div></div>'
                )
        if cc:
            codes_block = f'<div class="codes-grid">{cc}</div>'
        stats = ""
        # Only buyer-friendly facts (set in MERCHANT_CODES) are ever shown.
        # Seller-side KPIs from the Awin API (approval rate, EPC, commission,
        # sector) are intentionally suppressed — they're for the publisher,
        # not the customer.
        if mc and mc.get("facts"):
            for strong, span in mc["facts"]:
                stats += f'<div class="stat"><strong>{html.escape(strong)}</strong><span>{html.escape(span)}</span></div>'

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
            + ((lambda lg: f'<img src="{html.escape(lg)}" alt="{html.escape(m["name"])} logo">' if lg else '')(m.get("logo") or local_logo(m["name"])))
            + f'<div class="info"><h1>{html.escape(m["name"])}</h1>'
            + (f'<p>{html.escape(m["description"])}</p>' if m.get("description") else "")
            + f'<div class="cta-row"><a href="{cta_link}" class="btn-primary" rel="nofollow sponsored" target="_blank">Visit {html.escape(m["name"])} →</a>'
            + (f'<a href="{html.escape(m["displayUrl"])}" class="btn-secondary" target="_blank">Direct site</a>' if m.get("displayUrl") else "")
            + '</div></div></div>'
            + (f'<div class="stats">{stats}</div>' if stats else "")
            + codes_block
            + render_feed_products(m["name"], products_for_merchant(m["id"]))
            + '<div class="seo-block"><h2>About this offer page</h2>'
            + (f'<p>The codes above are verified through our official {html.escape(m["name"])} Awin partnership. We only list codes confirmed by the merchant — no fake or expired codes.</p>'
               if mc else
               f'<p>This page links directly to {html.escape(m["name"])}\'s current offers via our verified Awin partnership. Rather than listing individual codes that often expire within hours, we send you straight to {html.escape(m["name"])}\'s official sale and offers pages where the live discounts are guaranteed to work.</p>')
            + f'<p>Browse <a href="/codes/">all our retailer offer pages</a>, check today\'s <a href="/">hot deals</a>, or read our <a href="/guides/spot-a-real-deal-vs-fake-discount.html">guide on spotting fake discounts</a>.</p>'
            + '</div></main>' + footer_html + copy_js + '</body></html>'
        )
        with open(f"codes/{slug_b}.html", "w") as f: f.write(page)

    # --- Codes index page ---
    cards = ""
    for m in sorted(merchants, key=lambda x: x["name"].lower()):
        mc = lookup_codes(m["name"])
        slug_b = (mc["slug"] if mc else re.sub(r'[^a-z0-9]+', '-', m["name"].lower()).strip('-'))
        lg = m.get("logo") or local_logo(m["name"])
        logo = (f'<img src="{html.escape(lg)}" alt="{html.escape(m["name"])} logo" class="brand-logo" loading="lazy">' if lg
                else '<div class="brand-logo" style="display:flex;align-items:center;justify-content:center;background:#f1f5f9;border-radius:8px;font-weight:800;color:#475569">' + html.escape(m["name"][:2].upper()) + '</div>')
        if mc and mc.get("codes"):
            n = len(mc["codes"])
            meta = f'{n} Code{"s" if n != 1 else ""} Available'
            comm = f'<div class="brand-comm">{" · ".join(c[0] for c in mc["codes"])}</div>'
        elif mc and mc.get("offers_url"):
            meta = "Live Sale Page"
            comm = '<div class="brand-comm">Direct to discounted products</div>'
        else:
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

# Per-merchant editorial guide content. FACTUAL only — what they sell, how to save,
# honest FAQs. NO invented reviews/ratings/testimonials. Keyed by the FEATURED_CARDS
# "name". Pages target buyer-intent queries like "<brand> discount code uk".
BRAND_GUIDES = {
    "AATU": {
        "intro": "AATU is a British premium pet food brand built on a simple idea: one main animal protein, packed with meat or fish and free from the fillers, grains and additives found in many supermarket brands.",
        "about": "AATU makes dry and wet food for both dogs and cats, using an 80/20 recipe — around 80% meat or fish — with no wheat, no soya and no artificial colours or flavours. The single-protein approach makes it a popular choice for pets with sensitive stomachs or allergies.",
        "save": "New customers can use code <b>HELLO10</b> for 10% off a first order, or <b>FIRSTSUB</b> for 30% off a first Subscribe &amp; Save order. Delivery is free on orders over £30.",
        "faqs": [("Is AATU good quality pet food?", "AATU uses around 80% meat or fish with no grains or fillers and a single main protein, which puts it in the premium tier of UK pet food."),
                 ("How do I get an AATU discount?", "Use HELLO10 for 10% off your first order, or FIRSTSUB for 30% off your first subscription order."),
                 ("Does AATU offer free delivery?", "Yes — delivery is free on orders over £30.")],
    },
    "8WINES": {
        "intro": "8wines is an award-winning online wine merchant shipping a curated range across the UK, with a focus on quality bottles from established and boutique producers.",
        "about": "8wines stocks a broad range of red, white, rosé, sparkling and fine wines, and has been recognised with industry Gold awards multiple years running. Orders are delivered across the UK.",
        "save": "New customers get <b>£8 off</b> their first order over £88, applied through our link with no code needed, and <b>free UK shipping</b> on orders over £400. Their live sale page lists current reductions.",
        "faqs": [("Is 8wines a reputable wine retailer?", "Yes — 8wines is an established UK merchant that has won industry Gold awards several years running."),
                 ("How do I save at 8wines?", "New customers get £8 off a first order over £88 via our link, plus free UK shipping over £400. Their sale page shows further reductions."),
                 ("Does 8wines deliver across the UK?", "Yes, 8wines delivers nationwide across the UK.")],
    },
    "BUNCHES": {
        "intro": "Bunches is a long-established UK florist delivering fresh flowers by post and hand-tied bouquets to any address in the country.",
        "about": "Bunches offers letterbox flowers, bouquets and gift sets for occasions like birthdays, anniversaries and sympathy, with prices starting from around £20.25 and UK-wide delivery.",
        "save": "Browse current bouquets and gift offers through our link. Bunches runs seasonal promotions around key dates such as Mother's Day, Valentine's and Christmas.",
        "faqs": [("Does Bunches deliver anywhere in the UK?", "Yes — Bunches delivers fresh flowers to any UK address, including letterbox flowers and bouquets."),
                 ("How much are flowers from Bunches?", "Prices start from around £20.25 depending on the bouquet or gift set."),
                 ("Is Bunches a trusted florist?", "Bunches is a long-established UK flowers-by-post specialist.")],
    },
    "COMPARE PARKING PRICES": {
        "intro": "Compare Parking Prices is a UK airport parking comparison service that checks trusted providers across all major airports so you can book the best-value option in advance.",
        "about": "The service compares Meet &amp; Greet, Park &amp; Ride and on-site parking at airports including Heathrow, Gatwick, Manchester, Stansted and Luton. Booking ahead is almost always cheaper than turning up on the day.",
        "save": "You can save up to <b>60%</b> versus on-the-day prices by pre-booking through the comparison. Summer holiday periods are when booking early matters most, as spaces fill and prices rise.",
        "faqs": [("How much can I save on airport parking?", "Pre-booking through a comparison can save up to 60% compared with turning up and paying on the day."),
                 ("Which airports are covered?", "All major UK airports including Heathrow, Gatwick, Manchester, Stansted and Luton."),
                 ("What's the difference between Meet & Greet and Park & Ride?", "With Meet & Greet a driver parks your car for you at the terminal; with Park & Ride you park yourself and take a transfer bus.")],
    },
    "MYSTERY BOX SHOP": {
        "intro": "Mystery Box Shop sells surprise gift boxes for birthdays and special occasions, where the contents are worth more than the price you pay.",
        "about": "Based around a large UK warehouse, Mystery Box Shop curates themed boxes of surprise items dispatched quickly across the UK — a popular gifting option when you want something fun and better value than the sticker price.",
        "save": "Current mystery boxes and bundles are listed through our link, with the value of contents designed to exceed what you pay.",
        "faqs": [("What is a mystery box?", "A mystery box is a sealed gift box of surprise items, curated so the total value is higher than the price you pay."),
                 ("Does Mystery Box Shop deliver fast?", "Yes — boxes are dispatched quickly from a UK warehouse."),
                 ("Are mystery boxes good value?", "They're designed so the contents are worth more than the box price, which is the main appeal.")],
    },
    "SEDLEY": {
        "intro": "Sedley is a UK menswear brand focused on well-crafted, easy-to-wear essentials that offer strong value for everyday wardrobes.",
        "about": "Sedley makes men's clothing and footwear designed as versatile staples — pieces that work across smart and casual occasions without a premium price tag.",
        "save": "Browse the current Sedley range through our link; the brand positions itself around value, with seasonal reductions across clothing and footwear.",
        "faqs": [("What does Sedley sell?", "Sedley sells men's clothing and footwear — everyday wardrobe essentials."),
                 ("Is Sedley good value?", "Sedley positions itself around well-made essentials at accessible prices."),
                 ("Where is Sedley based?", "Sedley is a UK menswear brand.")],
    },
    "MORISH": {
        "intro": "Morish makes healthier snacks designed to give you the moreish taste of a treat without the sugar — high in protein and fibre, and low in carbs.",
        "about": "Morish snacks are positioned as 'snacks with benefits': no added sugar, higher protein and fibre, and a crispy, savoury texture aimed at people wanting smarter snacking for themselves or their families.",
        "save": "Browse the current Morish range and bundles through our link. Multi-pack bundles are typically the best value per pack.",
        "faqs": [("Are Morish snacks healthy?", "Morish snacks are made with no added sugar and are higher in protein and fibre than typical crisps or sweets."),
                 ("Are Morish snacks low carb?", "Yes — they're formulated to be lower in carbohydrates."),
                 ("What's the best way to buy Morish?", "Multi-pack bundles usually give the best price per pack.")],
    },
    "BRICKZONEHUB": {
        "intro": "brickzonehub makes premium UK display frames and acrylic cases that protect and show off built LEGO sets.",
        "about": "Designed for collectors, brickzonehub offers wall-mounted frames and clear display cases sized for popular ranges including LEGO Technic, Icons and Speed Champions, with fast UK delivery.",
        "save": "Browse the current frame and case range through our link. Sizes are matched to specific LEGO sets, so check the set compatibility before ordering.",
        "faqs": [("What does brickzonehub sell?", "Premium display frames and acrylic cases for built LEGO sets, made in the UK."),
                 ("Which LEGO sets are supported?", "Ranges including LEGO Technic, Icons and Speed Champions, with sizes matched to specific sets."),
                 ("Does brickzonehub deliver in the UK?", "Yes — brickzonehub offers fast UK delivery.")],
    },
    "BUY ME ONCE": {
        "intro": "Buy Me Once is a UK retailer built on a 'buy it for life' philosophy — long-lasting homeware and goods chosen for durability so you replace them less often.",
        "about": "Buy Me Once curates kitchen, home and lifestyle products that are durability-tested to last, with a focus on reducing waste by buying better-quality items once rather than cheap items repeatedly.",
        "save": "Buy Me Once runs a dedicated <b>sale section</b> — our link points straight to it so you land on the discounted long-life products.",
        "faqs": [("What is Buy Me Once?", "A UK retailer that curates long-lasting, durability-tested homeware and goods designed to be bought once."),
                 ("Does Buy Me Once have a sale?", "Yes — it has a dedicated sale section, which our link points to directly."),
                 ("Why buy from Buy Me Once?", "The focus is durability and less waste — better-quality items that don't need frequent replacing.")],
    },
}

# Proper-cased display names (FEATURED_CARDS "name" is uppercase for the card design)
BRAND_DISPLAY = {
    "AATU": "AATU", "8WINES": "8wines", "BUNCHES": "Bunches",
    "COMPARE PARKING PRICES": "Compare Parking Prices", "MYSTERY BOX SHOP": "Mystery Box Shop",
    "SEDLEY": "Sedley", "MORISH": "Morish", "BRICKZONEHUB": "brickzonehub", "BUY ME ONCE": "Buy Me Once",
}

def make_brand_pages():
    """Generate /brands/ index + /brands/<slug>.html editorial guide per featured
    merchant. Factual buyer-intent content with FAQPage + Breadcrumb schema."""
    os.makedirs("brands", exist_ok=True)
    def bslug(n): return re.sub(r'[^a-z0-9]+', '-', n.lower()).strip('-')
    def disp(n): return BRAND_DISPLAY.get(n, n.title() if n.isupper() else n)
    css = (
        "*{box-sizing:border-box;margin:0;padding:0}\n"
        ":root{--navy:#0f172a;--red:#ef4444;--bg:#f4f4f4;--white:#fff;--text:#1e293b;--muted:#64748b;--border:#e2e8f0;--green:#16a34a}\n"
        "body{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:var(--text)}\n"
        + HEADER_CSS +
        ".page-hero{background:linear-gradient(135deg,var(--navy),#1e3a5f);padding:30px 24px;text-align:center}\n"
        ".page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(28px,5vw,46px);font-weight:800;color:#fff;line-height:1.1}\n"
        ".page-hero p{color:#94a3b8;font-size:14px;margin-top:8px;font-weight:600}\n"
        "main{max-width:820px;margin:0 auto;padding:28px 22px 60px}\n"
        ".brand-head{display:flex;align-items:center;gap:18px;background:var(--white);border:1px solid var(--border);border-radius:14px;padding:20px;margin-bottom:22px}\n"
        ".brand-head img{width:120px;height:66px;object-fit:contain;background:#fff;border:1px solid var(--border);border-radius:10px;padding:8px;flex-shrink:0}\n"
        ".brand-head .bh-label{font-size:11px;font-weight:800;letter-spacing:1px;color:var(--red);text-transform:uppercase}\n"
        ".brand-head h2{font-size:15px;color:var(--muted);font-weight:700;margin-top:4px}\n"
        ".cta-btn{display:inline-block;background:var(--red);color:#fff;font-weight:800;font-size:15px;padding:13px 26px;border-radius:10px;text-decoration:none;margin:6px 0 4px}\n"
        ".cta-wrap{text-align:center;background:var(--white);border:1px solid var(--border);border-radius:14px;padding:22px;margin:24px 0}\n"
        ".cta-wrap .feats{display:flex;flex-wrap:wrap;gap:6px 16px;justify-content:center;margin-top:10px}\n"
        ".cta-wrap .feats span{font-size:12px;color:var(--green);font-weight:700}\n"
        "article h3{font-family:'Barlow Condensed',sans-serif;font-size:24px;font-weight:800;color:var(--navy);margin:26px 0 10px}\n"
        "article p{font-size:15px;line-height:1.7;color:#334155;margin-bottom:12px}\n"
        ".faq{background:var(--white);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:10px}\n"
        ".faq b{display:block;font-size:15px;color:var(--navy);margin-bottom:5px}\n"
        ".faq span{font-size:14px;color:#475569;line-height:1.6}\n"
        ".xref{font-size:13px;color:var(--muted);margin-top:24px}\n"
        ".xref a{color:var(--red);font-weight:700;text-decoration:none}\n"
        ".disc{font-size:11px;color:#94a3b8;margin-top:18px;line-height:1.5}\n"
        "footer a{color:#94a3b8}\n"
    )
    built = []
    for c in FEATURED_CARDS:
        g = BRAND_GUIDES.get(c["name"])
        if not g: continue
        s = bslug(c["name"]); name = disp(c["name"])
        built.append((s, name, c))
        title_disp = name
        feats = "".join(f'<span>&#10003; {html.escape(f)}</span>' for f in c["feats"])
        faq_html = "".join(f'<div class="faq"><b>{html.escape(q)}</b><span>{a}</span></div>' for q, a in g["faqs"])
        faq_ld = json.dumps({"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
            {"@type":"Question","name":q,"acceptedAnswer":{"@type":"Answer","text":re.sub('<[^>]+>','',a)}} for q,a in g["faqs"]]})
        crumb_ld = json.dumps({"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
            {"@type":"ListItem","position":1,"name":"Home","item":"https://invisuale.com/"},
            {"@type":"ListItem","position":2,"name":"Brands","item":"https://invisuale.com/brands/"},
            {"@type":"ListItem","position":3,"name":title_disp,"item":f"https://invisuale.com/brands/{s}.html"}]})
        cat_xref = (f'<a href="/categories/{cat_slug(c["cat"])}.html">{html.escape(c["cat"])} deals</a>' if c.get("cat") else '<a href="/">today\'s deals</a>')
        meta_desc = f"{title_disp} UK guide: what they sell, how to save and FAQs. {re.sub('<[^>]+>','',g['save'])[:90]}"
        page = (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title_disp)} UK — Offers, Discounts &amp; Buyer\'s Guide | Invisuale</title>'
            f'<meta name="description" content="{html.escape(meta_desc)}">'
            f'<link rel="canonical" href="https://invisuale.com/brands/{s}.html">'
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
            f'<meta property="og:title" content="{html.escape(title_disp)} UK — Offers &amp; Buyer\'s Guide">'
            f'<meta property="og:description" content="{html.escape(meta_desc)}"><meta property="og:type" content="article">'
            f'<script type="application/ld+json">{faq_ld}</script>'
            f'<script type="application/ld+json">{crumb_ld}</script>'
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">'
            '<style>' + css + '</style>' + ANALYTICS +
            '</head><body>' + HEADER_HTML +
            f'<div class="page-hero"><h1>{html.escape(title_disp)} — Offers &amp; Buyer\'s Guide</h1>'
            f'<p>What they sell, how to save, and the questions people ask — updated regularly</p></div>'
            '<main>'
            f'<div class="brand-head"><img src="{c["logo"]}" alt="{html.escape(title_disp)} logo">'
            f'<div><div class="bh-label">{html.escape(c["label"])}</div><h2>{html.escape(c["head"])}</h2></div></div>'
            f'<article>'
            f'<p>{g["intro"]}</p>'
            f'<h3>About {html.escape(title_disp)}</h3><p>{g["about"]}</p>'
            f'<h3>How to save at {html.escape(title_disp)}</h3><p>{g["save"]}</p>'
            f'<div class="cta-wrap"><a class="cta-btn" href="{c["cta"]}" rel="nofollow sponsored" target="_blank">Visit {html.escape(title_disp)} &rarr;</a>'
            f'<div class="feats">{feats}</div></div>'
            f'<h3>{html.escape(title_disp)} FAQs</h3>{faq_html}'
            f'<p class="xref">See more in {cat_xref} &middot; <a href="/codes/">UK discount codes</a> &middot; <a href="/brands/">all brands</a></p>'
            '<p class="disc">Invisuale may earn a commission when you buy through links on this page, at no extra cost to you. Information is provided as a guide and prices/offers may change — always check the retailer\'s site for current details.</p>'
            '</article></main>' + FOOTER_HTML + '</body></html>'
        )
        with open(f"brands/{s}.html", "w") as f: f.write(page)

    # Brands index
    grid = ""
    for s, name, c in sorted(built, key=lambda x: x[1].lower()):
        grid += (f'<a href="/brands/{s}.html" class="bcard"><img src="{c["logo"]}" alt="{html.escape(name)} logo" loading="lazy">'
                 f'<span class="bn">{html.escape(name)}</span><span class="bl">{html.escape(c["label"])}</span></a>')
    idx_css = (
        "*{box-sizing:border-box;margin:0;padding:0}\n"
        ":root{--navy:#0f172a;--red:#ef4444;--bg:#f4f4f4;--white:#fff;--border:#e2e8f0;--muted:#64748b}\n"
        "body{font-family:'Nunito Sans',sans-serif;background:var(--bg);color:#1e293b}\n"
        + HEADER_CSS +
        ".page-hero{background:linear-gradient(135deg,var(--navy),#1e3a5f);padding:30px 24px;text-align:center}\n"
        ".page-hero h1{font-family:'Barlow Condensed',sans-serif;font-size:clamp(28px,5vw,46px);font-weight:800;color:#fff}\n"
        ".page-hero p{color:#94a3b8;font-size:14px;margin-top:8px;font-weight:600}\n"
        "main{max-width:1000px;margin:0 auto;padding:30px 22px 60px}\n"
        ".bgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}\n"
        ".bcard{background:var(--white);border:1px solid var(--border);border-radius:12px;padding:20px 16px;display:flex;flex-direction:column;align-items:center;gap:8px;text-decoration:none;transition:transform .15s,box-shadow .15s;box-shadow:0 1px 4px rgba(0,0,0,.07)}\n"
        ".bcard:hover{transform:translateY(-3px);box-shadow:0 8px 22px rgba(0,0,0,.12)}\n"
        ".bcard img{width:130px;height:54px;object-fit:contain}\n"
        ".bn{font-size:14px;font-weight:800;color:#1e293b;text-align:center}\n"
        ".bl{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.5px}\n"
        "footer a{color:#94a3b8}\n"
    )
    idx = (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>UK Brand Guides — Offers, Discounts &amp; Buyer\'s Guides | Invisuale</title>'
        '<meta name="description" content="Honest UK buyer\'s guides for trusted retailers — what they sell, how to save and FAQs. AATU, 8wines, Buy Me Once and more.">'
        '<link rel="canonical" href="https://invisuale.com/brands/">'
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@700;800&family=Nunito+Sans:wght@400;600;700&display=swap" rel="stylesheet">'
        '<style>' + idx_css + '</style>' + ANALYTICS +
        '</head><body>' + HEADER_HTML +
        '<div class="page-hero"><h1>Brand Guides</h1><p>Honest guides to trusted UK retailers — what they sell and how to save</p></div>'
        f'<main><div class="bgrid">{grid}</div></main>' + FOOTER_HTML + '</body></html>'
    )
    with open("brands/index.html", "w") as f: f.write(idx)
    print(f"Built {len(built)} brand guide pages.")

def make_sitemap():
    cat_pages = [f'categories/{f}' for f in os.listdir('categories') if f.endswith('.html')] if os.path.exists('categories') else []
    brand_pages = [f'brands/{f}' for f in os.listdir('brands') if f.endswith('.html')] if os.path.exists('brands') else []
    guide_pages = [f'guides/{f}' for f in os.listdir('guides') if f.endswith('.html')] if os.path.exists('guides') else []
    code_pages = [f'codes/{f}' for f in os.listdir('codes') if f.endswith('.html') and f != 'index.html'] if os.path.exists('codes') else []
    static_pages = ['', 'about.html', 'privacy.html', 'discount-codes.html', 'guides/', 'codes/', 'brands/']
    pages = static_pages + [f'deals/{f}' for f in os.listdir('deals') if f.endswith('.html')] + cat_pages + [g for g in guide_pages if g != 'guides/index.html'] + code_pages + [b for b in brand_pages if b != 'brands/index.html']
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
        if not deal.get("image"):
            print(f"skip (no image): {deal['title'][:50]}")
            posted.add(did)
            continue
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
    make_brand_pages()
    make_sitemap()
    save_posted(posted)
    print(f"Done. {count} deals added.")

if __name__ == "__main__":
    main()
