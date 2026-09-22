"""Petits utilitaires HTML en stdlib pur (aucune dépendance externe).

Le bot n'a besoin que de peu de chose côté HTML :
  * extraire les liens <a href> (détection des sites de vote depuis la page serveur)
  * détecter le type de CAPTCHA présent (hCaptcha / reCAPTCHA / Turnstile)
  * extraire la sitekey du CAPTCHA
  * trouver le champ « pseudo » le plus probable
  * chercher un marqueur texte/élément (vérification sur la page serveur)
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

LINK_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"']", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Marqueurs caractéristiques de chaque fournisseur de CAPTCHA dans le HTML
CAPTCHA_DETECTORS = (
    ("hcaptcha", re.compile(r"hcaptcha\.com|h-captcha", re.I)),
    ("recaptcha", re.compile(r"recaptcha|grecaptcha", re.I)),
    ("turnstile", re.compile(r"turnstile", re.I)),
)

SITEKEY_RES = {
    "hcaptcha": re.compile(
        r"(?:data-sitekey|sitekey)\s*=\s*[\"']?"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    ),
    "recaptcha": re.compile(r"(?:data-sitekey\s*=\s*[\"'])?((?:6L|6B)[0-9a-zA-Z_-]{10,})"),
    "turnstile": re.compile(r"data-sitekey\s*=\s*[\"']?(0x[0-9a-fA-F]{8,})"),
}

# Mots-clés des champs pseudo, par ordre de probabilité
NICK_HINTS = ("nick", "pseudo", "username", "user_name", "inname", "ingame", "player", "username_minecraft")

# Domaines qui ne sont JAMAIS des sites de vote (réseaux sociaux, vidéo, support…)
NOISE_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "twitch.tv", "tiktok.com", "discord.com", "discord.gg", "telegram.org", "t.me",
    "github.com", "githubusercontent.com", "reddit.com", "linkedin.com", "pinterest.com",
    "snapchat.com", "whatsapp.com", "spotify.com", "soundcloud.com", "steamcommunity.com",
    "wikipedia.org", "wikimedia.org", "google.com", "googleapis.com", "gstatic.com",
}


def domain_without_subdomain(url: str) -> str:
    """Domaine enregistré simplifié : les 2 labels de droite (assez pour nos sites)."""
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def get_title(html: str) -> str:
    m = TITLE_RE.search(html or "")
    return m.group(1).strip() if m else ""


def get_links(html: str, base_url: str) -> list[str]:
    """Toutes les URLs absolues des <a href>, dans l'ordre de la page."""
    out = []
    for href in LINK_RE.findall(html or ""):
        try:
            out.append(urljoin(base_url, href))
        except ValueError:
            continue
    return out


def detect_captcha(html: str) -> str:
    """Type de CAPTCHA présent dans la page, ou ''."""
    for ctype, rx in CAPTCHA_DETECTORS:
        if rx.search(html or ""):
            return ctype
    return ""


def extract_sitekey(html: str, ctype: str) -> str:
    rx = SITEKEY_RES.get(ctype)
    if not rx:
        return ""
    m = rx.search(html or "")
    return m.group(1) if m else ""


class _Inputs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: list[tuple[str, str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("input", "select", "textarea"):
            d = dict(attrs)
            self.inputs.append((tag, (d.get("name") or "").lower(), d.get("id") or "", d.get("type") or ""))


def find_nick_input(html: str) -> str:
    """Sélecteur CSS du champ pseudo le plus probable, ou ''."""
    parser = _Inputs()
    try:
        parser.feed(html or "")
    except Exception:
        return ""
    for tag, name, ident, itype in parser.inputs:
        if itype in ("password", "hidden", "submit", "button", "checkbox", "radio", "file"):
            continue
        for hint in NICK_HINTS:
            if hint in name or hint in ident.lower():
                if ident:
                    return f"#{ident}"
                return f"{tag}[name='{name}']"
    return ""


def find_submit_input(html: str) -> str:
    """Sélecteur CSS du bouton de validation le plus probable, ou ''."""
    parser = _Inputs()
    try:
        parser.feed(html or "")
    except Exception:
        return ""
    for tag, _name, ident, itype in parser.inputs:
        if itype == "submit":
            if ident:
                return f"#{ident}"
            return f"{tag}[type=submit]"
    return "form button[type=submit]" if re.search(r"<form\b", html or "", re.I) else "button[type=submit]"


def match_marker(html: str, spec: str) -> bool:
    """Vérifie un marqueur sur une page.

    Formats de `spec` :
      * commence par '#'  -> un élément id="…" existe
      * commence par '.'  -> un élément porte la classe
      * sinon             -> le texte est présent (insensible à la casse)
    """
    if not spec or not html:
        return False
    if spec.startswith("#"):
        return re.search(rf'id=["\']{re.escape(spec[1:])}["\']', html, re.I) is not None
    if spec.startswith("."):
        cls = re.escape(spec[1:])
        return re.search(rf'class=["\'][^"\']*\b{cls}\b', html, re.I) is not None
    return spec.lower() in html.lower()


def marker_found(html: str, markers: list[str]) -> bool:
    """True si au moins un marqueur (texte, #id ou .classe) est présent."""
    return any(match_marker(html, m) for m in markers if m)


# Champ qui reçoit le token résolu (hCaptcha / reCAPTCHA / Turnstile)
_CAPTCHA_FIELD_RX = re.compile(
    r"h-captcha-response|g-recaptcha-response|cf-turnstile-response|captcha-response|captcha", re.I)
_WIDGET_CLASSES = ("h-captcha", "hcaptcha", "g-recaptcha", "recaptcha", "cf-turnstile", "turnstile")


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "form":
            self.forms.append({
                "action": d.get("action") or "",
                "method": (d.get("method") or "POST").upper(),
                "fields": {}, "nick_field": "", "captcha_field": "", "has_widget": False,
            })
            self._stack.append(self.forms[-1])
            return
        if not self._stack:
            return
        f = self._stack[-1]
        if tag == "div":
            cls = (d.get("class") or "").lower()
            if any(w in cls for w in _WIDGET_CLASSES):
                f["has_widget"] = True
        elif tag in ("input", "textarea", "select"):
            itype = (d.get("type") or "text").lower()
            if itype in ("submit", "button", "image", "file", "checkbox", "radio"):
                return
            name = d.get("name") or ""
            if not name:
                return
            f["fields"][name] = d.get("value") or ""
            low = name.lower()
            if not f["captcha_field"] and _CAPTCHA_FIELD_RX.search(low):
                f["captcha_field"] = name
            elif not f["nick_field"] and any(h in low for h in NICK_HINTS):
                f["nick_field"] = name

    def handle_endtag(self, tag):
        if tag == "form" and self._stack:
            self._stack.pop()


def parse_vote_form(html: str, base_url: str) -> dict | None:
    """Le formulaire de vote de la page (celui qui porte le widget CAPTCHA ou
    le champ pseudo). Retourne {action, method, fields, nick_field,
    captcha_field} ou None.
    """
    p = _FormParser()
    try:
        p.feed(html or "")
    except Exception:
        return None
    if not p.forms:
        return None
    chosen = None
    for f in p.forms:  # priorité : widget CAPTCHA ou champ pseudo
        if f["has_widget"] or f["nick_field"]:
            chosen = f
            break
    if chosen is None:
        for f in p.forms:
            if f["captcha_field"]:
                chosen = f
                break
    if chosen is None:
        return None
    chosen["action"] = urljoin(base_url, chosen["action"]) if chosen["action"] else base_url
    return chosen
