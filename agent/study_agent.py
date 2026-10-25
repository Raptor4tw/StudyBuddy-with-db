from __future__ import annotations

import json
import os
import random
import re
import time

from openai import OpenAI

from agent.prompts import (
    CONCEPT_EXTRACTION_PROMPT,
    EVALUATION_PROMPT,
    QUESTION_GENERATION_PROMPT,
    TOPIC_EXTRACTION_PROMPT,
)
from tools.rag import RAGIndex


_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 1.5


def _is_retryable(exc: Exception) -> bool:
    """True for transient server-side conditions: rate limits, overload, timeouts."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    return "too_many_concurrent_requests" in str(exc) or "rate_limit" in str(exc)


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_json(text: str):
    text = _strip_thinking(text)
    text = re.sub(r"```(?:json)?\s*", "", text).strip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, text)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return None


def _sample_text(text: str, total_chars: int | None = None, sections: int = 3) -> str:
    """Sample evenly spaced sections so long documents are fully represented.

    The sample budget scales with the document: a 5-page note uses the whole
    text, a 100-page script uses up to 15,000 chars spread across more sections.
    """
    if total_chars is None:
        if len(text) <= 6000:
            total_chars = len(text)
            sections = 1
        elif len(text) <= 20000:
            total_chars = 8000
            sections = 3
        elif len(text) <= 60000:
            total_chars = 12000
            sections = 5
        else:
            total_chars = 15000
            sections = 7

    if len(text) <= total_chars:
        return text

    piece = total_chars // sections
    if sections == 1:
        return text[:total_chars]
    stride = (len(text) - piece) // (sections - 1)
    return "\n\n[...]\n\n".join(
        text[i * stride : i * stride + piece] for i in range(sections)
    )


def _mark_covered(concepts: dict, names: list) -> None:
    by_norm = {c.strip().lower(): c for c in concepts}
    for name in names:
        key = by_norm.get(str(name).strip().lower())
        if key:
            concepts[key] = True


def _is_broken_topic(s: str) -> bool:
    """Reject anything that looks like a JSON key, metadata fragment, or
    truncated model output — these sometimes slip into topic lists."""
    if not s:
        return True
    low = s.lower()
    if "is_lecture_material" in low:
        return True
    if low in {"topics", "topic", "json", "true", "false", "null"}:
        return True
    if any(ch in s for ch in "{}[]"):
        return True
    if s[0] in "\"':" or s[-1] in "\"':":
        return True
    if len(s) < 3 or len(s) > 80:
        return True
    return False


def _clean_topics(items) -> list[str]:
    """Normalize and filter any iterable of candidate topic names."""
    if not items:
        return []
    if isinstance(items, dict):
        items = items.get("topics") or []
    cleaned: list[str] = []
    for t in items:
        s = str(t).strip().strip("\"'`").strip()
        if _is_broken_topic(s):
            continue
        if s not in cleaned:
            cleaned.append(s)
    return cleaned


def _sanitize_concepts(concepts: list[str]) -> list[str]:
    """Clean up concepts that the model returned as combined lists.

    The model sometimes returns things like "Knoten, Beziehungen und Eigenschaften"
    instead of three separate concepts. We split those and drop too-generic ones.
    """
    result: list[str] = []
    for c in concepts:
        c = c.strip()
        if not c:
            continue
        # Split on " und ", ", ", " / ", " & " — but only if the result
        # has at least 2 parts with >= 3 chars each
        parts: list[str] = [c]
        for sep in [" und ", ", ", " / ", " & ", "; "]:
            new_parts = []
            for p in parts:
                new_parts.extend(p.split(sep))
            parts = [p.strip() for p in new_parts if p.strip()]
        # If we got multiple parts, keep them as separate concepts
        if len(parts) > 1 and all(len(p) >= 3 for p in parts):
            for p in parts:
                if p not in result and len(p) <= 80:
                    result.append(p)
            continue
        # Too long, too short, or too generic → reject
        if len(c) < 4 or len(c) > 80:
            continue
        if c.lower() in {"cypher", "neo4j", "datenbank", "sql", "python", "c++", "java"}:
            continue
        if c not in result:
            result.append(c)
    return result or concepts


class StudyBuddyAgent:
    def __init__(self, text: str, rag: RAGIndex):
        self.text = text
        self.rag = rag
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL", "https://chat-ai.academiccloud.de/v1"),
        )
        self.model = os.getenv("MODEL", "qwen3-coder-30b-a3b-instruct")
        self.current_question: str | None = None
        self.current_topic: str | None = None
        self.current_concept: str | None = None
        self.concepts: dict[str, bool] = {}
        self.asked_questions: list[str] = []
        self.follow_up_count: int = 0
        self.max_follow_ups: int = 999  # effectively unlimited; the user skips manually
        self.usage: list[dict] = []

    def _complete(self, messages: list, model: str | None = None) -> str:
        """Call the LLM, retrying when the endpoint is momentarily saturated."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.client.chat.completions.create(
                    model=model or self.model,
                    messages=messages,
                )
                self._record_usage(model or self.model, resp)
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == _MAX_RETRIES - 1 or not _is_retryable(e):
                    raise
                time.sleep(delay + random.uniform(0, 0.4))
                delay *= 2
        return ""  # unreachable

    def _record_usage(self, model: str, resp) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.usage.append(
            {
                "model": model,
                "prompt_tokens": usage.prompt_tokens or 0,
                "completion_tokens": usage.completion_tokens or 0,
            }
        )

    def extract_topics(self) -> list[str]:
        sample = _sample_text(self.text)
        raw = self._complete(
            [{"role": "user", "content": TOPIC_EXTRACTION_PROMPT.format(text=sample)}]
        )
        parsed = _parse_json(raw)

        if isinstance(parsed, dict) and "is_lecture_material" in parsed:
            if not parsed["is_lecture_material"]:
                raise ValueError(
                    "Dieses Dokument scheint kein Lernmaterial zu sein. "
                    "StudyBuddy ist für Vorlesungen, Skripte und Übungen "
                    "gedacht — bitte lade ein entsprechendes Dokument hoch."
                )
            topics = _clean_topics(parsed.get("topics") or [])
            return topics[:20] if topics else ["Main Content"]

        if isinstance(parsed, list):
            topics = _clean_topics(parsed)
            if topics:
                return topics[:20]

        # Parsing failed. Retry once with a stricter prompt.
        retry_prompt = (
            TOPIC_EXTRACTION_PROMPT.format(text=sample)
            + "\n\nIMPORTANT: Respond with valid JSON only, no markdown, no explanation, "
              "no line breaks inside strings. The JSON must be parseable by json.loads()."
        )
        raw2 = self._complete([{"role": "user", "content": retry_prompt}])
        parsed2 = _parse_json(raw2)
        if isinstance(parsed2, dict) and "is_lecture_material" in parsed2:
            if not parsed2["is_lecture_material"]:
                raise ValueError(
                    "Dieses Dokument scheint kein Lernmaterial zu sein. "
                    "StudyBuddy ist für Vorlesungen, Skripte und Übungen "
                    "gedacht — bitte lade ein entsprechendes Dokument hoch."
                )
            topics = _clean_topics(parsed2.get("topics") or [])
            if topics:
                return topics[:20]
        if isinstance(parsed2, list):
            topics = _clean_topics(parsed2)
            if topics:
                return topics[:20]

        # Last resort: line-based extraction, then clean.
        for candidate in (raw2, raw):
            lines = [
                l.strip().strip('",-[]{}:').strip()
                for l in _strip_thinking(candidate).splitlines()
                if l.strip()
            ]
            topics = _clean_topics(lines)
            if topics:
                return topics[:20]

        return ["Main Content"]

    def _extract_concepts(self, topic: str, context: str) -> list[str]:
        raw = self._complete(
            [{"role": "user", "content": CONCEPT_EXTRACTION_PROMPT.format(topic=topic, context=context)}]
        )
        parsed = _parse_json(raw)
        if isinstance(parsed, list):
            concepts = [str(c).strip() for c in parsed if str(c).strip()][:8]
            if concepts:
                return _sanitize_concepts(concepts)[:5]
        return [topic]

    @property
    def progress(self) -> tuple[int, int]:
        return sum(self.concepts.values()), len(self.concepts)

    @property
    def topic_complete(self) -> bool:
        return bool(self.concepts) and all(self.concepts.values())

    def _next_uncovered(self) -> str | None:
        for concept, covered in self.concepts.items():
            if not covered:
                return concept
        return None

    def start_topic(self, topic: str) -> str:
        self.current_topic = topic
        self.follow_up_count = 0
        self.asked_questions = []
        context = self.rag.retrieve(topic, k=4)
        self.concepts = {c: False for c in self._extract_concepts(topic, context)}
        return self._generate_question()

    def _generate_question(self, model: str | None = None) -> str:
        self.current_concept = self._next_uncovered() or self.current_topic
        context = self.rag.retrieve(f"{self.current_topic} {self.current_concept}", k=4)
        asked = "\n".join(f"- {q}" for q in self.asked_questions) or "(none yet)"
        prompt = QUESTION_GENERATION_PROMPT.format(
            topic=self.current_topic or "the material",
            concept=self.current_concept,
            context=context,
            asked=asked,
        )
        q = _strip_thinking(self._complete([{"role": "user", "content": prompt}], model))
        self.current_question = q
        self.asked_questions.append(q)
        return q

    def _evaluate(self, question: str, student_answer: str, model: str | None = None) -> dict:
        """Judge an answer without touching the learning state."""
        context = self.rag.retrieve(f"{question} {student_answer}", k=5)
        focus = self.current_concept or self.current_topic or "the material"
        prompt = EVALUATION_PROMPT.format(
            context=context,
            concepts="\n".join(f"- {c}" for c in self.concepts),
            focus_concept=focus,
            question=question,
            answer=student_answer,
        )
        raw = self._complete([{"role": "user", "content": prompt}], model)
        parsed = _parse_json(raw)

        if not isinstance(parsed, dict):
            parsed = {
                "score": "partial",
                "feedback": _strip_thinking(raw)[:300] or "Please try again with more detail.",
                "follow_up": None,
                "missing": None,
                "covered_concepts": [],
            }

        # ── Hard enforcement ─────────────────────────────────────────────────
        # On partial/incorrect, the follow-up MUST re-ask the SAME focus concept.
        # The model sometimes drifts UP (to a broader topic) or DOWN (to a
        # sub-detail). We reject both and generate our own focused question
        # — but it must stay specific, so we rephrase the ORIGINAL question
        # rather than dropping to a generic "explain the concept".
        if parsed.get("score") in ("partial", "incorrect"):
            follow_up = (parsed.get("follow_up") or "").strip()
            focus_clean = (self.current_concept or self.current_topic or "").strip()

            if focus_clean:
                focus_tokens = [
                    w.lower() for w in re.findall(r"\w+", focus_clean) if len(w) > 2
                ]
                follow_up_lower = follow_up.lower()

                matched = sum(1 for w in focus_tokens if w in follow_up_lower)
                needed = max(1, len(focus_tokens) // 2 + 1)

                other_tokens = [
                    w for w in re.findall(r"\w+", follow_up_lower)
                    if len(w) > 3
                       and w not in focus_tokens
                       and w not in {
                           "kannst", "kann", "erklären", "erkläre", "beschreiben",
                           "beschreibe", "eigenen", "worten", "wofür", "braucht",
                           "genau", "bedeutet", "dieses", "diese", "dieser",
                           "example", "concept", "explain", "describe", "means",
                           "meaning", "used", "einfach", "einfachen", "kurz",
                           "nochmal", "einmal", "versuchen", "versuche",
                       }
                ]
                drift_up = len(other_tokens) >= 3

                if not follow_up or matched < needed or drift_up:
                    # Replace BOTH follow_up and feedback so the reply stays
                    # coherent — a feedback that mentions "Primärschlüssel" while
                    # the follow-up asks about "Fremdschlüssel" is confusing.
                    base_q = question.strip().rstrip("?").rstrip(".")
                    parsed["follow_up"] = (
                        f"Versuche es noch einmal: {base_q}? "
                        f"Wenn du unsicher bist, beschreibe zumindest, was "
                        f"'{focus_clean}' deiner Meinung nach bedeutet."
                    )
                    parsed["feedback"] = (
                        f"Das war noch nicht ganz richtig. Wir bleiben beim "
                        f"Thema '{focus_clean}' — versuch es noch einmal, "
                        f"auch mit einer kurzen, eigenen Formulierung."
                    )

        return parsed

    def evaluate_answer(self, student_answer: str) -> dict:
        parsed = self._evaluate(self.current_question, student_answer)

        _mark_covered(self.concepts, parsed.get("covered_concepts") or [])
        if parsed.get("score") == "correct" and self.current_concept in self.concepts:
            self.concepts[self.current_concept] = True

        # No follow-ups needed once the whole topic is mastered
        if self.topic_complete:
            parsed["follow_up"] = None

        # Every wrong/partial answer gets a follow-up until the concept is correct.
        # There is no upper limit — the user can skip manually if they want.
        if parsed.get("follow_up") and parsed.get("score") in ("partial", "incorrect"):
            self.follow_up_count += 1
            self.current_question = parsed["follow_up"]
            self.asked_questions.append(parsed["follow_up"])

        return parsed

    def second_opinion(self, question: str, student_answer: str, model: str) -> dict:
        """Re-judge an earlier answer with another model. Display only."""
        return self._evaluate(question, student_answer, model)

    def next_question(self) -> str:
        self.follow_up_count = 0
        return self._generate_question()

    def regenerate_question(self, model: str) -> str:
        """Replace the open question with a fresh one from another model."""
        self.follow_up_count = 0
        return self._generate_question(model)