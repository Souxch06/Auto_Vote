"""Orchestrateur + interface en ligne de commande du bot.

    python -m autovote_bot run     [--config config.json] [--once]
    python -m autovote_bot detect  [--config config.json] [--save]
    python -m autovote_bot status  [--config config.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config, save_config
from .detect import DetectionResult, detect_voting_sites
from .htmlutil import domain_without_subdomain
from .scheduler import NightWindow, Scheduler
from .sites import SITE_PROFILES
from .state import State
from .solver_client import SolverClient
from .voter import Voter

log = logging.getLogger("autovote")


def setup_logging(log_file: str = "", verbose: bool = False) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def build_scheduler(cfg: Config) -> Scheduler:
    cooldowns = {}
    for s in cfg.sites:
        dom = domain_without_subdomain(s.url)
        profile = SITE_PROFILES.get(dom)
        hours = s.cooldown_hours if s.cooldown_hours is not None else (
            profile.default_cooldown_hours if profile else 24.0
        )
        cooldowns[s.url] = hours
    return Scheduler(
        cooldown_hours=cooldowns,
        night=NightWindow(cfg.timing.night_start, cfg.timing.night_end),
        jitter_minutes=cfg.timing.jitter_minutes,
    )


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def cmd_detect(cfg: Config, save: bool) -> int:
    result: DetectionResult = detect_voting_sites(cfg.server.hub_url)
    print(f"Page serveur : {cfg.server.hub_url}")
    if result.title:
        print(f"Titre        : {result.title}")
    if result.errors:
        print("Erreurs      :")
        for e in result.errors:
            print(f"  ! {e}")
    if result.sites:
        print(f"Sites détectés ({len(result.sites)}) — sondes: {result.probes}:")
        for url in result.sites:
            print(f"  - {url}")
    else:
        print("Aucun site de vote détecté sur la page.")
    if save and result.sites:
        for url in result.sites:
            if not any(s.url == url for s in cfg.sites):
                cfg.sites.append(_site_config_for(url))
        save_config(cfg._path or "config.json", cfg)
        print(f"\nConfig mise à jour: {cfg._path or 'config.json'}")
    return 0 if result.sites else 1


def _site_config_for(url: str):
    from .config import SiteConfig
    from .htmlutil import domain_without_subdomain
    profile = SITE_PROFILES.get(domain_without_subdomain(url))
    return SiteConfig(
        url=url,
        cooldown_hours=profile.default_cooldown_hours if profile else None,
    )


def cmd_status(cfg: Config) -> int:
    state = State(cfg.state_file)
    sched = build_scheduler(cfg)
    now = datetime.now()
    print(f"Serveur : {cfg.server.name or cfg.server.hub_url} — pseudo: {cfg.server.nick}")
    print(f"Panneau nocturne: {cfg.timing.night_start or '—'} → {cfg.timing.night_end or '—'}")
    print()
    for s in cfg.sites:
        p = sched.plan(s.url, state.last_vote(s.url), now)
        last = state.last_vote(s.url)
        print(f"• {s.url}")
        print(f"    cooldown {p.cooldown} | dernier: {last or 'jamais'} | "
              f"prochain: {p.next_vote.strftime('%Y-%m-%d %H:%M:%S')}"
              f"{'  (décalé nuit)' if p.night_shifted else ''}")
        e = state.entry(s.url)
        print(f"    tentatives: {e.get('attempts', 0)} | résolus: {e.get('solved', 0)} | "
              f"dernier résultat: {e.get('last_result') or '—'}")
    return 0


def cmd_run(cfg: Config, once: bool) -> int:
    state = State(cfg.state_file)
    sched = build_scheduler(cfg)
    solver = SolverClient(
        base_url=cfg.solver.base_url,
        auto_start=cfg.solver.auto_start,
        headless=cfg.solver.headless,
        repo_root=cfg.solver.repo_root,
    )
    voter = Voter(cfg, solver)

    stop = {"flag": False}

    def _sig(_signum, _frame):
        log.info("arrêt demandé…")
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    log.info("bot démarré (v%s) — %d site(s) — nuit %s→%s", __version__, len(cfg.sites),
             cfg.timing.night_start or "—", cfg.timing.night_end or "—")
    if not once:
        log.info("solveur: %s (auto_start=%s)", cfg.solver.base_url, cfg.solver.auto_start)
        try:
            solver.ensure_running()
        except Exception as e:  # noqa: BLE001
            log.warning("solveur pas prêt pour l'instant: %s (il sera relancé au 1er vote CAPTCHA)", e)

    try:
        while not stop["flag"]:
            now = datetime.now()
            acted = False
            for s in cfg.sites:
                if stop["flag"]:
                    break
                p = sched.plan(s.url, state.last_vote(s.url), now)
                if not p.due:
                    continue
                log.info("vote dû: %s (prochain à %s)", s.url, p.next_vote.strftime("%H:%M:%S"))
                res = voter.vote(s)
                if res.solved:
                    label = "verified" if res.verified else "solved"
                    state.record_vote(s.url, datetime.now(), label, "")
                    log.info("✔ %s — %s%s", s.url, res.method,
                             "" if res.verified is not False else " (vérification: non)")
                else:
                    state.record_vote(s.url, datetime.now(), "error", res.error)
                    log.error("✘ %s — %s", s.url, res.error)
                # Réajuste l'heure suivante (cooldown + jitter + nuit)
                nxt = sched.next_vote_time(s.url, datetime.now(), datetime.now())
                state.set_next(s.url, nxt)
                state.save()
                acted = True
                time.sleep(cfg.timing.gap_between_sites_seconds)
            if once:
                break
            if not acted:
                time.sleep(max(5, cfg.timing.poll_seconds))
    finally:
        state.save()
        solver.stop()
        log.info("bot arrêté")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autovote_bot", description="Bot de vote autonome (version machine).")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _common(p):
        p.add_argument("--config", "-c", default="config.json", help="fichier de config JSON")
        p.add_argument("--log", default="", help="fichier de log (défaut: stdout)")
        p.add_argument("-v", "--verbose", action="store_true")

    p_run = sub.add_parser("run", help="lance la boucle de vote")
    _common(p_run)
    p_run.add_argument("--once", action="store_true", help="une passe puis arrêt (test)")

    p_det = sub.add_parser("detect", help="détecte les sites de vote sur la page serveur")
    _common(p_det)
    p_det.add_argument("--save", action="store_true", help="ajoute les sites détectés à la config")

    p_st = sub.add_parser("status", help="affiche l'état de planification")
    _common(p_st)

    args = parser.parse_args(argv)
    setup_logging(args.log, args.verbose)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("config: %s", e)
        return 2
    cfg.log_file = args.log or cfg.log_file

    if args.cmd == "detect":
        return cmd_detect(cfg, args.save)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "run":
        try:
            cfg.validate()
        except ConfigError as e:
            log.error("config: %s", e)
            return 2
        return cmd_run(cfg, args.once)
    return 2
