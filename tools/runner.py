import logging

from . import enabled_tools

log = logging.getLogger(__name__)


def extract_scope_domains(scope: list[str]) -> list[str]:
    """Normalise scope entries (strip protocol, wildcard, path, port)."""
    out, seen = [], set()
    for entry in scope or []:
        if not entry:
            continue
        d = entry.strip().lower()
        if "://" in d:
            d = d.split("://", 1)[1]
        d = d.split("/", 1)[0]
        if d.startswith("*."):
            d = d[2:]
        d = d.split(":", 1)[0]
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def run_discovery(
    domains: list[str],
    tools: list[str] | None = None,
    on_domain_start=None,
    on_domain_done=None,
    on_host_found=None,
) -> dict:
    """Run every enabled tool against every domain and merge the results.

    Callbacks (all optional):
        on_domain_start(domain)               – before a domain is scanned
        on_host_found(host, tool_name)        – per (host, tool) discovery hit
        on_domain_done(domain, hosts)         – after all tools finished a domain

    Returns:
        {
          "per_domain": {domain: [hosts]},
          "sources":    {host: [tool names]},
          "total":      int,
          "errors":     [{tool, domain, error}],
        }
    """
    available = enabled_tools()
    if tools:
        available = [t for t in available if t.name in tools]
    if not available:
        raise RuntimeError("No discovery tools available on PATH.")
    if not domains:
        raise RuntimeError("No valid domains to scan.")

    per_domain: dict[str, set[str]] = {}
    sources: dict[str, set[str]] = {}
    errors: list[dict] = []

    for domain in domains:
        if on_domain_start:
            on_domain_start(domain)
        hosts: set[str] = set()
        for tool in available:
            try:
                found = tool.discover(domain)
                hosts |= found
                for host in found:
                    sources.setdefault(host, set()).add(tool.name)
                    if on_host_found:
                        on_host_found(host, tool.name)
            except Exception as exc:
                log.exception("Tool %s failed on %s", tool.name, domain)
                errors.append({"tool": tool.name, "domain": domain, "error": str(exc)})
        per_domain[domain] = hosts
        if on_domain_done:
            on_domain_done(domain, hosts)

    return {
        "per_domain": {d: sorted(h) for d, h in per_domain.items()},
        "sources": {h: sorted(s) for h, s in sources.items()},
        "total": len(sources),
        "errors": errors,
    }