"""Free-script fetcher for Obscura protected download links. The bot host does
the HTTP. Obscura uses a small handshake before serving the payload:

    1) GET /download (Roblox-like headers)  -> bootstrap stub (~1-2 KB)
    2) parse stub -> B (base), PT (token), DL (download url), KK, SD
    3) R = hr(KK + SD); GET {B}/k/{PT}?op=hsnonce&c={SD}&r={R}&...  -> {nonce}
    4) GET {DL}?n={nonce}&...  -> full Obscura-protected payload

We fetch the protected blob exactly as served (no deobfuscation). `fetch_free`
mirrors luaprot.fetch_free and raises the shared FetchError.
"""

import asyncio
import json
import re
import time
import uuid

from curl_cffi.requests import AsyncSession
from curl_cffi import CurlError

from luaprot import FetchError            # shared exception type

_UA = "Roblox/WinInet"                    # browser UA -> HTML; bare -> block stub
_IMPERSONATE = "chrome124"                # real browser TLS/JA3 fingerprint
_TIMEOUT = 60
_MIN_PAYLOAD = 3000                       # payload >> stub (~1-2 KB) / block (~89 B)

_URL_RE = re.compile(r"""https?://[^\s"'()]+""")
_CONSTS_RE = re.compile(r'local B,PT,DL="([^"]+)","([^"]+)","([^"]+)"')
_KK_RE = re.compile(r'local KK,SD="([^"]+)","([^"]+)"')


def is_obscura(link):
    return "obscuravm.com" in (link or "").lower()


def _hr(x):
    """The stub's hash: h = (h*31 + ord(ch)) % 16777213, printed as 6 hex."""
    h = 0
    for ch in x:
        h = (h * 31 + ord(ch)) % 16777213
    return f"{h:06x}"


async def _text(session, url, headers):
    try:
        r = await session.get(url, headers=headers, timeout=_TIMEOUT)
        return r.status_code, r.text
    except CurlError as e:
        raise FetchError(f"http failed ({e})")


async def fetch_free(link, on_phase=None):
    """Fetch an Obscura-protected payload for `link` (a .../download URL, or a
    loadstring containing one). Returns (payload_text, meta). Raises FetchError
    if the handshake fails or nothing deliverable comes back. on_phase(name) is
    awaited per stage: "send" (stub), "fetch" (nonce), "download" (payload)."""
    async def phase(name):
        if on_phase is not None:
            await on_phase(name)

    raw = (link or "").strip()
    if not is_obscura(raw):
        raise FetchError("not an obscuravm.com download link")
    m = _URL_RE.search(raw)
    url = m.group(0) if m else raw

    job = str(uuid.uuid4())
    headers = {
        "User-Agent": _UA,
        "Roblox-Game-Id": job,
        "Roblox-Session-Id": json.dumps({"GameId": job, "PlaceId": "0"}),
    }
    net = 0.0
    async with AsyncSession(impersonate=_IMPERSONATE) as session:
        # 1) bootstrap stub
        await phase("send")
        t = time.perf_counter()
        try:
            _, stub = await _text(session, url, headers)
        except FetchError as e:
            raise FetchError(f"could not reach obscura ({e})")
        net += time.perf_counter() - t
        cm, km = _CONSTS_RE.search(stub), _KK_RE.search(stub)
        if not cm or not km:
            raise FetchError("stub missing constants (blocked or wrong UA)")
        base, pt, dl = cm.group(1), cm.group(2), cm.group(3)
        kk, sd = km.group(1), km.group(2)
        r_hash = _hr(kk + sd)

        # 2) nonce (short-lived)
        await phase("fetch")
        nonce_url = (f"{base}/k/{pt}"
                     f"?rx=1&op=hsnonce&c={sd}&r={r_hash}&uid=1&pid=0")
        t = time.perf_counter()
        try:
            _, nbody = await _text(session, nonce_url, headers)
        except FetchError as e:
            raise FetchError(f"nonce request failed ({e})")
        net += time.perf_counter() - t
        try:
            nonce = json.loads(nbody)["nonce"]
        except (ValueError, KeyError, TypeError):
            raise FetchError("no nonce returned (blocked or expired)")

        # 3) protected payload (request right after the nonce)
        await phase("download")
        payload_url = f"{dl}?n={nonce}&plr=Player&pid=0&exec=Potassium"
        t = time.perf_counter()
        try:
            pstatus, payload = await _text(session, payload_url, headers)
        except FetchError as e:
            raise FetchError(f"payload request failed ({e})")
        net += time.perf_counter() - t

    if pstatus not in (200, 201):
        raise FetchError(f"obscura HTTP {pstatus}")
    if "detected and blocked" in payload[:200].lower() or len(payload) < _MIN_PAYLOAD:
        raise FetchError("no obscura payload returned (blocked / too small)")
    return payload, {"project": pt, "size": len(payload), "elapsed": net}
