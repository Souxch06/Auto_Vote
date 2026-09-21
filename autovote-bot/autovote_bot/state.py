"""État persistant du bot : dernière heure de vote par site, compteurs, erreurs."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("autovote.state")


class State:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("état illisible (%s) — repart de zéro", e)
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- accès par site ----------------------------------------------------
    def entry(self, site: str) -> dict[str, Any]:
        return self.data.setdefault(site, {
            "last_vote": None,
            "next_vote": None,
            "last_result": None,
            "last_error": None,
            "attempts": 0,
            "solved": 0,
            "verified": 0,
        })

    def last_vote(self, site: str) -> Optional[datetime]:
        raw = self.entry(site).get("last_vote")
        return datetime.fromisoformat(raw) if raw else None

    def record_vote(self, site: str, now: datetime, result: str, error: str = "") -> None:
        e = self.entry(site)
        e["last_vote"] = now.isoformat(timespec="seconds")
        e["last_result"] = result
        e["last_error"] = error or None
        e["attempts"] += 1
        if result == "solved":
            e["solved"] = e.get("solved", 0) + 1
        if result == "verified":
            e["verified"] = e.get("verified", 0) + 1

    def set_next(self, site: str, when: datetime) -> None:
        self.entry(site)["next_vote"] = when.isoformat(timespec="seconds")

    def summary(self) -> list[dict[str, Any]]:
        out = []
        for site, e in self.data.items():
            out.append({
                "site": site,
                "last_vote": e.get("last_vote"),
                "next_vote": e.get("next_vote"),
                "last_result": e.get("last_result"),
                "attempts": e.get("attempts", 0),
                "solved": e.get("solved", 0),
            })
        return out
