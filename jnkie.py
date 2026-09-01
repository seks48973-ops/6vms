"""Free-script fetcher for jnkie deliveries (Delta-style handshake).

jnkie's current delivery flow is a POST handshake:

    POST /api/v1/luascripts/delivery/<HASH>?v=2
    User-Agent: Env_Logger
    Content-Type: text/plain
    Delta-User-Identifier: <id>
    Delta-Fingerprint: <fingerprint>
    body: KEYLESS

which replies with a plain-text CDN URL to the actual .lua file. That file
usually wraps a Luraph-protected block; when the Luraph marker is present we
trim to it (keeping the comment), otherwise the whole file is delivered.

The old GET /public/<HASH>/download endpoint is kept as a fallback for hashes
that still resolve there.

Scope: public/free scripts only.
"""

import asyncio
import re
import time

import aiohttp

try:
    from curl_cffi import AsyncSession as _CurlSession

    _CURL = True  # curl_cffi does browser TLS/JA3/JA4 impersonation (beats Cloudflare Bot Management)
except Exception:  # not installed -> fall back to aiohttp (no TLS impersonation, will 403 on Bot Management)
    _CURL = False


class FetchError(Exception):
    """Raised when nothing deliverable can be obtained from the fetcher."""


DELIVERY_URL = "https://api.jnkie.com/api/v1/luascripts/delivery/{hash}?v=2"
PUBLIC_URL = "https://api.jnkie.com/api/v1/luascripts/public/{hash}/download"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_CT = "text/plain"
_ID = "fakeidentifier"
_FP = "fakefingerprint"
_BODY = "KEYLESS"
_TIMEOUT = aiohttp.ClientTimeout(total=60)
_CDN_TIMEOUT = aiohttp.ClientTimeout(total=120)
_MIN_RAW_BODY = 8
_MIN_FILE_BODY = 500
_CDN_RE = re.compile(r"https?://[^\s\"']+\.lua\b")
_LURAPH_MARKER = "-- This file was protected using Luraph Obfuscator"


def _new_session(proxy=None):
    if _CURL:
        return _CurlSession(impersonate="chrome", headers={"User-Agent": _UA}, proxy=proxy)
    return aiohttp.ClientSession()


def _load_proxies():
    """Proxies from JNKIE_PROXIES env (comma/newline separated); used to rotate IPs."""
    import os
    src = os.environ.get("JNKIE_PROXIES", "")
    return [p.strip() for p in re.split(r"[\s,]+", src) if p.strip()]

_HASH_IN_URL_RE = re.compile(
    r"luascripts/(?:delivery|public)/([A-Za-z0-9_\-]+)", re.I)
_CDN_HASH_RE = re.compile(r"cdn\.jnkie\.com/([A-Za-z0-9_\-]+)\.lua", re.I)
_BARE_HASH_RE = re.compile(r"^[A-Za-z0-9_\-]{6,}$")


def is_jnkie(link):
    """True if `link` looks like a jnkie target (URL or bare script hash)."""
    low = (link or "").lower()
    if "jnkie" in low:
        return True
    return bool(_BARE_HASH_RE.match((link or "").strip()))


def extract_hash(link):
    link = (link or "").strip()
    m = _HASH_IN_URL_RE.search(link)
    if m:
        return m.group(1)
    m = _CDN_HASH_RE.search(link)
    if m:
        return m.group(1)
    if _BARE_HASH_RE.match(link):
        return link
    return None


def _trim_luraph(body: str):
    """Return (payload, luraph: bool) with anything before the Luraph block cut."""
    i = body.find(_LURAPH_MARKER)
    if i >= 0:
        return body[i:], True
    return body, False


async def _session_get(session, url, headers, timeout, what):
    try:
        if _CURL:
            r = await session.get(url, headers=headers, timeout=timeout.total)
            status = r.status_code
            body = r.text
        else:
            async with session.get(url, headers=headers, timeout=timeout) as r:
                status = r.status
                body = await r.text(errors="replace")
    except FetchError:
        raise
    except Exception as e:
        raise FetchError(f"could not reach {what} ({e})")
    if status == 403:
        raise FetchError(f"{what} returned 403 (blocked User-Agent)")
    if status == 404:
        raise FetchError(f"{what} not found (404) — is it public?")
    if status != 200:
        raise FetchError(f"{what} HTTP {status}")
    return body


async def _delivery(session, script_hash):
    """POST the Delta-style KEYLESS handshake, return the CDN payload body."""
    url = DELIVERY_URL.format(hash=script_hash)
    headers = {
        "User-Agent": _UA,
        "Content-Type": _CT,
        "Delta-User-Identifier": _ID,
        "Delta-Fingerprint": _FP,
    }
    if _CURL:
        r = await session.post(url, data=_BODY, headers=headers, timeout=_TIMEOUT.total)
        status = r.status_code
        body = r.text
    else:
        async with session.post(url, data=_BODY, headers=headers,
                                timeout=_TIMEOUT) as r:
            status = r.status
            body = await r.text(errors="replace")

    if status == 403:
        raise FetchError("jnkie delivery returned 403 (blocked User-Agent)")
    if status == 404:
        raise FetchError(f"jnkie script not found (404) — is it public?")
    if status not in (200, 201):
        raise FetchError(f"jnkie delivery HTTP {status}")
    if len(body) < _MIN_RAW_BODY:
        raise FetchError("jnkie delivery returned an empty/short body")

    m = _CDN_RE.search(body)
    if not m:
        raise FetchError(f"jnkie delivery did not return a CDN file URL: {body[:160]!r}")
    cdn = m.group(0)

    file_body = await _session_get(session, cdn,
                                    {"User-Agent": _UA},
                                    _CDN_TIMEOUT, "jnkie CDN")
    if len(file_body) < _MIN_FILE_BODY:
        raise FetchError("jnkie delivery returned an empty/short file")
    return file_body


async def _public(session, script_hash):
    """Legacy GET /public/<hash>/download fallback."""
    url = PUBLIC_URL.format(hash=script_hash)
    body = await _session_get(session, url, {"User-Agent": _UA},
                              _TIMEOUT, "jnkie")
    if len(body) < _MIN_FILE_BODY:
        raise FetchError("jnkie returned an empty/short body")
    return body


async def fetch_free(link, on_phase=None, proxies=None):
    """Download a jnkie script and return (payload_text, meta). Raises
    FetchError if there is nothing deliverable. on_phase(name) is awaited for
    each stage: "send", "fetch", "download". `proxies` is an optional list of
    proxy URLs (e.g. ["http://1.2.3.4:8080", ...]) to rotate through; the last
    entry None means a direct connection."""
    async def phase(name):
        if on_phase is not None:
            await on_phase(name)

    script_hash = extract_hash(link)
    if not script_hash:
        raise FetchError("no jnkie script hash found in that input")

    await phase("send")
    await phase("fetch")

    if proxies is None:
        proxies = _load_proxies()
    attempts = proxies + [None]  # None = direct connection (no proxy)

    net = 0.0
    body = None
    via = "delivery"
    luraph = False
    used_proxy = None
    last_err = None
    for proxy in attempts:
        try:
            async with _new_session(proxy) as session:
                try:
                    t = time.perf_counter()
                    file_body = await _delivery(session, script_hash)
                    net += time.perf_counter() - t
                    body, luraph = _trim_luraph(file_body)
                    used_proxy = proxy
                    break
                except FetchError as de:
                    if "404" in str(de) or "not found" in str(de):
                        try:
                            t = time.perf_counter()
                            file_body = await _public(session, script_hash)
                            net += time.perf_counter() - t
                            via = "public"
                            body, luraph = _trim_luraph(file_body)
                            used_proxy = proxy
                            break
                        except FetchError:
                            raise de
                    else:
                        last_err = de
                        continue
        except FetchError:
            raise
        except Exception as e:
            last_err = FetchError(f"proxy {proxy!r} failed: {e}")
            continue
    if body is None:
        raise last_err or FetchError("all proxies failed")

    await phase("download")
    await asyncio.sleep(0)
    return body, {"hash": script_hash, "size": len(body),
                  "luraph": luraph, "elapsed": net, "via": via,
                  "proxy": used_proxy}


# ── standalone entrypoint (also used by the decompiler via shell-out) ─────────
if __name__ == "__main__":
    import sys

    async def _main():
        args = sys.argv[1:]
        target = None
        outfile = None
        proxies = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--proxy" and i + 1 < len(args):
                proxies.append(args[i + 1])
                i += 2
                continue
            if a == "--proxies" and i + 1 < len(args):
                try:
                    with open(args[i + 1], encoding="utf-8") as pf:
                        proxies.extend([p.strip() for p in re.split(r"[\s,]+", pf.read()) if p.strip()])
                except OSError as e:
                    sys.stderr.write(f"proxy file error: {e}\n")
                i += 2
                continue
            if target is None:
                target = a
            elif outfile is None:
                outfile = a
            i += 1
        if not target:
            target = "472e2a90e26e8bbd25ae9cbcb7a75bf2e2261889724b9fc87ee174e59c07dd25"
        try:
            payload, meta = await fetch_free(target, proxies=proxies)
        except FetchError as ex:
            sys.stderr.write(f"FetchError: {ex}\n")
            sys.exit(1)
        # diagnostics go to stderr so stdout/file stays clean for capture
        sys.stderr.write(
            f"OK via {meta['via']} - {meta['size']} bytes - luraph: {meta['luraph']}"
            f" - proxy: {meta.get('proxy')}\n")
        if outfile:
            with open(outfile, "w", encoding="utf-8", errors="replace") as f:
                f.write(payload)
        else:
            sys.stdout.write(payload)

    asyncio.run(_main())