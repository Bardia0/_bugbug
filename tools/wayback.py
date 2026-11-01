"""
Wayback Machine URL harvesting via `waybackurls`.

Faithful Python port of:

    echo {domain} | waybackurls | unfurl domain | sort -u

See `tools/_url_utils.py` for the difference between MODE="domain"
(faithful to `unfurl domain`) and MODE="host" (what subdomain discovery
actually needs — returns subdomains, not apexes).
"""
import shutil
import subprocess

from .base import DiscoveryTool
from ._url_utils import extract_hosts


class WaybackTool(DiscoveryTool):
    name = "wayback"
    description = "Wayback Machine URLs via waybackurls + unfurl domain/host"

    # ── pipeline behaviour ───────────────────────────────────────────
    # "domain" → unfurl domain  (apex + third-party domains, no target filter)
    # "host"   → unfurl host    (real subdomains under target only)
    MODE = "domain"
    # ─────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return shutil.which("waybackurls") is not None

    def discover(self, domain: str, timeout: int = 300) -> set[str]:
        if not self.is_available():
            raise RuntimeError("waybackurls is not installed or not in PATH")

        try:
            proc = subprocess.run(
                ["waybackurls"],
                input=domain,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"waybackurls timed out after {timeout}s for {domain}")

        return extract_hosts(proc.stdout.splitlines(), domain, mode=self.MODE)