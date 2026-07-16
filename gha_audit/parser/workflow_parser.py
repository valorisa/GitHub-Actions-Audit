"""Extraction des `uses:` et des variables runtime depuis un workflow YAML.

Utilise ruamel.yaml en mode round-trip (typ="rt") : c'est la même instance
et le même arbre qui serviront plus tard à la réécriture ciblée dans
writer/yaml_updater.py, sans jamais reformatter le fichier entier.
"""

from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML

from gha_audit.models import ActionUsage, RuntimeVar, YamlPath
from gha_audit.parser.ref_classifier import classify_ref, parse_uses

# Capture les installs Go embarqués dans un `run:` (ex: `go install
# honnef.co/go/tools/cmd/staticcheck@latest`). Ce n'est PAS un `uses:`
# structuré : c'est du texte libre dans un step shell, donc un chemin
# d'extraction volontairement séparé de extract_actions().
_GO_INSTALL_RE = re.compile(r"go\s+install\s+(\S+@\S+)")

DEFAULT_RUNTIME_VAR_NAMES = frozenset(
    {"GO_VERSION", "NODE_VERSION", "PYTHON_VERSION", "JAVA_VERSION", "RUBY_VERSION"}
)


def get_yaml_loader() -> YAML:
    """Instance ruamel configurée pour préserver le style du fichier source.

    À réutiliser telle quelle pour l'écriture (writer/yaml_updater.py) afin
    que le round-trip charge/écrit reste cohérent.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096  # évite le retour à la ligne automatique de ruamel
    return yaml


def load_workflow(file_path: str | Path):
    """Charge un fichier YAML de workflow, arbre ruamel round-trip."""
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    return load_workflow_from_text(content)


def load_workflow_from_text(content: str):
    """Charge un workflow depuis une chaîne déjà en mémoire (round-trip).

    Nécessaire pour discovery/remote_discovery.py : une WorkflowSource
    distante n'a pas de fichier sur disque à ouvrir, seulement du contenu
    déjà récupéré via l'API GitHub Contents.
    """
    return get_yaml_loader().load(content)


def extract_actions(data, file_path: str) -> list[ActionUsage]:
    """Parcourt l'arbre et extrait toutes les occurrences de `uses:`.

    Une ref qui ne peut pas être parsée (pas de '@', pas de owner/repo)
    est silencieusement ignorée plutôt que de faire échouer tout le scan —
    ce n'est pas la responsabilité de ce module de valider la syntaxe YAML.
    """
    results: list[ActionUsage] = []

    def walk(node, path: tuple) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = path + (key,)
                if key == "uses" and isinstance(value, str) and "@" in value:
                    try:
                        parsed = parse_uses(value)
                    except ValueError:
                        continue
                    results.append(
                        ActionUsage(
                            file_path=file_path,
                            owner=parsed.owner,
                            repo=parsed.repo,
                            subpath=parsed.subpath,
                            ref=parsed.ref,
                            raw=value,
                            yaml_path=YamlPath(new_path),
                            kind=classify_ref(parsed),
                        )
                    )
                else:
                    walk(value, new_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, path + (idx,))

    walk(data, ())
    return results


def extract_go_installs(data, file_path: str) -> list[ActionUsage]:
    """Extrait les `go install owner.tld/path@ref` trouvés dans des `run:`.

    Réutilise parse_uses/classify_ref : un module Go (segment de domaine
    avec un point) est automatiquement classifié NON_GITHUB_RELEASE, donc
    signalé "non vérifiable automatiquement" plutôt que faussement comparé.
    Limitation connue : seul le motif `go install <path>@<ref>` est
    reconnu ; ce n'est pas un parseur de shell généraliste.
    """
    results: list[ActionUsage] = []

    def walk(node, path: tuple) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = path + (key,)
                if key == "run" and isinstance(value, str):
                    for match in _GO_INSTALL_RE.finditer(value):
                        raw = match.group(1)
                        try:
                            parsed = parse_uses(raw)
                        except ValueError:
                            continue
                        results.append(
                            ActionUsage(
                                file_path=file_path,
                                owner=parsed.owner,
                                repo=parsed.repo,
                                subpath=parsed.subpath,
                                ref=parsed.ref,
                                raw=raw,
                                yaml_path=YamlPath(new_path),  # pointe sur le `run:` parent, pas sur le sous-texte
                                kind=classify_ref(parsed),
                            )
                        )
                else:
                    walk(value, new_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, path + (idx,))

    walk(data, ())
    return results


def extract_runtime_vars(
    data,
    file_path: str,
    known_names: frozenset[str] = DEFAULT_RUNTIME_VAR_NAMES,
) -> list[RuntimeVar]:
    """Parcourt l'arbre et extrait les variables runtime déclarées dans env:.

    `known_names` est injectable pour éviter la magie implicite — l'appelant
    (CLI/config) contrôle explicitement quels runtimes sont audités.
    """
    results: list[RuntimeVar] = []

    def walk(node, path: tuple) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                new_path = path + (key,)
                if key in known_names and isinstance(value, (str, int, float)):
                    results.append(
                        RuntimeVar(
                            file_path=file_path,
                            name=key,
                            value=str(value),
                            yaml_path=YamlPath(new_path),
                        )
                    )
                walk(value, new_path)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, path + (idx,))

    walk(data, ())
    return results
