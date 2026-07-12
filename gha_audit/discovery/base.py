"""Modèle commun pour une source de workflow, locale ou distante.

Le reste du pipeline (parser, resolver, audit) ne doit jamais savoir si le
YAML vient du disque ou de l'API GitHub — c'est tout l'intérêt de cette
abstraction : un seul WorkflowSource, deux façons de le peupler.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkflowSource:
    """Un fichier de workflow, avec son contenu déjà chargé en mémoire.

    `local_path` n'est renseigné que pour les sources locales — c'est ce
    qui détermine si `--fix` peut réécrire ce fichier. Une source distante
    (API, sans clone) a `local_path=None` : le writer doit refuser d'y
    toucher plutôt que d'improviser une écriture qui n'a pas de sens.
    """

    display_name: str          # ex: "ci.yml" ou "valorisa/stormgrill:.github/workflows/ci.yml"
    content: str
    local_path: Path | None = None
    repo_slug: str | None = None  # ex: "valorisa/stormgrill", None en mode local pur

    @property
    def is_writable(self) -> bool:
        return self.local_path is not None
