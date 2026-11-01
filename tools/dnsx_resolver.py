"""
DNS resolution via ProjectDiscovery's dnsx.

Resolves a list of hostnames to A / AAAA / CNAME records and returns a
dict of the form:

    {
      "api.example.com": {"a": ["1.2.3.4"], "aaaa": [], "cname": []},
      "cdn.example.com": {"a": [], "aaaa": [], "cname": ["cdn.cf.com"]},
    }

Only hostnames that produced at least one A/AAAA/CNAME record appear
in the output. Hosts that return NOERROR with no relevant records are
silently dropped (they'd be noise for bug-bounty work).

The subprocess runs in a new session so timeouts can kill the whole
process group (dnsx doesn't spawn children, but the pattern is
consistent with dnsbrute and future-proofs it).
"""
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class DnsxResolver:
    name = "dnsx"
    description = "DNS resolution (A/AAAA/CNAME) via dnsx"

    def is_available(self) -> bool:
        return shutil.which("dnsx") is not None

    def resolve(self, hostnames: list[str], timeout: int = 600) -> dict[str, dict]:
        if not self.is_available():
            raise RuntimeError("dnsx is not installed or not in PATH")
        if not hostnames:
            raise RuntimeError("No hostnames to resolve")

        # Dedupe + normalise once, in memory
        clean: list[str] = []
        seen: set[str] = set()
        for h in hostnames:
            h = (h or "").strip().lower().rstrip(".")
            if h and h not in seen:
                seen.add(h)
                clean.append(h)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8"
            ) as fh:
                tmp_path = Path(fh.name)
                for h in clean:
                    fh.write(h + "\n")

            cmd = [
                "dnsx",
                "-silent",
                "-json",
                "-a", "-aaaa", "-cname",
                "-retry", "2",
                "-t", "100",
                "-l", str(tmp_path),
            ]
            log.info("dnsx: resolving %d hostnames (timeout=%ss)", len(clean), timeout)

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise RuntimeError(f"dnsx timed out after {timeout}s")

            if stderr.strip():
                log.debug("dnsx stderr: %s", stderr.strip()[:500])

            results: dict[str, dict] = {}
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                host = (obj.get("host") or "").strip().lower().rstrip(".")
                if not host:
                    continue

                a     = [str(x) for x in (obj.get("a") or [])]
                aaaa  = [str(x) for x in (obj.get("aaaa") or [])]
                cname = [str(x) for x in (obj.get("cname") or [])]

                # Only count as resolved if at least one relevant record exists
                if not (a or aaaa or cname):
                    continue

                results[host] = {"a": a, "aaaa": aaaa, "cname": cname}

            log.info("dnsx: %d/%d resolved", len(results), len(clean))
            return results

        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass