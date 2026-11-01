"""
GetAllUrls (gau) harvesting.

Faithful Python port of:

    gau {domain} --threads 10 --subs | unfurl domain | sort -u

See `tools/_url_utils.py` for the MODE="domain" vs MODE="host" difference.
`--subs` tells gau to include URLs on subdomains of the target domain.
"""
import shutil
import subprocess

from .base import DiscoveryTool
from ._url_utils import extract_hosts


class GauTool(DiscoveryTool):
    name = "gau"
    description = "GetAllUrls (gau --subs) + unfurl domain/host"

    # ── pipeline behaviour ───────────────────────────────────────────
    # "domain" → unfurl domain  (apex + third-party domains, no target filter)
    # "host"   → unfurl host    (real subdomains under target only)
    MODE = "domain"
    # ─────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return shutil.which("gau") is not None

    def discover(self, domain: str, timeout: int = 300) -> set[str]:
        if not self.is_available():
            raise RuntimeError("gau is not installed or not in PATH")

        try:
            proc = subprocess.run(
                ["gau", domain, "--threads", "10", "--subs"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"gau timed out after {timeout}s for {domain}")

        return extract_hosts(proc.stdout.splitlines(), domain, mode=self.MODE)