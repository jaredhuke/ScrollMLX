import httpx
import re


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
