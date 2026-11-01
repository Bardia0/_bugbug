"""
crt.sh Certificate Transparency log scraper.

Faithful Python port of:

    curl -s 'https://crt.sh/?q=${domain}&output=json' \
      | jq -r '.[]|.name_value,.common_name' \
      | tr 'A-Z' 'a-z' | sed 's/\\*\\.//g' \
      | grep -E '\\.?domain\\.com$' | sort -u

Notes vs. the shell pipeline:
  * jq -r outputs `name_value` and `common_name` verbatim; name_value can
    contain embedded newlines, so we split on them just like the shell does
    implicitly when piping through tr / sed / grep.
  * The grep filter in the original also matches unrelated names like
    `barexample.com` (because `\\.?` allows zero dots). We filter the
    intended way instead: exact domain or a proper subdomain.
"""
import logging

import requests

from .base import DiscoveryTool

log = logging.getLogger(__name__)


class CrtShTool(DiscoveryTool):
    name = "crtsh"
    description = "Certificate Transparency log scraping via crt.sh"

    URL = "https://crt.sh/"

    def is_available(self) -> bool:
        # HTTP-only, no external binary or credentials required.
        # Always "available" – failures (offline, rate-limit) are captured
        # per-domain by the runner and reported as errors.
        return True

    def discover(self, domain: str, timeout: int = 60) -> set[str]:
        params = {"q": domain, "output": "json"}
        try:
            resp = requests.get(self.URL, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"crt.sh request failed for {domain}: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"crt.sh returned HTTP {resp.status_code} for {domain}")

        try:
            data = resp.json()
        except ValueError:
            # crt.sh occasionally serves an HTML error page with status 200
            raise RuntimeError(f"crt.sh returned non-JSON response for {domain}")

        if not isinstance(data, list):
            raise RuntimeError(f"crt.sh returned unexpected JSON shape for {domain}")

        return self._parse(data, domain)

    def _parse(self, data: list, domain: str) -> set[str]:
        target = domain.lower().strip().strip(".")
        suffix = "." + target
        out: set[str] = set()

        for entry in data:
            if not isinstance(entry, dict):
                continue
            for key in ("name_value", "common_name"):
                raw = entry.get(key)
                if not raw:
                    continue
                # jq -r emits each name; embedded newlines become separate lines
                for name in str(raw).splitlines():
                    name = name.strip().lower()
                    # sed 's/\*\.//g' → strip wildcard prefixes anywhere
                    name = name.replace("*.", "")
                    # drop FQDN trailing dot if present
                    name = name.rstrip(".")
                    if not name:
                        continue
                    # keep only the target domain or a real subdomain of it
                    if name == target or name.endswith(suffix):
                        out.add(name)

        return out