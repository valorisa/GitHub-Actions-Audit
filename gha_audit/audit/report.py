"""Rendu du rapport d'audit — console, JSON, markdown — à partir des AuditItem.

Module pur : aucun appel réseau, aucune lecture/écriture disque. Chaque
fonction reçoit une liste d'AuditItem déjà construite par
audit/comparator.py::build_audit_items() et renvoie une chaîne. L'appelant
(CLI, à venir) décide quoi en faire (print, écriture fichier, corps de PR).

Cohérent avec la devise du projet ("les faits avant les décisions") : ce
module ne recalcule ni ne réinterprète aucun statut — il se contente
d'afficher ce que comparator.py a déjà décidé.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from gha_audit.models import AuditItem, VersionStatus

_STATUS_ICONS = {
    VersionStatus.OUTDATED: "🔴",
    VersionStatus.FLOATING: "🟡",
    VersionStatus.UNPINNED: "🟠",
    VersionStatus.UNRESOLVABLE: "⚪",
    VersionStatus.UP_TO_DATE: "✅",
}

# Ordre d'affichage : les statuts qui appellent une action humaine d'abord.
_STATUS_SORT_ORDER = {
    VersionStatus.OUTDATED: 0,
    VersionStatus.FLOATING: 1,
    VersionStatus.UNPINNED: 2,
    VersionStatus.UNRESOLVABLE: 3,
    VersionStatus.UP_TO_DATE: 4,
}


@dataclass(frozen=True)
class AuditSummary:
    """Comptage par statut — un fait dérivé simple, pas une décision."""

    total: int
    up_to_date: int
    outdated: int
    floating: int
    unpinned: int
    unresolvable: int


def build_summary(items: list[AuditItem]) -> AuditSummary:
    """Compte les items par statut. Ne fait aucune hypothèse d'ordre."""
    counts = {status: 0 for status in VersionStatus}
    for item in items:
        counts[item.status] += 1
    return AuditSummary(
        total=len(items),
        up_to_date=counts[VersionStatus.UP_TO_DATE],
        outdated=counts[VersionStatus.OUTDATED],
        floating=counts[VersionStatus.FLOATING],
        unpinned=counts[VersionStatus.UNPINNED],
        unresolvable=counts[VersionStatus.UNRESOLVABLE],
    )


def _sorted_items(items: list[AuditItem]) -> list[AuditItem]:
    return sorted(items, key=lambda i: (_STATUS_SORT_ORDER[i.status], i.identifier))


def _item_to_dict(item: AuditItem) -> dict:
    return {
        "identifier": item.identifier,
        "category": item.category,
        "file_path": item.file_path,
        "current": item.current,
        "latest": item.resolved.latest,
        "status": item.status.value,
        "relation": item.comparison.relation.value,
        "is_comparable": item.comparison.is_comparable,
        "source": item.resolved.source,
        "confidence": item.resolved.confidence,
        "note": item.resolved.note,
    }


def format_json(items: list[AuditItem], *, indent: int = 2) -> str:
    """Rapport complet en JSON — résumé + détail de chaque item."""
    summary = build_summary(items)
    payload = {
        "summary": asdict(summary),
        "items": [_item_to_dict(item) for item in _sorted_items(items)],
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def format_markdown(items: list[AuditItem]) -> str:
    """Rapport markdown — pensé pour être injecté tel quel dans un corps de PR."""
    summary = build_summary(items)
    lines = [
        "# Rapport d'audit GitHub Actions",
        "",
        f"**{summary.total} élément(s) audité(s)** — "
        f"{summary.outdated} obsolète(s), {summary.floating} flottant(s), "
        f"{summary.unpinned} non pinné(s), {summary.unresolvable} non résolu(s), "
        f"{summary.up_to_date} à jour.",
        "",
    ]

    if not items:
        lines.append("_Aucun élément audité._")
        return "\n".join(lines)

    lines += [
        "| Statut | Élément | Catégorie | Actuel | Dernier connu | Source |",
        "|---|---|---|---|---|---|",
    ]
    for item in _sorted_items(items):
        icon = _STATUS_ICONS[item.status]
        latest = item.resolved.latest or "—"
        lines.append(
            f"| {icon} {item.status.value} | `{item.identifier}` | {item.category} "
            f"| `{item.current}` | `{latest}` | {item.resolved.source} |"
        )

    return "\n".join(lines)


def format_console(items: list[AuditItem]) -> str:
    """Rapport texte brut, sans dépendance externe (pas de couleur ici —
    une CLI qui veut du rich/coloré peut consommer AuditItem directement
    et faire son propre rendu ; ce module reste la base testable sans
    terminal)."""
    summary = build_summary(items)
    lines = [
        f"gha-audit — {summary.total} élément(s) audité(s)",
        f"  outdated={summary.outdated}  floating={summary.floating}  "
        f"unpinned={summary.unpinned}  unresolvable={summary.unresolvable}  "
        f"up_to_date={summary.up_to_date}",
        "",
    ]

    if not items:
        lines.append("(aucun élément)")
        return "\n".join(lines)

    id_width = max(len(item.identifier) for item in items)
    for item in _sorted_items(items):
        latest = item.resolved.latest or "-"
        lines.append(
            f"[{item.status.value.upper():<13}] {item.identifier.ljust(id_width)}  "
            f"current={item.current}  latest={latest}"
        )

    return "\n".join(lines)
