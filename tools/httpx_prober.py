import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class HttpxProber:
    name = "httpx"
    description = "HTTP probing via httpx"

    def _get_binary(self) -> str | None:
        # 1. Prioritize the standard Go bin path to bypass the Python httpx wrapper
        go_bin = os.path.expanduser("~/go/bin/httpx")
        if os.path.isfile(go_bin) and os.access(go_bin, os.X_OK):
            return go_bin
        
        # 2. Fallback to PATH (if Go bin is in PATH without the venv conflict)
        path = shutil.which("httpx")
        if path:
            return path
            
        return None

    def is_available(self) -> bool:
        binary = self._get_binary()
        if not binary:
            return False
        try:
            # Run help command to verify it's the ProjectDiscovery binary
            res = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=5)
            # ProjectDiscovery binary supports -silent and -json. Python httpx does not.
            if "-silent" in res.stdout or "-json" in res.stdout:
                return True
        except Exception:
            pass
        return False

    def probe(self, hostnames: list[str], timeout: int = 600) -> list[dict]:
        binary = self._get_binary()
        if not binary or not self.is_available():
            raise RuntimeError("Wrong httpx binary detected (Python library instead of Go binary)")
        if not hostnames:
            raise RuntimeError("No hostnames to probe")

        clean = []
        seen = set()
        for h in hostnames:
            h = (h or "").strip().lower().rstrip(".")
            if h and h not in seen:
                seen.add(h)
                clean.append(h)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
                tmp_path = Path(fh.name)
                for h in clean:
                    fh.write(h + "\n")

            cmd = [
                binary,
                "-silent",
                "-json",
                "-status-code",
                "-title",
                "-tech-detect",
                "-server",
                "-ip",
                "-cname",
                "-threads", "50",
                "-l", str(tmp_path),
            ]
            log.info("httpx: probing %d hostnames (timeout=%ss)", len(clean), timeout)

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
                raise RuntimeError(f"httpx timed out after {timeout}s")

            if stderr.strip():
                log.debug("httpx stderr: %s", stderr.strip()[:500])

            results = []
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

                hostname = str(obj.get("input") or "")
                if not hostname:
                    continue

                url = str(obj.get("url") or "")
                status_code = int(obj.get("status_code") or 0)
                title = str(obj.get("title") or "")
                tech = [str(x) for x in (obj.get("tech") or [])]
                server = str(obj.get("webserver") or "")
                
                ip = str(obj.get("host") or "")
                if not ip:
                    a_records = obj.get("a") or []
                    ip = ", ".join(str(x) for x in a_records)
                    
                cname_list = obj.get("cname") or []
                cname = ", ".join(str(x) for x in cname_list)

                results.append({
                    "hostname": hostname,
                    "url": url,
                    "status_code": status_code,
                    "title": title,
                    "tech": tech,
                    "server": server,
                    "ip": ip,
                    "cname": cname,
                })

            log.info("httpx: %d probed successfully", len(results))
            return results

        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass