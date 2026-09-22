# Auto Vote Bot — la version « machine » d'Auto-Vote-Rating

Le bot de vote autonome, **sans navigateur à ouvrir, sans extension** : un
programme Python qui tourne sur une machine (PC laissé allumé, VPS, Raspberry
Pi avec Xvfb…) et vote pour ton serveur Minecraft 24 h/24, comme le service
Voxa — mais **chez toi**, avec **ton** solveur de CAPTCHAs.

Il reprend tout ce que l'extension savait faire, et y ajoute le comportement
observé chez Voxa :

- **Cooldown par site** : chaque site de vote est revisité dès que SON délai
  est écoulé (le compte à rebours de l'un ne bloque pas l'autre) ;
- **Délai aléatoire** (jitter) ajouté à chaque planification ;
- **Pause nocturne** : rien n'est planifié dans une fenêtre horaire (ex.
  02:00–08:00), la fenêtre peut passer minuit (ex. 23:00–01:00) ;
- **Détection automatique** : colle l'URL de ta page de vote (ex.
  `https://skyofskill.fr/vote`) et le bot retrouve tout seul les sites de
  vote qu'elle pointe ;
- **CAPTCHA résolus par le sidecar** `captcha-solver` déjà présent dans ce
  repo (hCaptcha, reCAPTCHA v2/v3/Enterprise, Turnstile, …) dans un vrai
  navigateur anti-détection.

## Les 3 sites de SkyOfSkill

| Site | CAPTCHA | Cooldown |
|---|---|---|
| `serveur-prive.net/…/skyofskill…/vote` | hCaptcha | 1 h 30 |
| `serveur-minecraft.com/5834` | reCAPTCHA v2 Enterprise | 3 h |
| `serveursminecraft.org/serveur/3136/` | reCAPTCHA v2 | 24 h |

→ jusqu'à **8 + 16 + 1 = 25 votes/jour**, sans CAPTCHA manuel.

## Prérequis

- Python **3.10+**
- Le sidecar `captcha-solver` (déjà dans la racine du repo : `server.py`)
  avec son venv — `pip install -r requirements.txt` (racine du repo).
- Un navigateur CloakBrowser (installé automatiquement par la 1re utilisation
  du solveur). En serveur sans écran : `BROWSER_HEADLESS=1` ou
  Xvfb (`xvfb-run python server.py`).

## Mise en route

```bash
# 1. venv commun (racine du repo) — une seule fois
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # sidecar solveur
.venv/bin/pip install -r autovote-bot/requirements.txt

# 2. config
cd autovote-bot
cp config.example.json config.json
#    → éditer config.json : ton pseudo, ta page de vote, tes sites

# 3. détecter les sites de vote depuis ta page serveur (et les écrire dans la config)
.venv/bin/python -m autovote_bot detect --config config.json --save

# 4. test : une seule passe
.venv/bin/python -m autovote_bot run --config config.json --once

# 5. suivi de la planification
.venv/bin/python -m autovote_bot status --config config.json
```

Ou via `./run.sh` (voir plus bas).

## La config (`config.json`)

```jsonc
{
  "server": {
    "name": "SkyOfSkill",
    "hubUrl": "https://skyofskill.fr/vote",   // ta page de vote (detection + vérif)
    "nick": "TonPseudoMinecraft",             // pseudo Minecraft (une seule fois)
    "voteVerify": ""                          // marqueur « voté » sur la page serveur
                                              // (texte, #id ou .classe) — optionnel
  },
  "sites": [
    { "url": "https://serveur-prive.net/…/vote", "cooldownHours": 1.5 },
    { "url": "https://serveur-minecraft.com/5834", "cooldownHours": 3 },
    { "url": "https://www.serveursminecraft.org/serveur/3136/", "cooldownHours": 24 }
    // Champs optionnels par site : "verify", "sitekey", "captcha", "cooldownHours"
  ],
  "timing": {
    "jitterMinutes": [5, 20],     // délai aléatoire ajouté à chaque vote
    "nightPause": ["02:00", "08:00"],   // [] = pas de pause nocturne
    "gapBetweenSitesSeconds": 45  // respiration entre deux sites
  },
  "solver": {
    "baseUrl": "http://127.0.0.1:8877",  // le sidecar captcha-solver (localhost)
    "autoStart": true,        // le bot le démarre s'il est éteint
    "headless": true,         // sans écran = moins de RAM/CPU par solve
    "keepAlive": false,       // le solveur s'arrête entre les votes (~300 Mo libérés)
    "timeoutSeconds": 180
    // "proxy": "http://user:pass@host:port"   // proxy résidentiel (recommandé)
  },
  "logFile": "bot.log"
}
```

## Emprise ressources (optimisée au minimum)

Le bot est conçu pour dormir : **entre deux votes il ne fait rien** (il s'endort
jusqu'à la prochaine échéance, CPU ≈ 0 %, aucun polling).

| État | RAM | CPU |
|---|---|---|
| Bot au repos (entre les votes) | **~20 Mo** (Python stdlib) | ~0 % |
| Solveur arrêté (`keepAlive: false`, entre les votes) | **0** | 0 |
| Pendant un vote (1 CAPTCHA en cours) | +1 à 1,5 Go (1 navigateur headless, temporaire) | 1 cœur à ~50-100 % |
| Solveur au repos s'il reste allumé (`keepAlive: true`) | +200-400 Mo | ~0 % |

Choix d'optimisation actifs :
- **Dormance jusqu'au prochain vote** (pas de réveil inutile, cap 30 min) ;
- **Solveur démarré à la demande** uniquement (premier vote CAPTCHA) ;
- **Solveur arrêté automatiquement** quand le prochain vote est à plus de 5 min
  (`keepAlive: false`) — relancé tout seul au besoin ;
- **Mode headless par défaut** (rendu non nécessaire) ;
- **Échec = réessai dans 45 min** (le cooldown du site n'a pas démarré), pas de
  boucle folle ni d'attente inutile de 24 h ;
- Le sidecar n'expose **aucun port réseau** (127.0.0.1 uniquement).

→ sur une machine de 4 Go, la machine reste à ~1 Go libre la quasi-totalité du
temps ; le pic (1,5 Go) dure la durée d'un solve (30 s à 3 min), une poignée de
fois par jour.

## Comment un vote se passe

1. Le bot lit la page de vote → repère le **type de CAPTCHA** (hCaptcha,
   reCAPTCHA…), sa **sitekey** et le **champ pseudo**.
2. Il demande au **sidecar** de résoudre le CAPTCHA **sur la page réelle**
   (mode `real_page`) : le sidecar remplit le pseudo, clique, résout le
   CAPTCHA et soumet — **depuis la même session et la même IP**, donc le
   token est valide.
3. Le bot **vérifie** : marqueur sur ta page serveur (`voteVerify`) ou marqueur
   « déjà voté » sur le site.
4. Il recalcule l'échéance : `maintenant + cooldown + jitter`, repoussé hors
   de la pause nocturne. L'état est persisté dans `state.json`.

Le site est **inconnu** du registre ? Le bot construit un profil générique à
la volée (CAPTCHA + champ pseudo détectés sur la page) — rien n'est codé pour
un serveur précis.

## Exécuter en service (24 h/24)

`run.sh` lance le bot en boucle. Sur un serveur, utilise systemd :

```ini
# /etc/systemd/system/autovote.service
[Unit]
Description=Auto Vote Bot
After=network-online.target

[Service]
WorkingDirectory=/chemin/vers/Auto_Vote/autovote-bot
ExecStart=/chemin/vers/Auto_Vote/.venv/bin/python -m autovote_bot run --config config.json
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now autovote
journalctl -u autovote -f
```

## Tests

```bash
python3 autovote-bot/test_autovote_bot.py
```

## Avertissements

- **Risque de bannissement** : comme tout système automatisé (Voxa l'avoue
  lui-même), aucune indétectabilité n'est garantie. Le jitter, la pause
  nocturne et les proxies résidentiels réduisent le risque ; utilise des
  proxies résidentiels pour les sites sensibles.
- **Respecte les CGU** des sites de vote et de ton serveur.
- Le bot n'invente rien : il vote **une seule fois** par cooldown, avec **ton**
  pseudo, comme un joueur ferait à la main.
