import json
import logging
import os
import shutil
import signal
import subprocess
from urllib.parse import urlparse

log = logging.getLogger(__name__)

class WaybackCrawler:
    name = "waybackurls"
    description = "Wayback Machine URL crawling"

    def is_available(self) -> bool:
        return shutil.which("waybackurls") is not None

    def crawl(self, urls: list[str], timeout: int = 300, on_result=None, **kwargs) -> list[dict]:
        if not self.is_available():
            raise RuntimeError("waybackurls is not installed or not in PATH")

        results = []
        for url in urls:
            # waybackurls expects the domain name (e.g. example.com), not the full URL
            parsed = urlparse(url)
            hostname = parsed.hostname or parsed.path.split('/')[0]
            if not hostname:
                continue
                
            try:
                proc = subprocess.Popen(
                    ["waybackurls"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                stdout, stderr = proc.communicate(input=hostname + "\n", timeout=timeout)
                
                if stderr and stderr.strip():
                    log.warning("waybackurls stderr for %s: %s", hostname, stderr.strip()[:500])
                
                count = 0
                for line in stdout.splitlines():
                    u = line.strip()
                    if u and u.startswith("http"):
                        res = {"url": u, "source": self.name}
                        results.append(res)
                        if on_result:
                            on_result(res)
                        count += 1
                
                log.info("waybackurls found %d URLs for %s", count, hostname)
                            
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                log.warning("waybackurls timed out for %s", hostname)
            except Exception as e:
                log.error("waybackurls failed for %s: %s", hostname, e)

        return results


class KatanaCrawler:
    name = "katana"
    description = "Next-generation crawling via katana"

    def is_available(self) -> bool:
        return shutil.which("katana") is not None

    def crawl(self, urls: list[str], timeout: int = 600, on_result=None, **kwargs) -> list[dict]:
        if not self.is_available():
            raise RuntimeError("katana is not installed or not in PATH")

        # Extract dynamic options
        katana_opts = kwargs.get("katana_opts", {})
        depth = str(katana_opts.get("d", "3"))
        concurrency = str(katana_opts.get("c", "5"))
        rate_limit = str(katana_opts.get("rl", "30"))
        custom_headers = kwargs.get("custom_headers", [])

        results = []
        for url in urls:
            if not url.startswith("http"):
                url = "http://" + url
                
            try:
                cmd = [
                    "katana", "-u", url, "-d", depth, "-s", "breadth-first",
                    "-jc", "-kf", "all", "-fs", "fqdn",
                    "-timeout", "10", "-retry", "1", "-c", concurrency, "-rl", rate_limit,
                    "-j", "-duc", "-nc", "-silent",
                    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "-H", 'Sec-Ch-Ua: "Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
                    "-H", "Sec-Ch-Ua-Mobile: ?0",
                    "-H", 'Sec-Ch-Ua-Platform: "Windows"',
                    "-H", "Sec-Fetch-Dest: document",
                    "-H", "Sec-Fetch-Mode: navigate",
                    "-H", "Sec-Fetch-Site: none",
                    "-H", "Sec-Fetch-User: ?1",
                    "-H", "Upgrade-Insecure-Requests: 1"
                ]
                
                # Append custom headers from the UI
                for h in custom_headers:
                    h = h.strip()
                    if h:
                        cmd.extend(["-H", h])
                
                log.info("Starting katana for %s", url)
                
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                stdout, stderr = proc.communicate(timeout=timeout)
                
                if stderr and stderr.strip():
                    log.warning("katana stderr for %s: %s", url, stderr.strip()[:500])

                if proc.returncode != 0:
                    log.error("katana exited with code %d for %s", proc.returncode, url)
                    continue

                count = 0
                for line in stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Parse JSON output from Katana (-j flag)
                    if line.startswith("{"):
                        try:
                            obj = json.loads(line)
                            endpoint = obj.get("request", {}).get("endpoint")
                            if endpoint:
                                res = {"url": endpoint, "source": self.name}
                                results.append(res)
                                if on_result:
                                    on_result(res)
                                count += 1
                        except json.JSONDecodeError:
                            pass
                    else:
                        # Fallback for plain text output
                        if line.startswith("http"):
                            res = {"url": line, "source": self.name}
                            results.append(res)
                            if on_result:
                                on_result(res)
                            count += 1
                
                log.info("katana found %d URLs for %s", count, url)
                            
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                log.warning("katana timed out for %s", url)
            except Exception as e:
                log.error("katana failed for %s: %s", url, e)

        return results