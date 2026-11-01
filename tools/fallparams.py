import os
import shutil
import subprocess
import tempfile
import logging

log = logging.getLogger(__name__)

class FallparamsTool:
    name = "fallparams"
    description = "Parameter discovery using fallparams"

    def is_available(self) -> bool:
        return shutil.which("fallparams") is not None

    def discover(self, urls: list[str], timeout: int = 1200) -> set[str]:
        if not self.is_available():
            raise RuntimeError("fallparams is not installed or not in PATH")

        if not urls:
            return set()

        # Write URLs to a temporary file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f_in:
            for url in urls:
                f_in.write(url + "\n")
            input_file = f_in.name

        output_file = input_file + ".out.txt"

        try:
            # Try with -duc first as requested
            cmd = ["fallparams", "-u", input_file, "-o", output_file, "-disable-update-check"]
            log.info("Running command: %s", " ".join(cmd))
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

            # If it fails, retry without -duc (since you mentioned it's sometimes needed, sometimes not)
            if proc.returncode != 0:
                log.warning("fallparams with -duc failed, retrying without -duc. stderr: %s", proc.stderr)
                cmd = ["fallparams", "-u", input_file, "-o", output_file]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)

            results = set()
            if os.path.exists(output_file):
                with open(output_file, "r") as f:
                    for line in f:
                        word = line.strip()
                        if word:
                            results.add(word)
            else:
                log.warning("fallparams output file not created. stderr: %s", proc.stderr)
                
            return results
        except subprocess.TimeoutExpired:
            log.error("fallparams timed out after %s seconds", timeout)
            return set()
        except Exception as e:
            log.error("Error running fallparams: %s", e)
            return set()
        finally:
            # Cleanup temp files
            if os.path.exists(input_file):
                os.remove(input_file)
            if os.path.exists(output_file):
                os.remove(output_file)