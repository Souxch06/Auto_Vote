#!/usr/bin/env python3
"""Tests du noyau du bot (sans navigateur, sans réseau).

Lance :  python3 autovote-bot/test_autovote_bot.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Rendre le package importable depuis le repo (autovote-bot/autovote_bot)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from autovote_bot.scheduler import NightWindow, Scheduler, shift_out_of_night_window  # noqa: E402
from autovote_bot import htmlutil as H  # noqa: E402
from autovote_bot.sites import SITE_PROFILES, generic_profile, profile_for  # noqa: E402
from autovote_bot.state import State  # noqa: E402
from autovote_bot.config import Config, ConfigError, SiteConfig  # noqa: E402
from autovote_bot.detect import detect_voting_sites  # noqa: E402
from autovote_bot.solver_client import SolverClient, SolverError  # noqa: E402


SKY_HUB_HTML = """
<html><head><title>Vote | SkyOfSkill</title></head><body>
<a href="https://serveur-prive.net/minecraft/skyofskill-serveur-minecraft-prison/vote">Voter</a>
<a href="https://serveur-minecraft.com/5834">Voter</a>
<a href="https://www.serveursminecraft.org/serveur/3136/">Voter</a>
<a href="https://www.youtube.com/watch?v=xyz">Tutoriel vidéo</a>
<a href="/jouer">Guide</a>
<a href="https://serveur-minecraft.com/5834">Dupliqué</a>
</body></html>
"""

PRIVE_PAGE = """
<html><body><form><input id="username" type="text">
<div class="h-captcha" data-sitekey="10000000-ffff-ffff-ffff-000000000001"></div>
<button id="voteBtn">Je vote maintenant</button></form></body></html>
"""

MYSTERY_PAGE = """
<html><body><form><input name="pseudo" type="text">
<div class="g-recaptcha" data-sitekey="6LxAbC1234567890abc"></div>
<button type="submit">Voter</button></form></body></html>
"""


class TestShiftOutOfNight(unittest.TestCase):
    def test_pas_de_fenetre(self):
        dt = datetime(2026, 9, 21, 3, 0)
        self.assertEqual(shift_out_of_night_window(dt, NightWindow("", "")), dt)

    def test_fenetre_journee_dans(self):
        dt = datetime(2026, 9, 21, 3, 30)
        out = shift_out_of_night_window(dt, NightWindow("02:00", "08:00"))
        self.assertEqual(out, datetime(2026, 9, 21, 8, 0))

    def test_fenetre_journee_hors(self):
        dt = datetime(2026, 9, 21, 9, 0)
        self.assertEqual(shift_out_of_night_window(dt, NightWindow("02:00", "08:00")), dt)

    def test_fenetre_minuit_cote_matin(self):
        # 23:00-01:00 ; dt=00:30 (dans la 2e moitié) -> 01:00 même jour
        dt = datetime(2026, 9, 21, 0, 30)
        out = shift_out_of_night_window(dt, NightWindow("23:00", "01:00"))
        self.assertEqual(out, datetime(2026, 9, 21, 1, 0))

    def test_fenetre_minuit_cote_soir(self):
        # 23:00-01:00 ; dt=23:30 (1re moitié) -> 01:00 +1 jour
        dt = datetime(2026, 9, 21, 23, 30)
        out = shift_out_of_night_window(dt, NightWindow("23:00", "01:00"))
        self.assertEqual(out, datetime(2026, 9, 22, 1, 0))

    def test_fenetre_minuit_hors(self):
        dt = datetime(2026, 9, 21, 12, 0)
        self.assertEqual(shift_out_of_night_window(dt, NightWindow("23:00", "01:00")), dt)


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, 12, 0, 0)

    def test_dernier_vote_recent_pas_due(self):
        s = Scheduler({"a": 24}, jitter_minutes=(0, 0))
        last = self.now - timedelta(hours=1)
        p = s.plan("a", last, self.now)
        self.assertFalse(p.due)

    def test_dernier_vote_cote_due(self):
        s = Scheduler({"a": 24}, jitter_minutes=(0, 0))
        last = self.now - timedelta(hours=25)
        p = s.plan("a", last, self.now)
        self.assertTrue(p.due)

    def test_premier_vote_due(self):
        s = Scheduler({"a": 24}, jitter_minutes=(0, 0))
        p = s.plan("a", None, self.now)
        self.assertTrue(p.due)

    def test_cooldown_par_site(self):
        s = Scheduler({"prive": 1.5, "mc": 3, "org": 24}, jitter_minutes=(0, 0))
        self.assertEqual(s.cooldown_for("prive"), timedelta(hours=1.5))
        self.assertEqual(s.cooldown_for("mc"), timedelta(hours=3))
        self.assertEqual(s.cooldown_for("org"), timedelta(hours=24))
        # site inconnu -> défaut 24h
        self.assertEqual(s.cooldown_for("autre"), timedelta(hours=24))

    def test_jitter_borne(self):
        s = Scheduler({"a": 1}, jitter_minutes=(10, 20), rng=random.Random(1))
        for _ in range(50):
            p = s.plan("a", None, self.now)
            minutes = p.jitter.total_seconds() / 60
            self.assertGreaterEqual(minutes, 10)
            self.assertLessEqual(minutes, 20)

    def test_nuit_repousse(self):
        # 12:00 + cooldown 14h = 02:00 LE LENDEMAIN (dans la nuit 02-08) -> repoussé à 08:00
        s = Scheduler({"a": 14}, night=NightWindow("02:00", "08:00"), jitter_minutes=(0, 0))
        p = s.plan("a", self.now, self.now)
        self.assertTrue(p.night_shifted)
        self.assertEqual(p.next_vote, self.now.replace(hour=8, minute=0) + timedelta(days=1))


class TestSites(unittest.TestCase):
    def test_profiles_connus(self):
        self.assertEqual(profile_for("https://serveur-prive.net/x/vote").captcha, "hcaptcha")
        self.assertTrue(profile_for("https://serveur-minecraft.com/5834").recaptcha_enterprise)
        self.assertEqual(profile_for("https://www.serveursminecraft.org/serveur/3136/").default_cooldown_hours, 24.0)

    def test_domaine_inconnu(self):
        self.assertIsNone(profile_for("https://inconnu.fr/vote"))

    def test_generic_vote_actions(self):
        p = generic_profile(captcha="recaptcha")
        actions = p.vote_actions("#nick", "button[type=submit]")
        kinds = [a["type"] for a in actions]
        self.assertIn("fill", kinds)
        self.assertIn("wait", kinds)
        self.assertIn("click", kinds)
        self.assertTrue(any(a.get("value") == "__NICK__" for a in actions))

    def test_priseurs_modale(self):
        # serveursminecraft.org ouvre d'abord une modale
        p = profile_for("https://www.serveursminecraft.org/serveur/3136/")
        self.assertTrue(p.pre_actions)
        self.assertEqual(p.pre_actions[0]["type"], "click")


class TestHtmlUtil(unittest.TestCase):
    def test_domain(self):
        self.assertEqual(H.domain_without_subdomain("https://www.serveur-minecraft.com/5834"), "serveur-minecraft.com")
        self.assertEqual(H.domain_without_subdomain("https://a.b.serveursminecraft.org/x"), "serveursminecraft.org")

    def test_detect_captcha(self):
        self.assertEqual(H.detect_captcha('<div class="h-captcha" data-sitekey="10000000-ffff-ffff-ffff-000000000001"></div>'), "hcaptcha")
        self.assertEqual(H.detect_captcha('<div class="g-recaptcha" data-sitekey="6Le"></div>'), "recaptcha")
        self.assertEqual(H.detect_captcha('<div class="cf-turnstile" data-sitekey="0xabc123"></div>'), "turnstile")
        self.assertEqual(H.detect_captcha("<p>aucun captcha</p>"), "")

    def test_extract_sitekey(self):
        html = '<div data-sitekey="10000000-ffff-ffff-ffff-000000000001"></div>'
        self.assertEqual(H.extract_sitekey(html, "hcaptcha"), "10000000-ffff-ffff-ffff-000000000001")
        self.assertEqual(H.extract_sitekey('<div data-sitekey="6LxAbC1234567890abc"></div>', "recaptcha"), "6LxAbC1234567890abc")

    def test_find_nick_input(self):
        html = '<input name="username" id="username" type="text"><input name="email" type="email">'
        self.assertEqual(H.find_nick_input(html), "#username")
        html2 = '<input name="pseudo" type="text">'
        self.assertEqual(H.find_nick_input(html2), "input[name='pseudo']")

    def test_match_marker(self):
        html = '<div id="ok">Merci de votre vote</div>'
        self.assertTrue(H.match_marker(html, "#ok"))
        self.assertTrue(H.match_marker(html, "merci de votre vote"))
        self.assertFalse(H.match_marker(html, "#missing"))


class TestState(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            st = State(p)
            now = datetime(2026, 9, 21, 10, 0, 0)
            st.record_vote("a", now, "solved")
            st.set_next("a", now + timedelta(hours=24))
            st.save()
            st2 = State(p)
            self.assertEqual(st2.last_vote("a"), now)
            self.assertEqual(st2.entry("a")["solved"], 1)
            self.assertIsNotNone(st2.entry("a")["next_vote"])


class TestConfig(unittest.TestCase):
    def test_validate_ok(self):
        cfg = Config()
        cfg.server.hub_url = "https://skyofskill.fr/vote"
        cfg.server.nick = "Pseudo"
        cfg.sites = [SiteConfig(url="https://serveur-minecraft.com/5834")]
        cfg.validate()  # ne lève pas

    def test_validate_nickname(self):
        cfg = Config()
        cfg.server.hub_url = "https://skyofskill.fr/vote"
        cfg.sites = [SiteConfig(url="https://serveur-minecraft.com/5834")]
        with self.assertRaises(ConfigError):
            cfg.validate()

    def test_roundtrip_json(self):
        cfg = Config()
        cfg.server.hub_url = "https://skyofskill.fr/vote"
        cfg.server.nick = "Pseudo"
        cfg.sites = [SiteConfig(url="https://serveur-minecraft.com/5834", cooldown_hours=3.0)]
        d = cfg.to_dict()
        s = json.dumps(d)
        self.assertIn("serveur-minecraft.com/5834", s)
        cfg2 = Config().from_dict(json.loads(s))
        self.assertEqual(cfg2.sites[0].cooldown_hours, 3.0)


class TestDetect(unittest.TestCase):
    def _fetcher(self, pages: dict):
        calls = []

        def fetcher(url):
            calls.append(url)
            if url in pages:
                return 200, url, pages[url]
            raise ConnectionError(f"inconnu: {url}")
        return fetcher, calls

    def test_page_skyofskill(self):
        fetcher, calls = self._fetcher({
            "https://skyofskill.fr/vote": SKY_HUB_HTML,
        })
        r = detect_voting_sites("https://skyofskill.fr/vote", fetcher=fetcher)
        self.assertEqual(r.title, "Vote | SkyOfSkill")
        self.assertEqual(r.sites, [
            "https://serveur-prive.net/minecraft/skyofskill-serveur-minecraft-prison/vote",
            "https://serveur-minecraft.com/5834",
            "https://www.serveursminecraft.org/serveur/3136/",
        ])
        # Les 3 sont du registre -> aucune sonde nécessaire
        self.assertEqual(r.probes, 0)
        # Pas de sonde sur youtube (noise), ni sur le lien relatif
        self.assertTrue(all("youtube" not in c for c in calls))

    def test_sonde_site_inconnu(self):
        fetcher, calls = self._fetcher({
            "https://monserveur.fr/vote": (
                "<html><head><title>Mon serveur</title></head><body>"
                '<a href="https://votelist.example.com/server/99">Voter</a></body></html>'
            ),
            "https://votelist.example.com/server/99": MYSTERY_PAGE,
        })
        r = detect_voting_sites("https://monserveur.fr/vote", fetcher=fetcher)
        # Détecté en phase 2 (CAPTCHA + champ pseudo)
        self.assertEqual(r.sites, ["https://votelist.example.com/server/99"])
        self.assertEqual(r.probes, 1)

    def test_rien_de_connu(self):
        fetcher, _ = self._fetcher({
            "https://vide.fr/vote": "<html><head><title>Vide</title></head><body><a href='https://a.fr/b'>x</a></body></html>",
            "https://a.fr/b": "<html><body>aucun captcha</body></html>",
        })
        r = detect_voting_sites("https://vide.fr/vote", fetcher=fetcher)
        self.assertEqual(r.sites, [])

    def test_page_injoignable(self):
        fetcher, _ = self._fetcher({})
        r = detect_voting_sites("https://mort.fr/vote", fetcher=fetcher)
        self.assertEqual(r.sites, [])
        self.assertTrue(r.errors)


class TestSolverClient(unittest.TestCase):
    def _client(self):
        import urllib.request
        class _Resp:
            def __init__(self, payload): self._p = payload
            def read(self): return json.dumps(self._p).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        client = SolverClient(base_url="http://127.0.0.1:1", auto_start=False)
        return client, _Resp

    def test_solved_ok(self):
        client, _Resp = self._client()
        import urllib.request
        client.health = lambda: {"ok": True}
        client.ensure_running = lambda: None
        urllib.request.urlopen = lambda req, timeout=None: _Resp(
            {"solved": True, "token": "abc", "method": "route", "elapsed": 12.3})
        data = client.solve({"type": "hcaptcha", "url": "https://x", "sitekey": "k"}, timeout_s=60)
        self.assertTrue(data["solved"])
        self.assertEqual(data["token"], "abc")

    def test_solved_fail(self):
        client, _Resp = self._client()
        import urllib.request
        client.health = lambda: {"ok": True}
        client.ensure_running = lambda: None
        urllib.request.urlopen = lambda req, timeout=None: _Resp(
            {"solved": False, "error": "No token obtained"})
        with self.assertRaises(SolverError):
            client.solve({"type": "hcaptcha", "url": "https://x", "sitekey": "k"}, timeout_s=60)

    def test_http_error(self):
        client, _Resp = self._client()
        import urllib.error
        import urllib.request
        client.health = lambda: {"ok": True}
        client.ensure_running = lambda: None
        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                         __import__("io").BytesIO(b'{"detail": "missing sitekey"}'))
        urllib.request.urlopen = boom
        with self.assertRaises(SolverError) as cm:
            client.solve({"type": "hcaptcha", "url": "https://x"}, timeout_s=60)
        self.assertIn("missing sitekey", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
