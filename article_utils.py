#!/usr/bin/env python3
"""
Article generation pipeline for CursedTours.com (v3 — hardened).

Four-stage pipeline:

  1. QC LAYER       — 20+ checks: SEO, linking, content quality, schema readiness
  2. EDITORIAL LAYER — Auto-fixes: titles, excerpts, slugs, links, encoding, HTML
  3. FINAL QC        — Re-validates everything after fixes
  4. WRITE LAYER     — Writes files only after clean QC

Usage:

    from article_utils import Article, publish_articles

    articles = [Article(...), Article(...), ...]
    publish_articles(articles, hub_url="/chicago-ghost-tours/")

CLI:
    python3 article_utils.py             # audit existing articles
    python3 article_utils.py --repair    # fix hub links in existing articles
    python3 article_utils.py --fix-all   # repair hub links + mojibake + ext links
"""

import json, os, re, sys
from dataclasses import dataclass, field

# ─── Constants ───────────────────────────────────────────────────────────────

BRAND_SUFFIX = " | Cursed Tours"
MAX_TITLE_RAW = 50
MAX_RENDERED_TITLE = 65
MIN_TITLE = 10
MAX_EXCERPT = 160
MIN_EXCERPT = 50
MIN_WORD_COUNT_CLUSTER = 500
MIN_WORD_COUNT_PILLAR = 1200
MIN_BODY_INTERNAL_LINKS = 2
MIN_CONTINUE_READING_LINKS = 3
ARTICLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "data", "articles")

# Hub page mapping — must match CATEGORIES in src/data/articles.ts
CATEGORY_HUBS = {
    'salem-witch-trials': '/salem-ghost-tours/',
    'new-orleans-voodoo-haunted-history': '/new-orleans-ghost-tours/',
    'chicago-haunted-history': '/chicago-ghost-tours/',
    'dracula-gothic-literature': '/destinations/draculas-castle/',
    'tour-planning': None,
}

# Mojibake fix map (UTF-8 bytes misinterpreted as Windows-1252)
MOJIBAKE_MAP = {
    '\u00c3\u00a9': 'é', '\u00c3\u00a8': 'è', '\u00c3\u00a0': 'à',
    '\u00c3\u00a2': 'â', '\u00c3\u00af': 'ï', '\u00c3\u00b4': 'ô',
    '\u00c3\u00bc': 'ü', '\u00c3\u00b1': 'ñ', '\u00c3\u00a7': 'ç',
    '\u00c3\u00ad': 'í', '\u00c3\u00ab': 'ë', '\u00c3\u00ae': 'î',
    '\u00c3\u2030': 'É', '\u00c3\u0089': 'É',
    '\u00e2\u20ac\u201c': '—', '\u00e2\u20ac\u2122': '\u2019',
    '\u00e2\u20ac\u0153': '\u201c', '\u00e2\u20ac\u00a6': '…',
}


# ─── Data class ──────────────────────────────────────────────────────────────

@dataclass
class Article:
    title: str
    slug: str
    excerpt: str
    category_slug: str
    category_name: str
    image_url: str
    image_alt: str
    content: str
    category_description: str = ""
    category_id: int = 0
    article_id: int = 0
    date: str = "2026-02-12 12:00:00"
    article_type: str = "cluster"  # "cluster" (500w min) or "pillar" (1200w min)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _split_continue_reading(content):
    """Split content into (body, cr_section) or (content, None)."""
    pattern = r'(\s*<hr\s*/?>[\s\n]*<h3>Continue Reading</h3>[\s\n]*<ul>.*?</ul>)\s*$'
    m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if m:
        return content[:m.start()], m.group(1)
    return content, None


def _extract_cr_links(cr_html):
    if not cr_html:
        return []
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', cr_html)


def _build_continue_reading(links):
    items = "\n".join(f'<li><a href="{url}">{text}</a></li>' for url, text in links)
    return f'\n\n<hr />\n\n<h3>Continue Reading</h3>\n<ul>\n{items}\n</ul>'


def _get_hub_url(art, explicit_hub=None):
    if explicit_hub:
        return explicit_hub
    return CATEGORY_HUBS.get(art.category_slug)


def _clean_word_count(content):
    text = re.sub(r'<[^>]+>', ' ', content)
    return len(text.split())


def _has_mojibake(text):
    """Detect common mojibake patterns."""
    for bad in MOJIBAKE_MAP:
        if bad in text:
            return True
    return False


def _build_valid_urls():
    """Build set of all valid internal URL paths from the project."""
    valid = set()
    valid.add('/')
    valid.add('/articles/')
    valid.add('/destinations/')
    valid.add('/experiences/')

    # Articles
    if os.path.isdir(ARTICLE_DIR):
        for f in os.listdir(ARTICLE_DIR):
            if f.endswith('.json'):
                d = json.load(open(os.path.join(ARTICLE_DIR, f)))
                valid.add(f'/articles/{d["slug"]}/')

    # City hubs
    base = os.path.dirname(ARTICLE_DIR)  # src/data
    pages_dir = os.path.join(os.path.dirname(base), 'pages')  # src/pages
    for fname in os.listdir(pages_dir):
        if fname.endswith('-ghost-tours.astro'):
            slug = fname.replace('.astro', '')
            valid.add(f'/{slug}/')

    # Destinations (from data file)
    dest_file = os.path.join(base, 'destinations.ts')
    if os.path.exists(dest_file):
        with open(dest_file) as f:
            dest_content = f.read()
        for m in re.findall(r"'([a-z0-9-]+)':\s*\{", dest_content):
            valid.add(f'/destinations/{m}/')

    # Category pages
    for cat in CATEGORY_HUBS:
        valid.add(f'/articles/category/{cat}/')

    # Experience pages
    for exp in ['cemetery-tours', 'paranormal-investigations', 'pub-crawls', 'true-crime', 'walking-tours']:
        valid.add(f'/experiences/{exp}/')

    # Utility pages
    for u in ['about', 'contact', 'editorial-policy', 'privacy-policy', 'terms']:
        valid.add(f'/{u}/')

    return valid


# ─── Stage 1: QC Layer ──────────────────────────────────────────────────────

def _qc_one(art, hub_url=None, sibling_slugs=None, valid_urls=None, existing_slugs=None):
    """Run full QC checks. Returns (fixable, blocking) issue lists."""
    fixable = []
    blocking = []
    hub = _get_hub_url(art, hub_url)
    siblings = sibling_slugs or []
    min_words = MIN_WORD_COUNT_PILLAR if art.article_type == 'pillar' else MIN_WORD_COUNT_CLUSTER

    # ── SEO: Title ──
    rendered = f"{art.title}{BRAND_SUFFIX}"
    if len(art.title) < MIN_TITLE:
        blocking.append(f"title too short ({len(art.title)} chars, min {MIN_TITLE})")
    elif len(rendered) > MAX_RENDERED_TITLE:
        fixable.append(f"title too long: {len(art.title)} raw → {len(rendered)} rendered (max {MAX_RENDERED_TITLE})")

    # ── SEO: Excerpt ──
    if len(art.excerpt) < MIN_EXCERPT:
        blocking.append(f"excerpt too short ({len(art.excerpt)} chars, min {MIN_EXCERPT})")
    elif len(art.excerpt) > MAX_EXCERPT:
        fixable.append(f"excerpt too long: {len(art.excerpt)} chars (max {MAX_EXCERPT})")

    # ── SEO: Slug ──
    if art.slug != art.slug.lower() or re.search(r'[^a-z0-9A-Z\-]', art.slug) or art.slug.endswith('/'):
        fixable.append(f"slug needs cleanup: \"{art.slug}\"")

    # ── #5: Duplicate slug detection ──
    if existing_slugs and art.slug in existing_slugs:
        blocking.append(f"slug '{art.slug}' already exists on disk — would overwrite")

    # ── #9: Absolute URL for featured image ──
    if art.image_url and not art.image_url.startswith('https://'):
        blocking.append(f"image_url must be absolute https:// URL (got: {art.image_url[:50]})")

    # ── #13: Content depth (type-aware) ──
    body, cr = _split_continue_reading(art.content)
    wc = _clean_word_count(body)
    if wc < min_words:
        blocking.append(f"content too thin ({wc} words, min {min_words} for {art.article_type})")

    # ── Required fields ──
    if not art.image_url:
        blocking.append("missing featured image URL")
    if not art.image_alt:
        blocking.append("missing featured image alt text")
    if not art.category_slug:
        blocking.append("missing category slug")
    if not art.category_name:
        blocking.append("missing category name")

    # ── #24: Category registration (breadcrumbs) ──
    if art.category_slug and art.category_slug not in CATEGORY_HUBS:
        blocking.append(f"category '{art.category_slug}' not in CATEGORY_HUBS — add it before publishing")

    # ── #6: Heading hierarchy ──
    headings = re.findall(r'<(h[1-6])', body)
    if 'h1' in headings:
        fixable.append("content contains <h1> (conflicts with page title) — will downgrade to <h2>")
    if headings and headings[0] not in ('h2', 'h3'):
        fixable.append(f"first heading is <{headings[0]}> (should be <h2>)")
    levels = [int(h[1]) for h in headings]
    for i in range(1, len(levels)):
        if levels[i] > levels[i-1] + 1:
            fixable.append(f"heading jump: h{levels[i-1]} → h{levels[i]} (skipped h{levels[i-1]+1})")
            break

    # ── #7: Mojibake detection ──
    if _has_mojibake(art.content):
        fixable.append("content has mojibake characters — editorial will fix")
    if _has_mojibake(art.title):
        fixable.append("title has mojibake characters")
    if _has_mojibake(art.excerpt):
        fixable.append("excerpt has mojibake characters")

    # ── #8: External link security ──
    ext_links = re.findall(r'<a\s+href="(https?://[^"]+)"([^>]*)>', art.content)
    for url, attrs in ext_links:
        if 'noopener' not in attrs or 'noreferrer' not in attrs:
            fixable.append(f"external link missing rel=\"noopener noreferrer\": {url[:60]}")
            break  # report once

    # ── #10: Empty content detection ──
    empty_p = re.findall(r'<p>\s*</p>', art.content)
    if empty_p:
        fixable.append(f"{len(empty_p)} empty <p> tag(s)")

    # ── #11: Duplicate paragraph detection ──
    paras = re.findall(r'<p>(.*?)</p>', art.content, re.DOTALL)
    seen_paras = set()
    for p in paras:
        clean = p.strip()
        if len(clean) > 50 and clean in seen_paras:
            fixable.append("duplicate paragraph detected")
            break
        seen_paras.add(clean)

    # ── #12: Inline image alt text ──
    imgs = re.findall(r'<img\s([^>]+)>', art.content)
    for img_attrs in imgs:
        if 'alt=' not in img_attrs or 'alt=""' in img_attrs:
            fixable.append("inline <img> missing or empty alt text")
            break

    # ── Internal linking: body ──
    body_links = re.findall(r'href="(/[^"]*)"', body)
    if len(body_links) < MIN_BODY_INTERNAL_LINKS:
        fixable.append(f"body has {len(body_links)} internal link(s) (min {MIN_BODY_INTERNAL_LINKS})")

    # ── #23: Broken internal link validation ──
    if valid_urls:
        all_internal = re.findall(r'href="(/[^"]*)"', art.content)
        for link in all_internal:
            normalized = link.rstrip('/') + '/'
            if normalized not in valid_urls:
                # Also check without trailing slash
                if link not in valid_urls:
                    fixable.append(f"internal link target may not exist: {link}")

    # ── Hub link ──
    if hub:
        all_links = re.findall(r'href="(/[^"]*)"', art.content)
        has_hub = any(hub.rstrip('/') in link for link in all_links)
        if not has_hub:
            fixable.append(f"missing hub link ({hub})")

    # ── Continue Reading section ──
    if not cr:
        fixable.append("no Continue Reading section")
    else:
        cr_links = _extract_cr_links(cr)
        available = (1 if hub else 0) + len([s for s in siblings if s != art.slug])
        effective_min = min(MIN_CONTINUE_READING_LINKS, max(1, available))
        if len(cr_links) < effective_min:
            fixable.append(f"Continue Reading has {len(cr_links)} link(s) (min {effective_min})")
        if hub and not any(hub.rstrip('/') in url for url, _ in cr_links):
            fixable.append(f"Continue Reading missing hub link ({hub})")
        if siblings:
            cr_urls = [url for url, _ in cr_links]
            sibling_count = sum(1 for s in siblings if any(s in u for u in cr_urls))
            min_siblings = min(2, len([s for s in siblings if s != art.slug]))
            if sibling_count < min_siblings:
                fixable.append(f"Continue Reading has {sibling_count} sibling link(s) (min {min_siblings})")

    return fixable, blocking


# ─── Stage 2: Editorial Layer ────────────────────────────────────────────────

def _truncate_title(title):
    if len(title) <= MAX_TITLE_RAW:
        return title
    if ':' in title:
        base = title[:title.rindex(':')].strip()
        if MIN_TITLE <= len(base) <= MAX_TITLE_RAW:
            return base
    for sep in [' — ', ' – ', ' - ']:
        if sep in title:
            base = title[:title.rindex(sep)].strip()
            if MIN_TITLE <= len(base) <= MAX_TITLE_RAW:
                return base
    truncated = title[:MAX_TITLE_RAW - 3]
    last_space = truncated.rfind(' ')
    if last_space > MIN_TITLE:
        truncated = truncated[:last_space]
    return truncated.rstrip('.,;:!? ') + "..."


def _truncate_excerpt(excerpt):
    if len(excerpt) <= MAX_EXCERPT:
        return excerpt
    sentences = re.split(r'(?<=[.!?])\s+', excerpt)
    built = ""
    for s in sentences:
        candidate = (built + " " + s).strip() if built else s
        if len(candidate) <= MAX_EXCERPT:
            built = candidate
        else:
            break
    if built and len(built) >= MIN_EXCERPT:
        return built
    truncated = excerpt[:MAX_EXCERPT - 1]
    last_space = truncated.rfind(' ')
    if last_space > MIN_EXCERPT:
        truncated = truncated[:last_space]
    return truncated.rstrip('.,;:!? ') + "."


def _fix_slug(slug):
    slug = slug.lower().strip('/')
    slug = re.sub(r'[^a-z0-9\-]', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _fix_mojibake(text):
    """Fix all known mojibake sequences."""
    for bad, good in MOJIBAKE_MAP.items():
        text = text.replace(bad, good)
    return text


def _fix_external_links(content):
    """Add rel='noopener noreferrer' target='_blank' to external links."""
    def fix_tag(m):
        tag = m.group(0)
        if 'rel=' not in tag:
            tag = tag.replace('>', ' rel="noopener noreferrer" target="_blank">', 1)
        elif 'noopener' not in tag:
            tag = re.sub(r'rel="([^"]*)"', r'rel="\1 noopener noreferrer"', tag)
        if 'target=' not in tag:
            tag = tag.replace('>', ' target="_blank">', 1)
        return tag

    return re.sub(r'<a\s+href="https?://[^"]*"[^>]*>', fix_tag, content)


def _fix_headings(content):
    """Downgrade H1 to H2 in content."""
    content = re.sub(r'<h1([^>]*)>', r'<h2\1>', content)
    content = re.sub(r'</h1>', '</h2>', content)
    return content


def _fix_empty_paragraphs(content):
    """Remove empty <p> tags."""
    return re.sub(r'<p>\s*</p>', '', content)


HUB_LABELS = {
    '/salem-ghost-tours/': 'Salem Ghost Tours Hub',
    '/new-orleans-ghost-tours/': 'New Orleans Ghost Tours Hub',
    '/chicago-ghost-tours/': 'Chicago Ghost Tours Hub',
    '/destinations/draculas-castle/': "Dracula's Castle",
}


def _editorial_fix(articles, hub_url=None):
    """Apply all auto-fixes in-place. Returns fix log."""
    log = []

    for art in articles:
        fixes = []
        hub = _get_hub_url(art, hub_url)
        siblings = [a for a in articles if a.slug != art.slug]

        # ── Title ──
        rendered = f"{art.title}{BRAND_SUFFIX}"
        if len(rendered) > MAX_RENDERED_TITLE:
            old = art.title
            art.title = _truncate_title(art.title)
            fixes.append(f"title: \"{old}\" ({len(old)}) → \"{art.title}\" ({len(art.title)})")

        # ── Excerpt ──
        if len(art.excerpt) > MAX_EXCERPT:
            old_len = len(art.excerpt)
            art.excerpt = _truncate_excerpt(art.excerpt)
            fixes.append(f"excerpt: {old_len} → {len(art.excerpt)} chars")

        # ── Slug ──
        clean = _fix_slug(art.slug)
        if clean != art.slug:
            fixes.append(f"slug: \"{art.slug}\" → \"{clean}\"")
            art.slug = clean

        # ── #14: Mojibake ──
        if _has_mojibake(art.content):
            art.content = _fix_mojibake(art.content)
            fixes.append("fixed mojibake in content")
        if _has_mojibake(art.title):
            art.title = _fix_mojibake(art.title)
            fixes.append("fixed mojibake in title")
        if _has_mojibake(art.excerpt):
            art.excerpt = _fix_mojibake(art.excerpt)
            fixes.append("fixed mojibake in excerpt")

        # ── #15: External link security ──
        ext_links = re.findall(r'<a\s+href="https?://[^"]*"[^>]*>', art.content)
        needs_fix = any('noopener' not in tag or 'target' not in tag for tag in ext_links)
        if needs_fix:
            art.content = _fix_external_links(art.content)
            fixes.append("added rel/target to external links")

        # ── #16: H1 downgrade ──
        if '<h1' in art.content:
            art.content = _fix_headings(art.content)
            fixes.append("downgraded <h1> to <h2>")

        # ── #10: Empty paragraphs ──
        if re.search(r'<p>\s*</p>', art.content):
            art.content = _fix_empty_paragraphs(art.content)
            fixes.append("removed empty <p> tags")

        # ── Continue Reading ──
        body, existing_cr = _split_continue_reading(art.content)

        if existing_cr:
            cr_links = _extract_cr_links(existing_cr)
            cr_modified = False
            if hub and not any(hub.rstrip('/') in url for url, _ in cr_links):
                cr_links.append((hub, HUB_LABELS.get(hub, 'Ghost Tours Hub')))
                cr_modified = True
                fixes.append(f"injected hub link ({hub}) into Continue Reading")
            if cr_modified:
                art.content = body + _build_continue_reading(cr_links)
        else:
            cr_links = []
            for sib in siblings[:4]:
                cr_links.append((f"/articles/{sib.slug}/", sib.title))
            if hub:
                cr_links.append((hub, HUB_LABELS.get(hub, 'Ghost Tours Hub')))
            if cr_links:
                art.content = body + _build_continue_reading(cr_links)
                fixes.append(f"generated Continue Reading ({len(cr_links)} links)")

        if fixes:
            log.append((art.slug, fixes))

    return log


# ─── Stage 3: Write Layer ────────────────────────────────────────────────────

def _write_to_disk(articles):
    os.makedirs(ARTICLE_DIR, exist_ok=True)
    for i, art in enumerate(articles):
        data = {
            "title": art.title,
            "slug": art.slug,
            "id": art.article_id or (70000 + i),
            "status": "publish",
            "post_type": "post",
            "uri": f"/articles/{art.slug}/",
            "date": art.date,
            "modified": art.date,
            "content": art.content,
            "excerpt": art.excerpt,
            "wordCount": _clean_word_count(art.content),
            "articleType": art.article_type,
            "categories": [{
                "id": art.category_id or 0,
                "slug": art.category_slug,
                "name": art.category_name,
                "description": art.category_description,
            }],
            "pageType": "unassigned",
            "featuredImage": {
                "sourceUrl": art.image_url,
                "altText": art.image_alt,
            },
        }
        with open(os.path.join(ARTICLE_DIR, f"{art.slug}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Pipeline ────────────────────────────────────────────────────────────────

def publish_articles(articles, hub_url=None):
    """Full pipeline: QC → Editorial → Final QC → Write. Returns True if published."""
    n = len(articles)
    sibling_slugs = [a.slug for a in articles]

    # Build valid URL set for link checking
    valid_urls = _build_valid_urls()
    # Also add the new articles being published
    for a in articles:
        valid_urls.add(f'/articles/{a.slug}/')

    # Get existing slugs on disk to check for collisions
    existing_slugs = set()
    if os.path.isdir(ARTICLE_DIR):
        for f in os.listdir(ARTICLE_DIR):
            if f.endswith('.json'):
                existing_slugs.add(f.replace('.json', ''))

    print()
    print("=" * 62)
    print(f"  ARTICLE PIPELINE — {n} articles")
    print("=" * 62)

    # ── Stage 1: QC ──
    print(f"\n  ┌─ STAGE 1: QC CHECK")
    total_fixable = 0
    total_blocking = 0
    for art in articles:
        fixable, blocking = _qc_one(art, hub_url, sibling_slugs, valid_urls, existing_slugs)
        total_fixable += len(fixable)
        total_blocking += len(blocking)

    if total_fixable == 0 and total_blocking == 0:
        print(f"  │  ✓ All {n} articles clean")
    else:
        if total_fixable:
            print(f"  │  ⚠ {total_fixable} fixable issue(s) → editorial layer")
        if total_blocking:
            print(f"  │  ✗ {total_blocking} BLOCKING issue(s):")
            for art in articles:
                _, blocking = _qc_one(art, hub_url, sibling_slugs, valid_urls, existing_slugs)
                for b in blocking:
                    print(f"  │      {art.slug}: {b}")
            print(f"  └─ ABORTED\n")
            return False
    print(f"  └─ Done")

    # ── Stage 2: Editorial Fix ──
    print(f"\n  ┌─ STAGE 2: EDITORIAL FIX")
    fix_log = _editorial_fix(articles, hub_url)
    if fix_log:
        print(f"  │  Fixed {len(fix_log)} article(s):")
        for slug, fixes in fix_log:
            for fix in fixes:
                print(f"  │    {slug}: {fix}")
    else:
        print(f"  │  ✓ No fixes needed")
    print(f"  └─ Done")

    # ── Stage 3: Final QC ──
    # Rebuild sibling slugs and valid URLs after editorial (slugs may have changed)
    sibling_slugs = [a.slug for a in articles]
    for a in articles:
        valid_urls.add(f'/articles/{a.slug}/')

    print(f"\n  ┌─ STAGE 3: FINAL QC")
    remaining = 0
    for art in articles:
        fixable, blocking = _qc_one(art, hub_url, sibling_slugs, valid_urls, None)
        for issue in fixable + blocking:
            print(f"  │  ✗ {art.slug}: {issue}")
            remaining += 1

    if remaining:
        print(f"  │  ✗ {remaining} issue(s) remain")
        print(f"  └─ ABORTED\n")
        return False

    print(f"  │  ✓ All {n} articles pass final QC")
    print(f"  └─ Done")

    # ── Stage 4: Write ──
    print(f"\n  ┌─ STAGE 4: WRITE")
    _write_to_disk(articles)
    total_words = sum(_clean_word_count(a.content) for a in articles)
    print(f"  │  ✓ {n} articles ({total_words:,} words) → {ARTICLE_DIR}/")

    for art in articles:
        rendered = f"{art.title}{BRAND_SUFFIX}"
        body, cr = _split_continue_reading(art.content)
        body_links = len(re.findall(r'href="(/[^"]*)"', body))
        cr_links = len(_extract_cr_links(cr)) if cr else 0
        wc = _clean_word_count(art.content)
        hub = _get_hub_url(art, hub_url)
        hub_status = "✓hub" if hub and hub in art.content else ("—" if not hub else "✗hub")
        print(f"  │    ✓ {art.slug}")
        print(f"  │        {len(rendered)}t | {len(art.excerpt)}e | {wc}w | {body_links}+{cr_links} links | {hub_status} | {art.article_type}")

    print(f"  └─ Done")
    print(f"\n  ✓ {n} articles published.")
    print("=" * 62)
    print()
    return True


# ─── Repair tools ────────────────────────────────────────────────────────────

def repair_hub_links():
    """Inject missing hub links into Continue Reading sections."""
    print(f"\n  Repairing hub links...\n")
    repaired = 0
    for fname in sorted(os.listdir(ARTICLE_DIR)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ARTICLE_DIR, fname)
        with open(path) as f:
            d = json.load(f)
        cat_slug = d['categories'][0]['slug'] if d.get('categories') else ''
        hub = CATEGORY_HUBS.get(cat_slug)
        if not hub or hub.rstrip('/') in d.get('content', ''):
            continue
        body, cr = _split_continue_reading(d.get('content', ''))
        if not cr:
            continue
        cr_links = _extract_cr_links(cr)
        cr_links.append((hub, HUB_LABELS.get(hub, 'Ghost Tours Hub')))
        d['content'] = body + _build_continue_reading(cr_links)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        print(f"    ✓ {d['slug']}: injected {hub}")
        repaired += 1
    print(f"\n  Repaired {repaired} articles.\n")
    return repaired


def repair_all():
    """Fix hub links + mojibake + external link security on all existing articles."""
    print(f"\n  Full repair on existing articles...\n")
    total_fixed = 0
    for fname in sorted(os.listdir(ARTICLE_DIR)):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(ARTICLE_DIR, fname)
        with open(path) as f:
            d = json.load(f)

        fixes = []
        content = d.get('content', '')
        original = content

        # Mojibake
        if _has_mojibake(content):
            content = _fix_mojibake(content)
            fixes.append("mojibake")
        if _has_mojibake(d.get('title', '')):
            d['title'] = _fix_mojibake(d['title'])
            fixes.append("title mojibake")
        if _has_mojibake(d.get('excerpt', '')):
            d['excerpt'] = _fix_mojibake(d['excerpt'])
            fixes.append("excerpt mojibake")

        # External links
        ext_links = re.findall(r'<a\s+href="https?://[^"]*"[^>]*>', content)
        needs_fix = any('noopener' not in tag or 'target' not in tag for tag in ext_links)
        if needs_fix:
            content = _fix_external_links(content)
            fixes.append("ext link security")

        # H1 in content
        if '<h1' in content:
            content = _fix_headings(content)
            fixes.append("h1 downgrade")

        # Empty paragraphs
        if re.search(r'<p>\s*</p>', content):
            content = _fix_empty_paragraphs(content)
            fixes.append("empty <p>")

        # Hub link
        cat_slug = d['categories'][0]['slug'] if d.get('categories') else ''
        hub = CATEGORY_HUBS.get(cat_slug)
        if hub and hub.rstrip('/') not in content:
            body, cr = _split_continue_reading(content)
            if cr:
                cr_links = _extract_cr_links(cr)
                cr_links.append((hub, HUB_LABELS.get(hub, 'Ghost Tours Hub')))
                content = body + _build_continue_reading(cr_links)
                fixes.append("hub link")

        if fixes:
            d['content'] = content
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            print(f"    ✓ {d['slug']}: {', '.join(fixes)}")
            total_fixed += 1

    print(f"\n  Fixed {total_fixed} articles.\n")
    return total_fixed


# ─── CLI Audit ───────────────────────────────────────────────────────────────

def audit_existing():
    """Full audit of all existing article JSON files."""
    print(f"\n  Auditing {ARTICLE_DIR}/\n")
    valid_urls = _build_valid_urls()
    errors = []
    count = 0

    for fname in sorted(os.listdir(ARTICLE_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(ARTICLE_DIR, fname)) as f:
            d = json.load(f)

        count += 1
        slug = d.get("slug", fname.replace(".json", ""))
        title = d.get("title", "")
        excerpt = d.get("excerpt", "")
        content = d.get("content", "")
        img = d.get("featuredImage", {})
        cats = d.get("categories", [])
        cat_slug = cats[0]['slug'] if cats else ''
        hub = CATEGORY_HUBS.get(cat_slug)

        rendered = f"{title}{BRAND_SUFFIX}"
        body, cr = _split_continue_reading(content)
        body_wc = _clean_word_count(body)
        body_links = len(re.findall(r'href="(/[^"]*)"', body))
        cr_links = _extract_cr_links(cr) if cr else []
        has_hub = hub and hub.rstrip('/') in content

        issues = []

        # SEO
        if len(rendered) > MAX_RENDERED_TITLE:
            issues.append(f"title {len(rendered)} chars (max {MAX_RENDERED_TITLE})")
        if len(title) < MIN_TITLE:
            issues.append(f"title {len(title)} chars (min {MIN_TITLE})")
        if len(excerpt) > MAX_EXCERPT:
            issues.append(f"excerpt {len(excerpt)} chars (max {MAX_EXCERPT})")
        if len(excerpt) < MIN_EXCERPT:
            issues.append(f"excerpt {len(excerpt)} chars (min {MIN_EXCERPT})")
        if body_wc < MIN_WORD_COUNT_CLUSTER:
            issues.append(f"only {body_wc} words (min {MIN_WORD_COUNT_CLUSTER})")
        if not img.get("sourceUrl"):
            issues.append("no featured image")
        if not cats:
            issues.append("no category")

        # Image URL
        img_url = img.get("sourceUrl", "")
        if img_url and not img_url.startswith("https://"):
            issues.append("image URL not absolute https://")

        # Linking
        if body_links < 1:
            issues.append("no internal links in body")
        if not cr:
            issues.append("no Continue Reading section")
        elif len(cr_links) < MIN_CONTINUE_READING_LINKS:
            issues.append(f"CR has {len(cr_links)} links (min {MIN_CONTINUE_READING_LINKS})")
        if hub and not has_hub:
            issues.append(f"missing hub link ({hub})")
        if cat_slug and cat_slug not in CATEGORY_HUBS:
            issues.append(f"category '{cat_slug}' not in CATEGORY_HUBS")

        # Content quality
        if _has_mojibake(content) or _has_mojibake(title) or _has_mojibake(excerpt):
            issues.append("mojibake detected")
        headings = re.findall(r'<(h[1-6])', body)
        if 'h1' in headings:
            issues.append("h1 in content")
        if re.search(r'<p>\s*</p>', content):
            issues.append("empty <p> tags")
        ext = re.findall(r'<a\s+href="https?://[^"]*"([^>]*)>', content)
        if any('noopener' not in a for a in ext):
            issues.append("ext links missing noopener")

        # Broken internal links
        all_internal = re.findall(r'href="(/[^"]*)"', content)
        broken = [l for l in all_internal if (l.rstrip('/') + '/') not in valid_urls and l not in valid_urls]
        if broken:
            issues.append(f"{len(broken)} broken internal link(s): {broken[0]}")

        if issues:
            errors.append((slug, issues))

    if errors:
        print(f"  ✗ {len(errors)} of {count} articles have issues:\n")
        for slug, issues in errors:
            print(f"    {slug}: {'; '.join(issues)}")
        print()
        return False
    else:
        print(f"  ✓ All {count} articles pass full audit ({len(valid_urls)} valid URLs checked).\n")
        return True


if __name__ == "__main__":
    if '--repair' in sys.argv:
        repair_hub_links()
    elif '--fix-all' in sys.argv:
        repair_all()
    audit_existing()
