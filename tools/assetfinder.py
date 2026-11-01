"""
Subdomain enumeration via `assetfinder`.

Command:
    assetfinder --subs-only {domain}

`--subs-only` restricts output to the target domain and its subdomains.
Without it, assetfinder also returns "related" third-party domains it
discovered via shared certificate / crt.sh pivots — those are not
subdomains of the target and would pollute the subdomain table.

Output is already hostnames (no URLs), so no `unfurl` step is required.
`extract_hosts(..., mode="host")` is used only to normalise case /
trailing dots and to drop anything that is not the target or a subdomain.
"""
import shutil
import subprocess

from .base import DiscoveryTool
from ._url_utils import extract_hosts


class AssetfinderTool(DiscoveryTool):
    name = "assetfinder"
    description = "Passive subdomain enumeration via assetfinder --subs-only"

    def is_available(self) -> bool:
        return shutil.which("assetfinder") is not None

    def discover(self, domain: str, timeout: int = 120) -> set[str]:
        if not self.is_available():
            raise RuntimeError("assetfinder is not installed or not in PATH")

        try:
            proc = subprocess.run(
                ["assetfinder", "--subs-only", domain],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"assetfinder timed out after {timeout}s for {domain}")

        return extract_hosts(proc.stdout.splitlines(), domain, mode="host")