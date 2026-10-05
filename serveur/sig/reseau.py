"""Client HTTP des appels sortants du serveur (services Esri, altimétrie IGN), selon la section « reseau ».

Certificats : magasin du système (Windows), qui contient l'autorité du proxy d'entreprise, ou fichier
déclaré dans « reseau.certificats ». La vérification n'est jamais désactivée.
"""
from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from .config import RACINE


def client(cfg: dict) -> httpx.Client:
    r = cfg["reseau"]
    certificats = r.get("certificats")
    if certificats and not Path(certificats).is_absolute():
        certificats = str(RACINE / certificats)
    contexte = ssl.create_default_context(cafile=certificats or None)
    return httpx.Client(proxy=r.get("proxy") or None, timeout=float(r["delai_secondes"]), verify=contexte,
                        trust_env=not r.get("proxy"), follow_redirects=True)


def message_erreur(e: httpx.HTTPError) -> str:
    """Message lisible, sans l'URL (qui pourrait porter un paramètre sensible)."""
    if isinstance(e, httpx.TimeoutException):
        return "délai dépassé"
    if "CERTIFICATE_VERIFY_FAILED" in str(e):
        return "certificat du serveur non reconnu par le poste (autorité à déclarer dans « reseau.certificats »)"
    if isinstance(e, httpx.ProxyError):
        return "proxy injoignable ou refus du proxy (« reseau.proxy » à vérifier)"
    return f"injoignable ({type(e).__name__})"
