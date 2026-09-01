import aiohttp

API_KEY  = "g1l8HidbaTyx9VcMcataqPl2IjmPmln1pTz3vQ5EGswq0eBWRHAbKkVFTKxG"
BASE_URL = "https://pastefy.app/api/v2"
MAX_SIZE = 50 * 1024 * 1024  # skip upload if over 50 MB


async def upload(session: aiohttp.ClientSession, title: str, content: str) -> str | None:
    """Upload to Pastefy and return the raw URL, or None on failure."""
    if len(content.encode("utf-8", errors="ignore")) > MAX_SIZE:
        return None
    try:
        async with session.post(
            f"{BASE_URL}/paste",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={"title": title, "content": content},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            if r.status not in (200, 201):
                return None
            data = await r.json()
            return data.get("paste", {}).get("raw_url") or None
    except Exception:
        return None
