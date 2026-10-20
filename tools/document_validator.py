"""
document_validator.py - Fast heuristic check that a document looks like
lecture material before we send it to the LLM.

This is the first line of defense: it rejects obvious invoices, payslips,
receipts, and contracts without spending an LLM call. The LLM-guard inside
StudyBuddyAgent.extract_topics() is the second line, catching the cases
that slip past the heuristics (unknown language, unusual format, etc.).
"""

from __future__ import annotations

import re

# Words that strongly suggest structured reference material
LEARNING_KEYWORDS = [
    "definition", "kapitel", "vorlesung", "seminar", "übung", "aufgabe",
    "beispiel", "satz", "theorem", "beweis", "lemma", "korollar", "formel",
    "abschnitt", "abbildung", "tabelle", "quelle", "literatur",
    "zusammenfassung", "einführung", "grundlagen", "methode", "algorithmus",
    "modell", "hypothese", "annahme", "fragestellung", "ergebnis",
    "chapter", "lecture", "section", "exercise", "example", "theorem",
    "proof", "definition", "summary", "introduction", "conclusion",
    "abstract", "figure", "equation", "algorithm", "hypothesis",
    "fragestellung", "kurs", "skript", "folien", "mitschrift",
]

# Words that suggest a financial, administrative, or personal document
ADMIN_KEYWORDS = [
    "rechnung", "rechnungsnummer", "invoice", "billing", "lohnabrechnung",
    "gehaltsabrechnung", "payslip", "lohnsteuer", "bruttolohn", "nettolohn",
    "sozialversicherung", "arbeitgeberanteil", "arbeitnehmeranteil",
    "steuerklasse", "krankenversicherung", "rentenversicherung",
    "iban", "bic", "bankverbindung", "kontonummer", "betrag", "summe",
    "mwst", "umsatzsteuer", "zahlbetrag", "fälligkeit", "vertragsnummer",
    "kundennummer", "bestellnummer", "auftragsnummer", "rechnungsdatum",
    "lieferdatum", "sehr geehrte", "mit freundlichen grüßen",
]


def _is_number_heavy(text: str, threshold: float = 0.35) -> bool:
    """True if a large share of tokens look like numbers (invoices, payslips)."""
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return False
    number_tokens = sum(1 for t in tokens if re.search(r"\d", t))
    return number_tokens / len(tokens) > threshold


def _keyword_score(text: str, keywords: list[str]) -> int:
    """Count how many distinct keywords appear (case-insensitive)."""
    lower = text.lower()
    return sum(1 for k in keywords if k in lower)


def check_is_learning_material(text: str) -> tuple[bool, str]:
    """
    Return (is_learning_material, reason).

    The reason is a short user-facing message explaining the rejection, or
    an empty string when the document is accepted.
    """
    stripped = text.strip()

    if len(stripped) < 500:
        return False, (
            "Das Dokument ist zu kurz, um Lernmaterial zu sein. "
            "Bitte lade eine vollständige Vorlesung, ein Skript oder "
            "Übungsblätter hoch."
        )

    if _is_number_heavy(stripped):
        return False, (
            "Das Dokument besteht überwiegend aus Zahlen. Das sieht eher "
            "nach einer Rechnung oder Abrechnung aus als nach Lernmaterial."
        )

    learning_hits = _keyword_score(stripped, LEARNING_KEYWORDS)
    admin_hits = _keyword_score(stripped, ADMIN_KEYWORDS)

    # Reject only when admin markers clearly dominate — leaves room for
    # math scripts with formulas and finance lectures with finance words.
    if admin_hits >= 2 and admin_hits > learning_hits:
        return False, (
            "Das Dokument enthält typische Merkmale einer Rechnung oder "
            "Abrechnung. StudyBuddy ist für Vorlesungsmaterial gedacht — "
            "bitte lade ein Skript oder Übungsblätter hoch."
        )

    return True, ""