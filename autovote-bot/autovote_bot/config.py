"""Configuration du bot (JSON) — chargement, validation, valeurs par défaut."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class ConfigError(ValueError):
    pass


@dataclass
class ServerConfig:
    name: str = ""
    hub_url: str = ""
    nick: str = ""
    vote_verify: str = ""   # marqueur (texte, #id ou .classe) sur la page serveur

    def from_dict(self, d: dict[str, Any]) -> "ServerConfig":
        self.name = str(d.get("name") or "")
        self.hub_url = str(d.get("hubUrl") or d.get("hub_url") or "")
        self.nick = str(d.get("nick") or "")
        self.vote_verify = str(d.get("voteVerify") or "")
        return self


@dataclass
class SiteConfig:
    url: str = ""
    cooldown_hours: Optional[float] = None
    verify: str = ""          # marqueur spécifique à ce site (prioritaire)
    sitekey: str = ""         # force la sitekey (sinon auto-détection)
    captcha: str = ""         # force le type de CAPTCHA (sinon auto-détection)

    def from_dict(self, d: dict[str, Any]) -> "SiteConfig":
        self.url = str(d.get("url") or "").strip()
        ch = d.get("cooldownHours", d.get("cooldown_hours"))
        self.cooldown_hours = float(ch) if ch is not None else None
        self.verify = str(d.get("verify") or "")
        self.sitekey = str(d.get("sitekey") or "")
        self.captcha = str(d.get("captcha") or "")
        return self


@dataclass
class TimingConfig:
    jitter_minutes: tuple[int, int] = (5, 20)
    night_start: str = ""
    night_end: str = ""
    poll_seconds: int = 60
    gap_between_sites_seconds: int = 45

    def from_dict(self, d: dict[str, Any]) -> "TimingConfig":
        j = d.get("jitterMinutes") or d.get("jitter_minutes") or [5, 20]
        try:
            self.jitter_minutes = (int(j[0]), int(j[1]))
        except Exception:
            self.jitter_minutes = (5, 20)
        night = d.get("nightPause") or d.get("night_pause") or []
        self.night_start = str(night[0]) if len(night) > 0 else ""
        self.night_end = str(night[1]) if len(night) > 1 else ""
        self.poll_seconds = int(d.get("pollSeconds", d.get("poll_seconds", 60)) or 60)
        self.gap_between_sites_seconds = int(d.get("gapBetweenSitesSeconds", 45) or 45)
        return self


@dataclass
class SolverConfig:
    base_url: str = "http://127.0.0.1:8877"
    auto_start: bool = True
    headless: bool = False
    timeout_seconds: int = 180
    proxy: str = ""
    repo_root: str = ""   # répertoire racine du repo (contient server.py)

    def from_dict(self, d: dict[str, Any]) -> "SolverConfig":
        self.base_url = str(d.get("baseUrl", d.get("base_url", self.base_url)))
        self.auto_start = bool(d.get("autoStart", d.get("auto_start", True)))
        self.headless = bool(d.get("headless", False))
        self.timeout_seconds = int(d.get("timeoutSeconds", 180) or 180)
        self.proxy = str(d.get("proxy") or "")
        self.repo_root = str(d.get("repoRoot", d.get("repo_root", "")))
        return self


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    sites: list[SiteConfig] = field(default_factory=list)
    timing: TimingConfig = field(default_factory=TimingConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    state_path: str = ""
    log_file: str = ""
    _path: Optional[Path] = None

    def from_dict(self, d: dict[str, Any]) -> "Config":
        self.server.from_dict(d.get("server") or {})
        self.sites = [SiteConfig().from_dict(s) for s in d.get("sites") or []]
        self.timing.from_dict(d.get("timing") or {})
        self.solver.from_dict(d.get("solver") or {})
        self.state_path = str(d.get("statePath", d.get("state_path", "")))
        self.log_file = str(d.get("logFile", d.get("log_file", "")))
        return self

    def validate(self) -> None:
        if not self.server.hub_url:
            raise ConfigError("server.hubUrl est vide (URL de la page de vote du serveur)")
        if not self.server.nick:
            raise ConfigError("server.nick est vide (pseudo Minecraft)")
        if not self.sites:
            raise ConfigError("aucun site de vote — utilise --detect pour les trouver depuis la page serveur")
        for s in self.sites:
            if not s.url.startswith(("http://", "https://")):
                raise ConfigError(f"url de site invalide: {s.url!r}")
        lo, hi = self.timing.jitter_minutes
        if hi < lo:
            raise ConfigError("timing.jitterMinutes : max < min")

    @property
    def state_file(self) -> Path:
        if self.state_path:
            return Path(self.state_path)
        base = self._path.parent if self._path else Path(".")
        return base / "state.json"

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": {
                "name": self.server.name,
                "hubUrl": self.server.hub_url,
                "nick": self.server.nick,
                "voteVerify": self.server.vote_verify,
            },
            "sites": [
                {
                    "url": s.url,
                    **({"cooldownHours": s.cooldown_hours} if s.cooldown_hours is not None else {}),
                    **({"verify": s.verify} if s.verify else {}),
                    **({"sitekey": s.sitekey} if s.sitekey else {}),
                    **({"captcha": s.captcha} if s.captcha else {}),
                }
                for s in self.sites
            ],
            "timing": {
                "jitterMinutes": list(self.timing.jitter_minutes),
                "nightPause": [self.timing.night_start, self.timing.night_end] if self.timing.night_start else [],
                "pollSeconds": self.timing.poll_seconds,
                "gapBetweenSitesSeconds": self.timing.gap_between_sites_seconds,
            },
            "solver": {
                "baseUrl": self.solver.base_url,
                "autoStart": self.solver.auto_start,
                "headless": self.solver.headless,
                "timeoutSeconds": self.solver.timeout_seconds,
                **({"proxy": self.solver.proxy} if self.solver.proxy else {}),
                **({"repoRoot": self.solver.repo_root} if self.solver.repo_root else {}),
            },
            **({"statePath": self.state_path} if self.state_path else {}),
            **({"logFile": self.log_file} if self.log_file else {}),
        }


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config introuvable: {p} (copie config.example.json)")
    cfg = Config().from_dict(json.loads(p.read_text(encoding="utf-8")))
    cfg._path = p
    return cfg


def save_config(path: str | Path, cfg: Config) -> None:
    Path(path).write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
