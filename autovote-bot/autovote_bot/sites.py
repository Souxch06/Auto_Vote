"""Registre des sites de vote : profil par domaine (CAPTCHA, champs, cooldown).

Porté des scripts de l'extension (`Auto-Vote-Rating-dev/scripts/*.js`).
Un site inconnu reçoit un profil GÉNÉRIQUE construit à la volée à partir de la
page réelle (type de CAPTCHA détecté, champ pseudo détecté) — rien n'est codé
pour un serveur précis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .htmlutil import domain_without_subdomain


@dataclass
class SiteProfile:
    """Tout ce que le bot sait faire sur un site de vote."""
    captcha: str = ""                     # hcaptcha | recaptcha | turnstile | "" (aucun)
    recaptcha_version: str = "v2"
    recaptcha_enterprise: bool = False
    sitekey: str = ""                     # sitekey fixe ('' = auto-détection sur la page)
    nick_selector: str = ""               # champ pseudo ('' = auto-détection)
    submit_selector: str = ""             # bouton de validation ('' = auto-détection)
    pre_actions: list = field(default_factory=list)  # étapes avant le CAPTCHA (ouverture de modale…)
    success_markers: list = field(default_factory=list)  # texte/éléments de succès
    already_markers: list = field(default_factory=list)  # texte « déjà voté » / cooldown
    default_cooldown_hours: float = 24.0

    def vote_actions(self, nick_selector: str, submit_selector: str) -> list:
        """Séquence click/fill à envoyer au solveur (placeholder __NICK__)."""
        actions = [dict(a) for a in self.pre_actions]
        if nick_selector:
            actions.append({"type": "fill", "selector": nick_selector, "value": "__NICK__", "timeout": 15000})
        actions.append({"type": "wait", "value": 3})
        if submit_selector:
            actions.append({"type": "click", "selector": submit_selector, "timeout": 15000})
        return actions


# Sites connus (portés des scripts de l'extension, vérifiés sur les pages réelles)
SITE_PROFILES: dict[str, SiteProfile] = {
    "serveur-prive.net": SiteProfile(
        captcha="hcaptcha",
        nick_selector="#username",
        submit_selector="#voteBtn",
        success_markers=[".alert.alert-success"],
        already_markers=["Vous avez déjà voté pour ce serveur"],
        default_cooldown_hours=1.5,
    ),
    "serveur-minecraft.com": SiteProfile(
        captcha="recaptcha",
        recaptcha_version="v2",
        recaptcha_enterprise=True,
        nick_selector="#form_username",
        submit_selector='form button[type="submit"]',
        success_markers=["div.alert-success"],
        already_markers=["Vous avez déjà voté pour ce serveur"],
        default_cooldown_hours=3.0,
    ),
    "serveursminecraft.org": SiteProfile(
        captcha="recaptcha",
        recaptcha_version="v2",
        nick_selector="#pseudo",
        submit_selector="#vote form input[type=submit]",
        pre_actions=[
            # La page vote dans une fenêtre modale Bootstrap — on l'ouvre d'abord
            {"type": "click", "selector": '[data-target="#vote"]', "timeout": 15000},
            {"type": "wait", "value": 2},
        ],
        success_markers=[".alert.alert-success"],
        already_markers=["Vous devez attendre"],
        default_cooldown_hours=24.0,
    ),
}


def profile_for(url: str) -> Optional[SiteProfile]:
    return SITE_PROFILES.get(domain_without_subdomain(url))


def generic_profile(captcha: str = "", nick_selector: str = "", submit_selector: str = "") -> SiteProfile:
    """Profil générique pour un site inconnu (auto-détection sur la page)."""
    return SiteProfile(
        captcha=captcha,
        nick_selector=nick_selector,
        submit_selector=submit_selector or 'form button[type="submit"], form input[type="submit"], button[type="submit"]',
        success_markers=[".alert-success", ".alert.alert-success", "merci", "vote enregistré"],
        already_markers=["déjà voté", "already voted", "Vous devez attendre"],
        default_cooldown_hours=24.0,
    )
