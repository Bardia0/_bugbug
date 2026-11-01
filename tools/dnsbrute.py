"""
DNS brute-force via shuffledns + massdns, with cached wordlists.

Replaces the shell-based implementation. Key behaviours:

  * Wordlists and resolvers are cached under ~/.cache/bbtool/dnsbrute/
    (XDG_CACHE_HOME respected). Downloaded once, reused across runs.
  * Default wordlist is assetnote's *small* `best-dns-wordlist.txt`
    (~57k entries) — fast and covers 95% of real-world subdomains.
    Set DNSBRUTE_WORDLIST=/path/to/bigger.txt to use your own combined
    list (e.g. best-dns-wordlist + 2m-subdomains + custom crunch output).
  * Short random labels (1..3 chars) are generated in Python, replacing
    `crunch 1 4 ...`. Disable with DNSBRUTE_SHORT_MAXLEN=0.
  * All intermediate files live in a TemporaryDirectory that is cleaned
    up automatically, even if the job crashes.
  * A hard timeout is enforced; the process group is killed on expiry so
    orphaned massdns children don't linger.

Environment variables:
    DNSBRUTE_WORDLIST       path to a custom wordlist (overrides download)
    DNSBRUTE_WORDLIST_URL   override the default download URL
    DNSBRUTE_RESOLVERS      path to a custom resolvers file
    DNSBRUTE_RESOLVERS_URL  override the default resolvers URL
    DNSBRUTE_SHORT_MAXLEN   max length of generated short labels (0=off, default 3)
    DNSBRUTE_THREADS        shuffledns -t value (default 200)
    DNSBRUTE_TIMEOUT        per-domain timeout in seconds (default 600)
"""
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import requests

from .base import DiscoveryTool

log = logging.getLogger(__name__)


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    d = base / "bbtool" / "dnsbrute"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, dest: Path, timeout: int = 120) -> Path:
    """Download `url` → `dest` atomically, unless already cached."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    log.info("dnsbrute: downloading %s → %s", url, dest)
    resp = requests.get(url, timeout=timeout, stream=True)
    resp.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                fh.write(chunk)
    tmp.replace(dest)
    return dest


class DnsBruteTool(DiscoveryTool):
    name = "dnsbrute"
    description = "DNS brute-force via shuffledns + cached wordlist (slow, ~1-3 min/domain)"

    WORDLIST_URL = "https://wordlists-cdn.assetnote.io/data/manual/best-dns-wordlist.txt"
    RESOLVERS_URL = "https://raw.githubusercontent.com/trickest/resolvers/main/resolvers.txt"
    SHORT_LABEL_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
    SHORT_LABEL_MAXLEN = 3  # 0 disables short-label generation

    # ---------------------------------------------------------------- helpers

    def _wordlist_path(self) -> Path:
        custom = os.environ.get("DNSBRUTE_WORDLIST")
        if custom:
            p = Path(os.path.expanduser(custom))
            if not p.is_file():
                raise RuntimeError(f"DNSBRUTE_WORDLIST points to missing file: {p}")
            return p
        url = os.environ.get("DNSBRUTE_WORDLIST_URL", self.WORDLIST_URL)
        return _download(url, _cache_dir() / "best-dns-wordlist.txt")

    def _resolvers_path(self) -> Path:
        custom = os.environ.get("DNSBRUTE_RESOLVERS")
        if custom:
            p = Path(os.path.expanduser(custom))
            if not p.is_file():
                raise RuntimeError(f"DNSBRUTE_RESOLVERS points to missing file: {p}")
            return p
        url = os.environ.get("DNSBRUTE_RESOLVERS_URL", self.RESOLVERS_URL)
        return _download(url, _cache_dir() / "resolvers.txt")

    def _gen_short_labels(self) -> list[str]:
        try:
            n = int(os.environ.get("DNSBRUTE_SHORT_MAXLEN", str(self.SHORT_LABEL_MAXLEN)))
        except ValueError:
            n = self.SHORT_LABEL_MAXLEN
        if n <= 0:
            return []

        out: list[str] = []

        def rec(prefix: str, depth: int) -> None:
            if depth == 0:
                out.append(prefix)
                return
            for c in self.SHORT_LABEL_CHARS:
                rec(prefix + c, depth - 1)

        for length in range(1, n + 1):
            rec("", length)
        return out

    # ---------------------------------------------------------------- ABC impl

    def is_available(self) -> bool:
        return (
            shutil.which("shuffledns") is not None
            and shutil.which("massdns") is not None
        )

    def discover(self, domain: str, timeout: int = 600) -> set[str]:
        if not self.is_available():
            raise RuntimeError(
                "shuffledns and/or massdns not installed or not in PATH"
            )

        domain = domain.lower().strip().rstrip(".")
        if not domain:
            raise RuntimeError("Empty domain")

        # allow per-run timeout override
        try:
            timeout = int(os.environ.get("DNSBRUTE_TIMEOUT", str(timeout)))
        except ValueError:
            pass
        try:
            threads = int(os.environ.get("DNSBRUTE_THREADS", "200"))
        except ValueError:
            threads = 200

        wordlist = self._wordlist_path()
        resolvers = self._resolvers_path()

        # Build the candidate set (single pass, in memory; 50k-3M strings is fine)
        words: set[str] = set()
        with open(wordlist, "r", errors="ignore") as fh:
            for line in fh:
                w = line.strip().lower()
                if w:
                    words.add(w)

        words.update(self._gen_short_labels())
        if not words:
            raise RuntimeError(f"Wordlist {wordlist} is empty")

        with tempfile.TemporaryDirectory(prefix="bbtool-dnsbrute-") as tmpdir:
            tmp = Path(tmpdir)
            fqdn_file = tmp / f"{domain}.fqdn.txt"
            out_file  = tmp / f"{domain}.out.txt"

            with open(fqdn_file, "w") as fh:
                for w in sorted(words):
                    fh.write(f"{w}.{domain}\n")

            cmd = [
                "shuffledns",
                "-d", domain,
                "-list", str(fqdn_file),
                "-r", str(resolvers),
                "-m", shutil.which("massdns"),
                "-mode", "resolve",
                "-t", str(threads),
                "-silent",
            ]
            log.info("dnsbrute: %d candidates, cmd=%s", len(words), " ".join(cmd))

            with open(out_file, "w") as fout:
                proc = subprocess.Popen(
                    cmd,
                    stdout=fout,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,  # new process group → we can killpg
                )
                try:
                    _, stderr = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
                    raise RuntimeError(
                        f"shuffledns timed out after {timeout}s for {domain} "
                        f"({len(words)} candidates)"
                    )

            if stderr.strip():
                log.debug("dnsbrute stderr for %s: %s", domain, stderr.strip()[:500])

            result: set[str] = set()
            if out_file.exists():
                with open(out_file, "r", errors="ignore") as fh:
                    for line in fh:
                        h = line.strip().lower().rstrip(".")
                        if h and (h == domain or h.endswith("." + domain)):
                            result.add(h)

            log.info("dnsbrute: %d hits for %s", len(result), domain)
            return result