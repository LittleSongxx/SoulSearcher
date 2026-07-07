"""Extract Web Images Tool — enrich research with visual data from web pages.

Extends the deer-flow multimodal pattern to extract images from fetched web pages.
The researcher LLM decides when to use this tool based on whether the research topic
would benefit from visual data (diagrams, financial reports, etc.).

Flow:
1. Fetch page HTML
2. Parse <img> tags, score and filter by relevance heuristics
3. Download top N images, validate MIME type, encode to base64
4. Store in config.viewed_images for downstream multimodal injection

Not enabled by default — gated behind vision_enrich_data config flag.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from agent.tools.view_image import _detect_image_mime

logger = logging.getLogger(__name__)

# ── Limits ──────────────────────────────────────────────────────────────────
_MAX_IMAGE_BYTES = 500 * 1024   # 500KB per image
_MIN_IMAGE_BYTES = 10 * 1024    # 10KB minimum (skip tiny icons)
_MAX_TOTAL_IMAGES = 5
_REQUEST_TIMEOUT = 15
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# ── Filtering Patterns ──────────────────────────────────────────────────────

# URL patterns likely to be ads, trackers, or non-content images
_AD_TRACKER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"/(?:ads?|advertisement|banner|pixel|tracker|analytics|stat|beacon)/",
        r"/(?:spacer|blank|clear|dot|transparent)(?:\.\w+)?$",
        r"/1x1(?:\.\w+)?$",
        r"doubleclick|facebook\.com/tr|google-analytics",
    ]
]

# Alt text that indicates decorative or non-informative images
_MEANINGLESS_ALT: set[str] = {
    "", "image", "picture", "photo", "img", "icon", "logo",
    "thumbnail", "avatar", "profile", "placeholder",
}

# Keywords that suggest an image contains useful visual data
_VISUAL_CONTENT_KEYWORDS = [
    "chart", "graph", "diagram", "figure", "table", "plot",
    "illustration", "screenshot", "infographic", "dashboard",
    "heatmap", "flowchart", "architecture", "timeline",
    "comparison", "trend", "distribution", "breakdown",
    "revenue", "earnings", "financial", "quarterly", "annual",
    "growth", "forecast", "projection", "analysis",
]

# ── HTML Image Extraction ───────────────────────────────────────────────────


def _is_blocked_url(url: str) -> bool:
    """Check if URL should be blocked (private IPs, etc.)."""
    parsed = urlsplit(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return True
    return False


def _parse_dimension(tag: str, attr: str) -> int | None:
    """Extract width or height attribute from an img tag."""
    m = re.search(rf'{attr}=["\']?(\d+)', tag, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _extract_img_candidates(html: str) -> list[dict[str, Any]]:
    """Extract all <img> tag candidates from HTML with context metadata.

    Uses regex to find img tags and surrounding semantic context.
    Returns list of candidate dicts sorted by position in document.
    """
    img_pattern = re.compile(r"<img\b[^>]*?/?>", re.IGNORECASE | re.DOTALL)
    candidates: list[dict[str, Any]] = []

    for match in img_pattern.finditer(html):
        tag = match.group(0)
        pos = match.start()

        # Extract src
        src_match = re.search(r'src\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not src_match:
            src_match = re.search(r'src\s*=\s*(\S+)', tag, re.IGNORECASE)
        if not src_match:
            continue
        src = unescape(src_match.group(1).strip())

        # Skip tiny data URIs (likely inline icons)
        if src.startswith("data:") and len(src) < 500:
            continue

        # Extract alt text
        alt_match = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
        alt = unescape(alt_match.group(1).strip()) if alt_match else ""

        # Extract dimensions
        width = _parse_dimension(tag, "width")
        height = _parse_dimension(tag, "height")

        # Check if inside semantic container (article, main, figure)
        context_before = html[max(0, pos - 3000):pos]
        in_semantic = bool(re.search(
            r"<(?:article|main|figure|section)\b[^>]*?>",
            context_before, re.IGNORECASE,
        ))
        # Check for caption or figcaption nearby
        has_caption = bool(re.search(
            r"<(?:figcaption|caption)\b[^>]*?>",
            html[pos:pos + 500], re.IGNORECASE,
        ))

        candidates.append({
            "src": src,
            "alt": alt,
            "width": width,
            "height": height,
            "in_semantic": in_semantic,
            "has_caption": has_caption,
            "position": pos,
        })

    return candidates


def _score_and_filter(
    candidates: list[dict[str, Any]],
    max_images: int,
) -> list[str]:
    """Score image candidates by informativeness and return top URLs.

    Scoring heuristics:
    - Descriptive alt text with visual keywords → high score
    - Inside semantic HTML (article, figure) → bonus
    - Has caption nearby → bonus
    - Decent dimensions (>= 200x150) → bonus
    - Known image extensions → bonus
    - Ad/tracker URL patterns → excluded
    - SVG files → excluded
    - Meaningless alt text → penalized
    """
    scored: list[tuple[int, str]] = []

    for img in candidates:
        src = img["src"]
        src_lower = src.lower()

        # Exclude ad/tracker URLs
        if any(p.search(src) for p in _AD_TRACKER_PATTERNS):
            continue

        # Exclude SVG
        if src_lower.endswith(".svg") or "image/svg" in src_lower:
            continue

        # Exclude common icon/service patterns
        if re.search(r"/icon[s]?/", src_lower) and img.get("width", 0) and (
            img["width"] or 999
        ) < 50:
            continue

        score = 0

        # Alt text quality
        alt = img["alt"].strip()
        alt_lower = alt.lower()
        if alt and alt_lower not in _MEANINGLESS_ALT:
            words = alt.split()
            score += min(len(words) * 3, 30)  # Longer descriptive alt = better

            # Visual content keyword bonus
            keyword_hits = sum(
                1 for kw in _VISUAL_CONTENT_KEYWORDS if kw in alt_lower
            )
            score += keyword_hits * 8

        # Semantic context signals
        if img["in_semantic"]:
            score += 8
        if img["has_caption"]:
            score += 12  # Caption strongly suggests informative image

        # Dimension signals
        w = img.get("width") or 0
        h = img.get("height") or 0
        if w >= 400 and h >= 300:
            score += 6  # Large image, likely content
        elif w >= 200 and h >= 150:
            score += 3
        elif w > 0 and h > 0 and (w < 50 or h < 50):
            score -= 5  # Tiny, likely decorative

        # Early-position images (above the fold) may be hero/header images
        # But images further down may be content; slight preference for mid-document
        # No strong position bias — let alt text and semantics drive

        # File extension
        if any(src_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            score += 2
        elif not any(src_lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            score -= 3  # No recognizable extension, might not be an image

        if score > 0:
            scored.append((score, src))

    # Sort by score descending, take top N unique URLs
    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    top_urls: list[str] = []
    for _score, url in scored:
        if url in seen:
            continue
        seen.add(url)
        top_urls.append(url)
        if len(top_urls) >= max_images:
            break

    return top_urls


# ── Image Download & Encoding ───────────────────────────────────────────────


async def _download_and_encode(
    image_urls: list[str],
    page_url: str,
) -> list[dict[str, Any]]:
    """Download images from URLs, validate, and encode to base64.

    Uses asyncio.to_thread with requests for async-safe HTTP downloads.
    Filters by size, validates MIME type via magic bytes.
    """
    results: list[dict[str, Any]] = []

    async def _fetch_one(img_url: str) -> dict[str, Any] | None:
        full_url = urljoin(page_url, img_url)

        if _is_blocked_url(full_url):
            return None

        try:
            resp = await asyncio.to_thread(
                requests.get,
                full_url,
                timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _DEFAULT_UA},
                stream=True,
            )

            if resp.status_code != 200:
                return None

            # Read with size cap
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        return None  # Too large, skip
            resp.close()

            if total < _MIN_IMAGE_BYTES:
                return None

            data = b"".join(chunks)

            mime = _detect_image_mime(data)
            if not mime:
                return None

            b64 = base64.b64encode(data).decode("utf-8")
            return {
                "url": full_url,
                "base64": b64,
                "mime_type": mime,
                "size_bytes": total,
            }

        except Exception as e:
            logger.debug(f"[ExtractImages] Download failed for {full_url}: {e}")
            return None

    tasks = [_fetch_one(url) for url in image_urls]
    gathered = await asyncio.gather(*tasks)

    for item in gathered:
        if item:
            results.append(item)

    return results


# ── Main Function ───────────────────────────────────────────────────────────


async def extract_web_images(
    url: str,
    max_images: int = 3,
    *,
    _state: dict | None = None,
    _html: str | None = None,
) -> dict[str, Any]:
    """Extract meaningful images from a web page for multimodal analysis.

    The researcher LLM should call this when the research topic involves
    visual data: figures, diagrams, financial reports, dashboards,
    architectural diagrams, etc.

    Args:
        url: URL of the web page to extract images from.
        max_images: Maximum number of images to return (1-5, default 3).
        _state: Optional state dict for storing viewed_images (injected by node).
        _html: Pre-fetched HTML content (avoids duplicate fetch if page is
               already loaded in the research context).

    Returns:
        dict with: success, url, images (list of {url, base64, mime_type, size_bytes}),
                   image_count, error, state_update
    """
    result: dict[str, Any] = {
        "success": False,
        "url": url,
        "images": [],
        "image_count": 0,
        "error": None,
        "state_update": None,
    }

    max_images = max(1, min(max_images, _MAX_TOTAL_IMAGES))

    # 1. Fetch HTML if not provided
    html = _html
    if not html:
        if _is_blocked_url(url):
            result["error"] = f"Blocked or invalid URL: {url}"
            return result

        try:
            resp = await asyncio.to_thread(
                requests.get,
                url,
                timeout=_REQUEST_TIMEOUT,
                headers={"User-Agent": _DEFAULT_UA},
                stream=True,
            )

            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}: {url}"
                return result

            content_type = resp.headers.get("content-type", "").lower()
            if "html" not in content_type:
                result["error"] = f"Not an HTML page: {content_type}"
                return result

            # Read with reasonable cap
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > 2 * 1024 * 1024:  # 2MB HTML cap
                        break
            resp.close()
            html = b"".join(chunks).decode("utf-8", errors="replace")

        except Exception as e:
            result["error"] = f"Failed to fetch page: {e}"
            return result

    # 2. Extract and score image candidates
    candidates = _extract_img_candidates(html)
    if not candidates:
        result["error"] = "No images found on the page"
        return result

    logger.debug(
        f"[ExtractImages] Found {len(candidates)} img candidates on {url}"
    )

    # 3. Filter
    top_urls = _score_and_filter(candidates, max_images)
    if not top_urls:
        result["error"] = "No informative images found after filtering"
        return result

    logger.info(
        f"[ExtractImages] Selected {len(top_urls)}/{len(candidates)} "
        f"images from {url} for download"
    )

    # 4. Download and encode
    encoded = await _download_and_encode(top_urls, url)

    if not encoded:
        result["error"] = "Failed to download any images (size/format constraints)"
        return result

    result["success"] = True
    result["images"] = encoded
    result["image_count"] = len(encoded)

    logger.info(
        f"[ExtractImages] Downloaded {len(encoded)} images from {url} "
        f"({sum(img['size_bytes'] for img in encoded):,} bytes total)"
    )

    # 5. Build state update for viewed_images
    if _state is not None:
        existing = _state.get("viewed_images", {})
        for img in encoded:
            key = img["url"]
            existing[key] = {
                "base64": img["base64"],
                "mime_type": img["mime_type"],
            }
        result["state_update"] = {"viewed_images": existing}

    return result


# =============================================================================
# LangChain Tool Adapter
# =============================================================================


async def extract_web_images_tool(
    url: str,
    max_images: int = 3,
) -> str:
    """Extract images from a web page for visual analysis.

    Use this tool when the research topic involves visual data such as:
    - Figures, diagrams, or infographics
    - Financial reports, earnings summaries, or dashboards
    - Architecture diagrams, flowcharts, or data visuals
    - Any topic where images are likely to contain key information

    Args:
        url: The web page URL to extract images from.
        max_images: Maximum images to extract (1-5, default 3).
    """
    result = await extract_web_images(url, max_images=max_images)

    if result["success"]:
        images = result["images"]
        lines = [
            f"Successfully extracted {len(images)} image(s) from: {url}",
        ]
        for i, img in enumerate(images, 1):
            lines.append(
                f"  {i}. {img['url']} "
                f"({img['mime_type']}, {img['size_bytes']:,} bytes)"
            )
        return "\n".join(lines)

    return f"Error extracting images from {url}: {result['error']}"
