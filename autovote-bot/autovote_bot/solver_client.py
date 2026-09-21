"""Client HTTP du sidecar « captcha-solver » (le serveur `server.py` du repo).

Le sidecar résout les CAPTCHAs dans un vrai navigateur anti-détection
(CloakBrowser) et renvoie un token rejouable. Le bot ne fait que l'appeler :
  * `POST /solve`  -> {"solved": bool, "token": ..., "error": ...}
  * `GET  /health` -> liveness + types supportés

En mode `real_page`, le sidecar navigue LUI-MÊME sur la page de vote, exécute
les `pre_actions` (remplir le pseudo, cliquer), résout le CAPTCHA et le
soumissionnement se fait depuis la même session/IP — c'est ce qui garantit la
cohérence du token (même origine, même proxy).

Le sidecar est démarré automatiquement si absent (sauf `auto_start=false`).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("autovote.solver")


class SolverError(RuntimeError):
    pass


class SolverClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8877",
                 auto_start: bool = True,
                 headless: bool = False,
                 repo_root: str = "",
                 start_wait_s: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.auto_start = auto_start
        self.headless = headless
        self.repo_root = repo_root
        self.start_wait_s = start_wait_s
        self._proc: subprocess.Popen | None = None

    # -- cycle de vie -------------------------------------------------------
    def health(self) -> dict | None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def ensure_running(self) -> None:
        if self.health():
            return
        if not self.auto_start:
            raise SolverError(
                f"solveur inatteignable sur {self.base_url} (auto_start désactivé) — "
                f"lance `python server.py` dans la racine du repo"
            )
        root = Path(self.repo_root) if self.repo_root else Path(__file__).resolve().parents[2]
        server_py = root / "server.py"
        if not server_py.exists():
            raise SolverError(f"server.py introuvable dans {root}")
        env = dict(os.environ)
        env["BROWSER_HEADLESS"] = "1" if self.headless else "0"
        log.info("démarrage du solveur: %s (headless=%s)", server_py, self.headless)
        self._proc = subprocess.Popen(
            [sys.executable, str(server_py)],
            cwd=str(root), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.start_wait_s
        while time.monotonic() < deadline:
            if self.health():
                log.info("solveur prêt")
                return
            time.sleep(1)
        raise SolverError(f"le solveur n'a pas répondu au bout de {int(self.start_wait_s)} s")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    # -- résolution ----------------------------------------------------------
    def solve(self, payload: dict, timeout_s: int = 180) -> dict:
        """POST /solve. Lève SolverError si le CAPTCHA n'est pas résolu."""
        self.ensure_running()
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/solve", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s + 30) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail", "")
            except Exception:  # noqa: BLE001
                pass
            raise SolverError(f"HTTP {e.code} du solveur: {detail or e.reason}") from None
        except Exception as e:  # noqa: BLE001
            raise SolverError(f"erreur réseau vers le solveur: {e}") from None
        if not data.get("solved"):
            raise SolverError(f"CAPTCHA non résolu: {data.get('error') or data.get('method') or 'inconnu'}")
        return data
