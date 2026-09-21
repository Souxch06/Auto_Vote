"""Détection automatique des sites de vote depuis la page serveur.

Port en Python du comportement du champ « Lien » de l'extension :
on lit la page du serveur (ex. https://skyofskill.fr/vote) et on retrouve tous
les sites de vote connus vers lesquels elle pointe.

  * phase 1 : les liens vers des domaines du registre (pas d'allers-retours) ;
  * phase 2 : les autres liens plausibles (max `max_probe`) sont sondes — on les
    télécharge et on les retient s'ils portent un CAPTCHA de vote + un champ
    pseudo (signaux forts d'une page de vote).

Aucun site n'est codé en dur : tout part du HTML réel.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.request import Request, urlopen

from .htmlutil import (NOISE_DOMAINS, detect_captcha, domain_without_subdomain,
                       find_nick_input, get_links, get_title)
from .sites import SITE_PROFILES

log = logging.getLogger("autovote.detect")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch_page(url: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Télécharge une page -> (status, url_finale, html). 403/503 tolérés (Cloudflare)."""
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
    with urlopen(req, timeout=timeout) as resp:
        status = resp.status
        final_url = resp.url
        html = resp.read().decode("utf-8", errors="replace")
    return status, final_url, html


@dataclass
class DetectionResult:
    hub_url: str
    title: str = ""
    sites: list[str] = field(default_factory=list)      # URLs absolues, ordre de la page
    probes: int = 0                                      # pages sondées en phase 2
    errors: list[str] = field(default_factory=list)


def detect_voting_sites(
    hub_url: str,
    fetcher: Callable[[str], tuple[int, str, str]] = fetch_page,
    max_probe: int = 8,
    timeout: float = 15.0,
) -> DetectionResult:
    result = DetectionResult(hub_url=hub_url)
    try:
        status, final_url, html = fetcher(hub_url)
    except Exception as e:  # noqa: BLE001 — tout échec réseau est signalé proprement
        result.errors.append(f"page serveur inaccessible: {e}")
        return result
    if status >= 400 and status not in (403, 503):
        result.errors.append(f"page serveur: HTTP {status}")
        return result

    base_url = final_url or hub_url
    result.title = get_title(html)
    hub_domain = domain_without_subdomain(base_url)

    seen: set[str] = set()
    candidates: list[tuple[str, bool]] = []  # (url, domaine connu du registre)
    for link in get_links(html, base_url):
        if not link.lower().startswith(("http://", "https://")):
            continue
        dom = domain_without_subdomain(link)
        if dom == hub_domain or dom in NOISE_DOMAINS:
            continue
        if link in seen:
            continue
        seen.add(link)
        candidates.append((link, dom in SITE_PROFILES))

    # Phase 1 : domaines du registre, sans sonde
    for link, known in candidates:
        if known:
            result.sites.append(link)

    # Phase 2 : sondage des autres liens plausibles (CAPTCHA + champ pseudo)
    probed = 0
    for link, known in candidates:
        if known or probed >= max_probe:
            continue
        probed += 1
        try:
            _status, _final, page_html = fetcher(link)
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"sonde {link} inaccessible: {e}")
            continue
        result.probes += 1
        if detect_captcha(page_html) and find_nick_input(page_html):
            log.info("site de vote détecté par sonde: %s", link)
            result.sites.append(link)

    return result
