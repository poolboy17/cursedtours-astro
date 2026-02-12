# Article Pipeline Gap Analysis — 25 Enhancements

**Date**: 2026-02-12
**Scope**: `article_utils.py` pipeline + template + data flow
**Method**: End-to-end audit of generation → JSON → template → sitemap → browser

---

## 🔴 BUGS FOUND DURING AUDIT (fix now)

### 1. og:image never set on article pages
The `[slug].astro` template never passes `ogImage` to Layout. Social sharing on Facebook/LinkedIn/Slack shows no preview image for any of the 61 articles.
**Fix**: Pass `ogImage={article.featuredImage?.sourceUrl}` to `<Layout>`.
**Pipeline role**: Validate `image_url` starts with `https://` (absolute URL required for og:image).

### 2. Twitter Card meta tags completely missing
Layout.astro has zero `twitter:` meta tags. Twitter/X shows generic unfurl for all pages.
**Fix**: Add `twitter:card`, `twitter:title`, `twitter:description`, `twitter:image` to Layout.
**Pipeline role**: None (template fix only).

### 3. Broken UTF-8 entities in 4 New Orleans articles
Mojibake `Ã©` instead of `é` in lalaurie-mansion, french-quarter, voodoo-complete-guide, st-louis-cemetery.
**Fix**: Find/replace broken sequences.
**Pipeline role**: Add encoding validation — detect common mojibake patterns (`Ã©`, `â€™`, `â€œ`) and reject or auto-fix.

### 4. 11 Dracula articles link to non-existent `/destinations/draculas-castle/`
The destination page file is `draculas-castle.astro` but the slug might not match, or the page may have been removed. All 11 Dracula Continue Reading hub links point to a 404.
**Fix**: Verify the destination exists; update hub URL if needed.
**Pipeline role**: Add broken internal link detection — validate all `href="/..."` targets exist as real pages at build time.

---

## 🟡 QC LAYER ENHANCEMENTS (validation)

### 5. Duplicate slug detection
Pipeline currently doesn't check if a new article's slug collides with an existing one on disk. Two articles with the same slug = one overwrites the other silently.
**Severity**: Data loss risk.

### 6. Heading hierarchy validation
Enforce: no `<h1>` in content (page H1 comes from template), first heading must be `<h2>`, no heading level jumps (h2→h4 without h3).
**Current state**: All 61 pass today, but no enforcement at generation time.

### 7. Broken HTML entity detection
Flag `â€™`, `â€œ`, `â€`, `Ã©`, `Ã¨`, `Ã¢` and other common mojibake patterns.
**Current state**: 4 articles have this today.

### 8. External link security check
Verify all `<a href="https://...">` tags include `rel="noopener noreferrer"` and `target="_blank"`.
**Current state**: All 61 pass today, but no enforcement at generation time.

### 9. Absolute URL in image_url validation
Featured image must be a full `https://` URL (not relative), since it's used for og:image and JSON-LD.
**Current state**: All use Unsplash URLs today, but nothing prevents a relative path.

### 10. Empty/whitespace content detection
Flag empty `<p></p>`, `<p>&nbsp;</p>`, or content that's just HTML tags with no text.

### 11. Duplicate paragraph detection
Flag if the same paragraph (>50 chars) appears twice in one article's content — catches copy-paste errors during generation.

### 12. Inline image alt text enforcement
Any `<img>` inside content must have non-empty `alt` attribute.
**Current state**: All pass today, not enforced at generation.

### 13. Word count floor per article type
Pillar articles should be ≥1,200 words. Current 500-word minimum is appropriate for cluster articles but too low for pillars. Add optional `article_type` field: `pillar` (1200+ words) vs `cluster` (500+).

---

## 🟢 EDITORIAL LAYER ENHANCEMENTS (auto-fix)

### 14. Auto-fix broken UTF-8 entities
Map common mojibake sequences back to correct characters:
- `Ã©` → `é`, `Ã¨` → `è`, `â€™` → `'`, `â€œ` → `"`, `â€` → `"`, `â€"` → `—`

### 15. Auto-inject `rel="noopener noreferrer" target="_blank"` on external links
Scan content for `<a href="https://..."` and add missing attributes.

### 16. Auto-strip `<h1>` from content
If content contains an `<h1>`, downgrade it to `<h2>` automatically.

### 17. Smart reading time calculation
The template currently counts raw HTML tokens (`article.content.split(/\s+/).length / 250`), which includes HTML tags. Pipeline should pre-compute a clean word count and store it in the JSON (or the template should strip tags first).

---

## 🔵 STRUCTURAL / DATA MODEL ENHANCEMENTS

### 18. Store `wordCount` in JSON
Pre-compute and store clean word count in each article JSON. Used by: reading time display, JSON-LD `wordCount` property, pillar vs cluster detection, audit reports.

### 19. Store `readingTime` in JSON
Pre-compute `Math.ceil(wordCount / 250)` and store as integer minutes. Template reads it directly instead of computing from raw HTML.

### 20. Add `articleType` field: `pillar` | `cluster`
Enables different validation thresholds (word count, linking density) and different template treatments (featured placement, schema markup). Category pages already sort by word count to identify the pillar — this makes it explicit.

### 21. Enrich JSON-LD Article schema
Current schema is missing:
- `wordCount` — helps search engines assess content depth
- `articleSection` — maps to category name
- `keywords` — topical keywords for the article
- `image` — should be the featured image (currently only in page meta, not in JSON-LD)

### 22. Add `og:image` dimensions
When og:image is set, also set `og:image:width` and `og:image:height` for optimal social card rendering. Unsplash URLs contain `w=` and `h=` params — pipeline can extract and store these.

---

## 🟣 PIPELINE PROCESS ENHANCEMENTS

### 23. Broken internal link validation against live page inventory
At QC time, scan all `href="/..."` in content and verify each target exists as a real page (article JSON, city hub, destination page, category page, etc). This would have caught the Dracula 404s.
**Implementation**: Build a `valid_urls` set from articles on disk + known static pages, check at QC time.

### 24. Category auto-registration check
When `publish_articles()` runs, verify the article's `category_slug` exists in both:
- `CATEGORY_HUBS` dict in article_utils.py
- `CATEGORIES` object in src/data/articles.ts

Currently only checks CATEGORY_HUBS. If the category isn't in articles.ts, the article builds but has no breadcrumbs and doesn't appear on the category page.

### 25. Post-write build verification
After writing articles to disk, run `npm run build` and check for build errors. A broken article (bad JSON, missing category) can break the entire site build. The pipeline should catch this before it reaches git/deploy.
**Implementation**: Optional `--build` flag on publish_articles that triggers `npm run build` after writing, parses output for errors.

---

## PRIORITY MATRIX

| # | Enhancement | Effort | Impact | Priority |
|---|------------|--------|--------|----------|
| 1 | og:image on articles | 5 min | High (social) | P0 |
| 2 | Twitter card tags | 5 min | High (social) | P0 |
| 3 | Fix 4 broken entities | 5 min | Medium | P0 |
| 4 | Fix 11 Dracula hub links | 10 min | High (404s) | P0 |
| 5 | Duplicate slug detection | 10 min | High (data loss) | P1 |
| 7 | Broken entity detection | 15 min | Medium | P1 |
| 14 | Auto-fix broken entities | 15 min | Medium | P1 |
| 23 | Internal link validation | 30 min | High (404 prevention) | P1 |
| 24 | Category registration check | 15 min | Medium | P1 |
| 6 | Heading hierarchy | 10 min | Medium | P2 |
| 8 | External link security | 10 min | Low | P2 |
| 9 | Absolute URL validation | 5 min | Low | P2 |
| 10 | Empty content detection | 5 min | Low | P2 |
| 11 | Duplicate paragraph detection | 10 min | Low | P2 |
| 12 | Inline img alt enforcement | 5 min | Medium | P2 |
| 13 | Pillar word count floor | 10 min | Medium | P2 |
| 15 | Auto-inject rel=noopener | 10 min | Low | P2 |
| 16 | Auto-strip H1 | 5 min | Low | P2 |
| 17 | Reading time fix | 10 min | Low | P2 |
| 18 | Store wordCount in JSON | 10 min | Medium | P3 |
| 19 | Store readingTime in JSON | 5 min | Low | P3 |
| 20 | articleType field | 15 min | Medium | P3 |
| 21 | Enrich JSON-LD | 20 min | Medium | P3 |
| 22 | og:image dimensions | 10 min | Low | P3 |
| 25 | Post-write build verify | 20 min | High (safety) | P3 |
