"""
Shared URL → domain/host extraction helpers (Python port of `unfurl`).

`extract_hosts(urls, target, mode=...)`:

  mode="domain"  →  equivalent to `unfurl domain`
                    returns the registered domain (eTLD+1) of every URL,
                    WITHOUT filtering to the target. Faithful to:
                        waybackurls | unfurl domain | sort -u
                    Output typically = target apex + any third-party domains
                    that appear in the input URLs.

  mode="host"    →  equivalent to `unfurl host` + `grep '\\.target$'`
                    returns the full hostname of every URL that is the
                    target or a subdomain of the target. This is what
                    "subdomain discovery" actually needs.

PSL resolution uses tldextract's bundled snapshot only
(`suffix_list_urls=()`) — no network call, works offline.
"""
from urllib.parse import urlparse

import tldextract

_extractor = tldextract.TLDExtract(suffix_list_urls=())


def extract_hosts(urls, target: str, mode: str = "domain") -> set[str]:
    """Return a set of hosts (or registered domains) parsed from `urls`."""
    out: set[str] = set()
    target = (target or "").lower().strip().rstrip(".")
    suffix = "." + target if target else ""

    for raw in urls:
        raw = (raw or "").strip()
        if not raw:
            continue
        # Bare "sub.example.com/path" without scheme
        if "://" not in raw:
            raw = "//" + raw
        try:
            parsed = urlparse(raw)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            continue

        if mode == "host":
            # keep only the target itself or its subdomains
            if host == target or (suffix and host.endswith(suffix)):
                out.add(host)
        else:  # "domain"
            reg = _extractor(host).registered_domain
            if reg:
                out.add(reg)

    return out