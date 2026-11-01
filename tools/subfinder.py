import shutil
import subprocess

from .base import DiscoveryTool


class SubfinderTool(DiscoveryTool):
    name = "subfinder"
    description = "Passive subdomain enumeration (subfinder -d {domain} -all)"

    def is_available(self) -> bool:
        return shutil.which("subfinder") is not None

    def discover(self, domain: str, timeout: int = 300) -> set[str]:
        if not self.is_available():
            raise RuntimeError("subfinder is not installed or not in PATH")

        cmd = ["subfinder", "-d", domain, "-all", "-silent"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"subfinder timed out after {timeout}s for {domain}")

        results: set[str] = set()
        for line in proc.stdout.splitlines():
            line = line.strip().lower()
            if not line or line.startswith("#"):
                continue
            results.add(line)
        return results