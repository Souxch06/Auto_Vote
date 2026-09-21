"""Planification des votes : cooldown par site, délai aléatoire, pause nocturne.

C'est le cœur du comportement « naturel » (cf. analyse du bot Voxa) :
  * chaque site de vote a SON propre cooldown (le compte à rebours de l'un ne
    bloque pas les autres) ;
  * un délai aléatoire (jitter) est ajouté à chaque planification ;
  * une pause nocturne (fenêtre horaire, ex. 02:00-08:00) retarde tout vote qui
    tomberait dans la nuit — la fenêtre peut passer minuit (ex. 23:00-01:00).

Tout est testable sans navigateur : les méthodes ne font que de l'arithmétique
de dates.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class NightWindow:
    """Fenêtre nocturne "HH:MM" -> "HH:MM". Vide = désactivé."""
    start: str = ""
    end: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.start and self.end)


def _parse_hhmm(value: str, now: datetime) -> Optional[datetime]:
    """'HH:MM' -> datetime aujourd'hui (base de `now`). None si invalide."""
    try:
        hh, mm = value.strip().split(":")
        hh, mm = int(hh), int(mm)
    except Exception:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def shift_out_of_night_window(dt: datetime, window: NightWindow) -> datetime:
    """Décale `dt` hors de la fenêtre nocturne (si y est), sur son bord de fin.

    Miroir exact de la fonction `shiftOutOfNightWindow` de l'extension :
      * fenêtre dans le jour   : [start, end)      -> end (même jour)
      * fenêtre qui passe minuit : [start, 24h) U [0h, end)
          - dt dans [start, 24h)  -> end (+1 jour)
          - dt dans [0h, end)     -> end (même jour)
    """
    if not window.enabled:
        return dt
    start = _parse_hhmm(window.start, dt)
    end = _parse_hhmm(window.end, dt)
    if start is None or end is None or start == end:
        return dt

    if start < end:
        # Fenêtre en un seul morceau dans la journée
        if start <= dt < end:
            return end
        return dt

    # Fenêtre qui passe minuit : [start, 24h) + [00h, end)
    if dt >= start:
        return end + timedelta(days=1)
    if dt < end:
        return end
    return dt


@dataclass
class SchedulePlan:
    """Résultat de la planification d'un site."""
    site: str
    due: bool                 # doit-on voter maintenant ?
    next_vote: datetime       # prochaine tentative planifiée
    cooldown: timedelta       # cooldown du site
    jitter: timedelta         # délai aléatoire appliqué
    night_shifted: bool       # a-t-il été repoussé par la pause nocturne ?


class Scheduler:
    """Planifie chaque site de vote indépendamment, selon son propre cooldown."""

    def __init__(
        self,
        cooldown_hours: dict[str, float],
        night: NightWindow = NightWindow(),
        jitter_minutes: tuple[int, int] = (5, 20),
        rng: Optional[random.Random] = None,
    ) -> None:
        self.cooldown_hours = dict(cooldown_hours)
        self.night = night
        self.jitter_minutes = tuple(jitter_minutes)
        self.rng = rng or random.Random()

    # -- primitives --------------------------------------------------------
    def cooldown_for(self, site: str) -> timedelta:
        hours = self.cooldown_hours.get(site, 24.0)
        return timedelta(hours=hours)

    def _roll_jitter(self) -> timedelta:
        lo, hi = self.jitter_minutes
        lo, hi = sorted((max(0, lo), max(0, hi)))
        minutes = self.rng.randint(lo, hi) if hi > lo else lo
        return timedelta(minutes=minutes)

    # -- logique principale ------------------------------------------------
    def next_vote_time(self, site: str, last_vote: datetime, now: datetime) -> datetime:
        """Prochaine tentative après `last_vote` : + cooldown + jitter, puis pause nocturne."""
        candidate = last_vote + self.cooldown_for(site) + self._roll_jitter()
        return shift_out_of_night_window(candidate, self.night)

    def plan(self, site: str, last_vote: Optional[datetime], now: datetime) -> SchedulePlan:
        cooldown = self.cooldown_for(site)
        jitter = self._roll_jitter()
        if last_vote is None:
            # Premier vote : immédiat ; le jitter ne joue que sur la planification SUIVANTE
            next_vote = now + jitter
            next_vote = shift_out_of_night_window(next_vote, self.night)
            return SchedulePlan(
                site=site,
                due=True,
                next_vote=next_vote,
                cooldown=cooldown,
                jitter=jitter,
                night_shifted=next_vote != now + jitter,
            )
        candidate = last_vote + cooldown + jitter
        original = candidate
        candidate = shift_out_of_night_window(candidate, self.night)
        return SchedulePlan(
            site=site,
            due=candidate <= now,
            next_vote=candidate,
            cooldown=cooldown,
            jitter=jitter,
            night_shifted=candidate != original,
        )
