import httpx
import re


def search_images(query: str, cwd: str, count: int = 6) -> str:
    """Find real, embeddable photo URLs (Openverse — CC-licensed, no API key needed).
    Returns direct image URLs the agent can put straight into <img src> so prototypes
    get real photography instead of empty boxes or generic placeholders."""
    count = max(1, min(int(count or 6), 12))
    try:
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.get(
                "https://api.openverse.org/v1/images/",
                params={"q": query, "page_size": count, "mature": "false"},
                headers={"User-Agent": "scroll/1.0"},
            )
            resp.raise_for_status()
            results = (resp.json() or {}).get("results", [])
    except Exception as e:
        return (f"ERROR: image search failed ({e}). Fall back to "
                "https://picsum.photos/seed/<word>/<w>/<h> for photos.")
    if not results:
        return (f"No images found for {query!r}. Fall back to "
                "https://picsum.photos/seed/<word>/<w>/<h>.")
    lines = [f"Images for {query!r} — use these URLs directly in <img src=...> "
             "(CC-licensed via Openverse; set width/height and a descriptive alt):"]
    for r in results:
        url = r.get("url") or ""
        if not url:
            continue
        title = (r.get("title") or "untitled")[:60]
        dims = f"{r.get('width')}x{r.get('height')}" if r.get("width") else "?"
        lines.append(f"- {url}  ({title} · {dims} · {r.get('license', '?')})")
    return "\n".join(lines)


def fetch_url(url: str, cwd: str, max_chars: int = 20000) -> str:
    try:
        with httpx.Client(follow_redirects=True, timeout=20) as client:
            resp = client.get(url, headers={"User-Agent": "scroll/1.0"})
            resp.raise_for_status()
            text = resp.text
    except httpx.HTTPStatusError as e:
        return f"ERROR: HTTP {e.response.status_code} for {url}"
    except Exception as e:
        return f"ERROR: {e}"

    # Strip HTML tags for readability
    ct = resp.headers.get("content-type", "")
    if "html" in ct:
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated to {max_chars} chars]"

    return text
