#!/usr/bin/env python3
"""
Article generation pipeline for CursedTours.com.

Three-stage pipeline that runs automatically on every batch:

  1. QC LAYER       — Identifies all SEO violations
  2. EDITORIAL LAYER — Auto-fixes what it can, flags what it can't
  3. WRITE LAYER     — Only writes files after everything passes

Usage:

    from article_utils import Article, publish_articles

    articles = [
        Article(
            title="The Great Fire of 1871",
            slug="great-fire-1871",
            excerpt="Short meta description here.",
            category_slug="chicago-haunted-history",
            category_name="Chicago Haunted History",
            image_url="https://images.unsplash.com/...",
            image_alt="Description of image",
            content="<h2>Section</h2>\\n<p>Content here...</p>",
        ),
    ]

    # Full pipeline: QC → editorial fix → final QC → write
    publish_articles(articles)

Auto-fixable (editorial layer handles silently):
  - Title too long → truncates at subtitle separator or word boundary
  - Excerpt too long → truncates at sentence or word boundary
  - Slug uppercase/bad chars/trailing slash → normalized

Not auto-fixable (pipeline aborts with clear error):
  - Title too short (<10 chars)
  - Excerpt too short (<50 chars)
  - Content too thin (<500 words)
  - No internal links in content
  - Missing image, alt text, or category
"""

import json, os, re, sys
from dataclasses import dataclass

# ─── Constants ───────────────────────────────────────────────────────────────

BRAND_SUFFIX = " | Cursed Tours"
MAX_TITLE_RAW = 50          # 50 + 15 (" | Cursed Tours") = 65
MAX_RENDERED_TITLE = 65
MIN_TITLE = 10
MAX_EXCERPT = 160
MIN_EXCERPT = 50
MIN_WORD_COUNT = 500
ARTICLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "data", "articles")


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


# ─── Stage 1: QC Layer ──────────────────────────────────────────────────────

def _qc_one(art):
    """Run QC checks. Returns (fixable_issues, blocking_issues)."""
    fixable = []
    blocking = []

    # Title
    rendered = f"{art.title}{BRAND_SUFFIX}"
    if len(art.title) < MIN_TITLE:
        blocking.append(f"title too short ({len(art.title)} chars, min {MIN_TITLE})")
    elif len(rendered) > MAX_RENDERED_TITLE:
        fixable.append(f"title too long: {len(art.title)} raw → {len(rendered)} rendered (max {MAX_RENDERED_TITLE})")

    # Excerpt
    if len(art.excerpt) < MIN_EXCERPT:
        blocking.append(f"excerpt too short ({len(art.excerpt)} chars, min {MIN_EXCERPT})")
    elif len(art.excerpt) > MAX_EXCERPT:
        fixable.append(f"excerpt too long: {len(art.excerpt)} chars (max {MAX_EXCERPT})")

    # Slug
    if art.slug != art.slug.lower() or re.search(r'[^a-z0-9\-]', art.slug.lower()) or art.slug.endswith('/'):
        fixable.append(f"slug needs cleanup: \"{art.slug}\"")

    # Content
    text_only = re.sub(r'<[^>]+>', ' ', art.content)
    wc = len(text_only.split())
    if wc < MIN_WORD_COUNT:
        blocking.append(f"content too thin ({wc} words, min {MIN_WORD_COUNT})")

    # Internal links
    if len(re.findall(r'href="(/[^"]*)"', art.content)) < 1:
        blocking.append("no internal links in content")

    # Required fields
    if not art.image_url:
        blocking.append("missing featured image URL")
    if not art.image_alt:
        blocking.append("missing featured image alt text")
    if not art.category_slug:
        blocking.append("missing category slug")
    if not art.category_name:
        blocking.append("missing category name")

    return fixable, blocking


# ─── Stage 2: Editorial Layer ────────────────────────────────────────────────

def _truncate_title(title):
    """Shorten title to fit MAX_TITLE_RAW, preferring natural break points."""
    if len(title) <= MAX_TITLE_RAW:
        return title

    # Try removing subtitle after colon
    if ':' in title:
        base = title[:title.rindex(':')].strip()
        if MIN_TITLE <= len(base) <= MAX_TITLE_RAW:
            return base

    # Try removing subtitle after dash
    for sep in [' — ', ' – ', ' - ']:
        if sep in title:
            base = title[:title.rindex(sep)].strip()
            if MIN_TITLE <= len(base) <= MAX_TITLE_RAW:
                return base

    # Word-boundary truncation
    truncated = title[:MAX_TITLE_RAW - 3]
    last_space = truncated.rfind(' ')
    if last_space > MIN_TITLE:
        truncated = truncated[:last_space]
    return truncated.rstrip('.,;:!? ') + "..."


def _truncate_excerpt(excerpt):
    """Shorten excerpt to MAX_EXCERPT, preferring sentence boundaries."""
    if len(excerpt) <= MAX_EXCERPT:
        return excerpt

    # Try cutting at last complete sentence that fits
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

    # Word-boundary fallback
    truncated = excerpt[:MAX_EXCERPT - 1]
    last_space = truncated.rfind(' ')
    if last_space > MIN_EXCERPT:
        truncated = truncated[:last_space]
    return truncated.rstrip('.,;:!? ') + "."


def _fix_slug(slug):
    """Normalize slug: lowercase, hyphens only, no leading/trailing."""
    slug = slug.lower().strip('/')
    slug = re.sub(r'[^a-z0-9\-]', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _editorial_fix(articles):
    """Apply auto-fixes in-place. Returns list of (slug, [fixes])."""
    log = []

    for art in articles:
        fixes = []

        # Title
        rendered = f"{art.title}{BRAND_SUFFIX}"
        if len(rendered) > MAX_RENDERED_TITLE:
            old = art.title
            art.title = _truncate_title(art.title)
            fixes.append(f"title: \"{old}\" ({len(old)}) → \"{art.title}\" ({len(art.title)})")

        # Excerpt
        if len(art.excerpt) > MAX_EXCERPT:
            old_len = len(art.excerpt)
            art.excerpt = _truncate_excerpt(art.excerpt)
            fixes.append(f"excerpt: {old_len} → {len(art.excerpt)} chars")

        # Slug
        clean = _fix_slug(art.slug)
        if clean != art.slug:
            fixes.append(f"slug: \"{art.slug}\" → \"{clean}\"")
            art.slug = clean

        if fixes:
            log.append((art.slug, fixes))

    return log


# ─── Stage 3: Write Layer ────────────────────────────────────────────────────

def _write_to_disk(articles):
    """Write articles to JSON files."""
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

        with open(os.path.join(ARTICLE_DIR, f"{art.slug}.json"), "w") as f:
            json.dump(data, f, indent=2)


# ─── Pipeline ────────────────────────────────────────────────────────────────

def publish_articles(articles):
    """
    Full pipeline: QC → Editorial Fix → Final QC → Write.
    Aborts before writing if any unfixable issues remain.
    Returns True if published, False if blocked.
    """
    n = len(articles)
    print()
    print("=" * 62)
    print(f"  ARTICLE PIPELINE — {n} articles")
    print("=" * 62)

    # ── Stage 1: QC ──
    print(f"\n  ┌─ STAGE 1: QC CHECK")
    total_fixable = 0
    total_blocking = 0
    for art in articles:
        fixable, blocking = _qc_one(art)
        total_fixable += len(fixable)
        total_blocking += len(blocking)

    if total_fixable == 0 and total_blocking == 0:
        print(f"  │  ✓ All {n} articles clean — no issues")
    else:
        if total_fixable:
            print(f"  │  ⚠ {total_fixable} fixable issue(s) → editorial layer will handle")
        if total_blocking:
            print(f"  │  ✗ {total_blocking} BLOCKING issue(s) — cannot auto-fix:")
            for art in articles:
                _, blocking = _qc_one(art)
                for b in blocking:
                    print(f"  │      {art.slug}: {b}")
            print(f"  └─ ABORTED\n")
            return False
    print(f"  └─ Done")

    # ── Stage 2: Editorial Fix ──
    print(f"\n  ┌─ STAGE 2: EDITORIAL FIX")
    fix_log = _editorial_fix(articles)
    if fix_log:
        print(f"  │  Fixed {len(fix_log)} article(s):")
        for slug, fixes in fix_log:
            for fix in fixes:
                print(f"  │    {slug}: {fix}")
    else:
        print(f"  │  ✓ No fixes needed")
    print(f"  └─ Done")

    # ── Stage 3: Final QC ──
    print(f"\n  ┌─ STAGE 3: FINAL QC")
    remaining = 0
    for art in articles:
        fixable, blocking = _qc_one(art)
        for issue in fixable + blocking:
            print(f"  │  ✗ {art.slug}: {issue}")
            remaining += 1

    if remaining:
        print(f"  │  ✗ {remaining} issue(s) remain after editorial fixes")
        print(f"  └─ ABORTED\n")
        return False

    print(f"  │  ✓ All {n} articles pass final QC")
    print(f"  └─ Done")

    # ── Stage 4: Write ──
    print(f"\n  ┌─ STAGE 4: WRITE")
    _write_to_disk(articles)
    total_words = sum(len(re.sub(r'<[^>]+>', ' ', a.content).split()) for a in articles)
    print(f"  │  ✓ {n} articles ({total_words:,} words) → {ARTICLE_DIR}/")

    for art in articles:
        rendered = f"{art.title}{BRAND_SUFFIX}"
        wc = len(re.sub(r'<[^>]+>', ' ', art.content).split())
        links = len(re.findall(r'href="(/[^"]*)"', art.content))
        print(f"  │    ✓ {art.slug}  [{len(rendered)}t|{len(art.excerpt)}e|{wc}w|{links}L]")

    print(f"  └─ Done")
    print(f"\n  ✓ {n} articles published.")
    print("=" * 62)
    print()
    return True


# ─── CLI: Audit existing articles on disk ────────────────────────────────────

def audit_existing():
    """Validate all existing article JSON files."""
    print(f"\n  Auditing {ARTICLE_DIR}/\n")
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

        rendered = f"{title}{BRAND_SUFFIX}"
        wc = len(re.sub(r'<[^>]+>', ' ', content).split())
        links = len(re.findall(r'href="(/[^"]*)"', content))

        issues = []
        if len(rendered) > MAX_RENDERED_TITLE:
            issues.append(f"title {len(rendered)} chars (max {MAX_RENDERED_TITLE})")
        if len(title) < MIN_TITLE:
            issues.append(f"title {len(title)} chars (min {MIN_TITLE})")
        if len(excerpt) > MAX_EXCERPT:
            issues.append(f"excerpt {len(excerpt)} chars (max {MAX_EXCERPT})")
        if len(excerpt) < MIN_EXCERPT:
            issues.append(f"excerpt {len(excerpt)} chars (min {MIN_EXCERPT})")
        if wc < MIN_WORD_COUNT:
            issues.append(f"only {wc} words (min {MIN_WORD_COUNT})")
        if links < 1:
            issues.append("no internal links")
        if not img.get("sourceUrl"):
            issues.append("no featured image")
        if not cats:
            issues.append("no category")

        if issues:
            errors.append((slug, issues))

    if errors:
        print(f"  ✗ {len(errors)} of {count} articles have issues:\n")
        for slug, issues in errors:
            print(f"    {slug}: {'; '.join(issues)}")
        return False
    else:
        print(f"  ✓ All {count} articles pass SEO validation.\n")
        return True


if __name__ == "__main__":
    audit_existing()
