"""Moteur de vote : exécute le vote sur UN site, puis vérifie le résultat.

Flux pour un site (miroir du cycle hub-and-spoke de l'extension) :
  1. lecture de la page de vote -> type de CAPTCHA, sitekey, champ pseudo
     (les profils connus priment, sinon auto-détection) ;
  2. vote :
       * si CAPTCHA -> le sidecar solveur navigue lui-même sur la page
         (mode `real_page`), remplit le pseudo, résout le CAPTCHA et le vote
         est soumis depuis la même session/IP (token cohérent) ;
       * sans CAPTCHA -> le bot vote directement dans son navigateur ;
  3. vérification : marqueur sur la page serveur (voteVerify) ou marqueur
     « déjà voté » sur le site — best effort, le résultat est signalé.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from .config import Config, SiteConfig
from .detect import fetch_page
from .htmlutil import (detect_captcha, domain_without_subdomain,
                       extract_sitekey, find_nick_input, find_submit_input,
                       match_marker, marker_found, parse_vote_form)
from .sites import SITE_PROFILES, SiteProfile, generic_profile
from .solver_client import SolverClient, SolverError

log = logging.getLogger("autovote.voter")


@dataclass
class VoteResult:
    site: str
    solved: bool = False          # le vote a été soumis (CAPTCHA résolu)
    verified: Optional[bool] = None  # vérification hub/site (None = impossible)
    method: str = ""
    error: str = ""
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.solved


class Voter:
    def __init__(self, cfg: Config, solver: SolverClient) -> None:
        self.cfg = cfg
        self.solver = solver

    # -- résolution du profil effectif --------------------------------------
    def _effective_profile(self, site: SiteConfig, html: str) -> tuple[SiteProfile, str]:
        """(profil, type de CAPTCHA effectif). Les forcages config priment."""
        dom = domain_without_subdomain(site.url)
        profile = SITE_PROFILES.get(dom)
        if profile is None:
            profile = generic_profile()
        if site.captcha:
            profile = SiteProfile(**{**profile.__dict__, "captcha": site.captcha})
        if site.sitekey:
            profile = SiteProfile(**{**profile.__dict__, "sitekey": site.sitekey})

        captcha = detect_captcha(html) if html else ""
        captcha = profile.captcha or captcha
        return profile, captcha

    # -- vote -----------------------------------------------------------------
    def vote(self, site: SiteConfig) -> VoteResult:
        result = VoteResult(site=site.url)
        html = ""
        try:
            _status, _final, html = fetch_page(site.url)
        except Exception as e:  # noqa: BLE001 — on tente quand même avec le profil
            log.warning("pré-lecture %s impossible: %s", site.url, e)

        profile, captcha = self._effective_profile(site, html)
        nick_selector = profile.nick_selector or (find_nick_input(html) if html else "")
        submit_selector = profile.submit_selector or (find_submit_input(html) if html else "")
        sitekey = profile.sitekey or (extract_sitekey(html, captcha) if (html and captcha) else "")
        result.details = {"captcha": captcha, "sitekey": sitekey[:8] + "…" if sitekey else "",
                          "nick_selector": nick_selector, "submit_selector": submit_selector}

        if not nick_selector:
            result.error = "champ pseudo introuvable sur la page"
            return result

        if captcha:
            self._vote_via_solver(site, profile, captcha, sitekey, nick_selector,
                                  submit_selector, html, result)
        else:
            self._vote_direct(site, profile, nick_selector, submit_selector, result)

        if result.solved and result.verified is None:
            # Non encore vérifié dans la session du vote -> vérification classique
            # (page serveur / page du site)
            result.verified = self._verify(site, profile)
        return result

    def _vote_via_solver(self, site, profile, captcha, sitekey, nick_selector,
                         submit_selector, html, result) -> None:
        if not sitekey:
            result.error = f"sitekey {captcha} introuvable sur la page (précise-la dans la config)"
            return

        # Si on connaît le formulaire de vote, le CAPTCHA est résolu par le solveur
        # puis le formulaire est RE-SOUMIS avec le token depuis la même session
        # (le clic « avant » le CAPTCHA du real_page ne suffit pas quand un défi
        # s'affiche : c'est exactement ce que l'extension faisait en re-cliquant
        # après captchaPassed).
        form = parse_vote_form(html, site.url) if html else None
        use_form = bool(form and form.get("captcha_field"))

        if use_form:
            pre_actions = [dict(a) for a in profile.pre_actions]
            if nick_selector:
                pre_actions.append({"type": "fill", "selector": nick_selector,
                                    "value": "__NICK__", "timeout": 15000})
            pre_actions.append({"type": "wait", "value": 2})
            body = dict(form.get("fields") or {})
            if form.get("nick_field"):
                body[form["nick_field"]] = "__NICK__"
            body[form["captcha_field"]] = "__TOKEN__"
            post_fetch = [{"url": form["action"], "method": form.get("method") or "POST",
                           "body": body, "contentType": "form"}]
            result.details["form_submit"] = form["action"]
        else:
            pre_actions = profile.vote_actions(nick_selector, submit_selector)
            # Vérification par re-lecture de la page depuis la session du vote
            post_fetch = [{"url": site.url, "method": "GET"}]

        payload = {
            "type": captcha,
            "url": site.url,
            "sitekey": sitekey,
            "real_page": True,
            "pre_actions": pre_actions,
            "post_fetch": post_fetch,
            "timeout_s": self.cfg.solver.timeout_seconds,
        }
        for a in payload["pre_actions"]:
            if a.get("value") == "__NICK__":
                a["value"] = self.cfg.server.nick
        for k, v in list(payload["post_fetch"][0].get("body") or {}).items():
            if isinstance(v, str) and "__NICK__" in v:
                payload["post_fetch"][0]["body"][k] = v.replace("__NICK__", self.cfg.server.nick)
        if captcha == "recaptcha":
            payload["version"] = profile.recaptcha_version or "v2"
            payload["classifier"] = "hybrid"
            if profile.recaptcha_enterprise:
                payload["enterprise"] = True
        if self.cfg.solver.proxy:
            payload["proxy"] = self.cfg.solver.proxy
        try:
            data = self.solver.solve(payload)
            result.solved = True
            result.method = f"solveur/{data.get('method') or captcha} ({data.get('elapsed') or '?'}s)"
            log.info("vote envoyé: %s (%s)", site.url, result.method)
            # La réponse du re-submit (ou la re-lecture de la page), lue DEPUIS la
            # session même du vote (cookies inclus) = vérification sans aller-retour
            post = data.get("post_fetch") or []
            if post and post[0].get("body"):
                body = post[0]["body"]
                result.details["verify_body_bytes"] = len(body)
                if marker_found(body, profile.success_markers) or marker_found(body, profile.already_markers):
                    result.verified = True
                    log.info("vote vérifié dans la session du vote: %s", site.url)
        except SolverError as e:
            result.error = str(e)
            log.error("échec vote %s: %s", site.url, e)

    def _vote_direct(self, site, profile, nick_selector, submit_selector, result) -> None:
        """Site sans CAPTCHA : vote dans un navigateur CloakBrowser local."""
        try:
            import cloakbrowser  # noqa: F401
        except ImportError:
            result.error = "cloakbrowser non installé (venv du solveur requis) — site sans CAPTCHA"
            return
        try:
            result.solved = asyncio.run(self._direct_async(site, profile, nick_selector, submit_selector))
            if result.solved:
                result.method = "navigateur direct (pas de captcha)"
        except Exception as e:  # noqa: BLE001
            result.error = f"navigateur direct: {e}"
            log.error("échec vote direct %s: %s", site.url, e)

    async def _direct_async(self, site, profile, nick_selector, submit_selector) -> bool:
        import cloakbrowser
        kwargs = {"humanize": True, "headless": self.cfg.solver.headless}
        if self.cfg.solver.proxy:
            kwargs["proxy"] = self.cfg.solver.proxy
        async with await cloakbrowser.launch_async(**kwargs) as browser:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(site.url, wait_until="domcontentloaded", timeout=60000)
            for a in profile.vote_actions(nick_selector, submit_selector):
                at, sel, val = a.get("type"), a.get("selector", ""), a.get("value", "")
                to = a.get("timeout", 15000)
                if at == "fill" and val:
                    await page.locator(sel).fill(self.cfg.server.nick, timeout=to)
                elif at == "click":
                    await page.locator(sel).click(timeout=to)
                elif at == "wait":
                    await asyncio.sleep(float(val or 1))
                await asyncio.sleep(0.5)
            for marker in profile.success_markers:
                try:
                    if marker.startswith(("#", ".")):
                        await page.wait_for_selector(marker, timeout=25000)
                        return True
                    elif marker in await page.inner_text("body"):
                        return True
                except Exception:  # noqa: BLE001
                    continue
            body = ""
            try:
                body = await page.content()
            except Exception:  # noqa: BLE001
                pass
            return any(m in body for m in profile.already_markers) or any(
                m in body for m in profile.success_markers if not m.startswith(("#", "."))
            )

    # -- vérification -----------------------------------------------------------
    def _verify(self, site: SiteConfig, profile: SiteProfile) -> Optional[bool]:
        spec = site.verify or self.cfg.server.vote_verify
        try:
            if spec:
                url = self.cfg.server.hub_url if not site.verify else site.url
                _status, _final, html = fetch_page(url)
                if match_marker(html, spec):
                    return True
            _status, _final, html = fetch_page(site.url)
            if any(m in html for m in profile.already_markers if not m.startswith(("#", "."))):
                return True
            if any(match_marker(html, m) for m in profile.already_markers if m.startswith(("#", "."))):
                return True
            return False
        except Exception:  # noqa: BLE001
            log.info("vérification impossible pour %s (page injoignable)", site.url)
            return None
