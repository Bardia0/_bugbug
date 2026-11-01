"""
AbuseIPDB reverse-whois scraper.

Faithful Python port of:

    curl -s "https://www.abuseipdb.com/whois/{domain}" \
      -H "user-agent: Mozilla/5.0 (X11; Linux x86_64) ... Chrome/117.0.0.0 ..." \
      -b "abuseipdb_session=YOUR-SESSION" | \
    grep -E "<li>\\w.*</li>" | \
    sed -E "s/<\\/?li>//g" | \
    sed "s|$|.{domain}|"

The session cookie is read from the ABUSEIPDB_SESSION env var
(get it from your browser DevTools: Application → Cookies → abuseipdb_session).
"""
import os
import re

import requests

from .base import DiscoveryTool


class AbuseIPDBTool(DiscoveryTool):
    name = "abuseipdb"
    description = "Reverse-whois harvesting via AbuseIPDB (requires ABUSEIPDB_SESSION)"

    BASE_URL = "https://www.abuseipdb.com/whois/{domain}"
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
    )

    # Matches "<li>word…</li>" – equivalent to the grep -E "<li>\w.*</li>" step
    LI_RE = re.compile(r"<li>(\w[^<]*)</li>", re.IGNORECASE)
    # Only keep words that can form a valid DNS label
    VALID_LABEL_RE = re.compile(r"^[\w-]+$")

    def __init__(self, session_cookie: str | None = None):
        if session_cookie is not None:
            self.session_cookie = session_cookie.strip()
        else:
            self.session_cookie = os.environ.get("ABUSEIPDB_SESSION", "").strip()

    def is_available(self) -> bool:
        return bool(self.session_cookie)

    def discover(self, domain: str, timeout: int = 30) -> set[str]:
        if not self.is_available():
            raise RuntimeError(
                "ABUSEIPDB_SESSION is not set – export it or add it to your shell env."
            )

        url = self.BASE_URL.format(domain=domain)
        headers = {
            "User-Agent": self.USER_AGENT,
            # Set the Cookie header directly so requests doesn't try to
            # parse/encode it (session values can contain '=' etc.)
            "Cookie": f"abuseipdb_session={self.session_cookie}",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            raise RuntimeError(f"HTTP request failed for {domain}: {exc}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise RuntimeError(
                f"AbuseIPDB returned HTTP {resp.status_code} for {domain} "
                "(session cookie may be expired)"
            )
        if resp.status_code == 429:
            raise RuntimeError(f"AbuseIPDB rate-limited us (HTTP 429) for {domain}")
        if resp.status_code != 200:
            raise RuntimeError(f"AbuseIPDB returned HTTP {resp.status_code} for {domain}")

        return self._parse(resp.text, domain)

    def _parse(self, html: str, domain: str) -> set[str]:
        """Reproduce `grep '<li>…</li>' | sed strip tags | sed append .domain`."""
        out: set[str] = set()
        for match in self.LI_RE.finditer(html):
            word = match.group(1).strip()
            if not word:
                continue
            if not self.VALID_LABEL_RE.match(word):
                # Drop entries that can't be a DNS label (spaces, symbols, etc.)
                continue
            # Emulate: sed "s|$|.{domain}|"
            out.add(f"{word}.{domain}".lower())
        return out