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


def _sample_text(text: str, total_chars: int = 4800, sections: int = 3) -> str:
    """Sample evenly spaced sections so long documents are represented beyond their start."""
    if len(text) <= total_chars:
        return text
    piece = total_chars // sections
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
        self.max_follow_ups: int = 2

    def _complete(self, messages: list) -> str:
        """Call the LLM, retrying when the endpoint is momentarily saturated.

        The API limits how many requests may be in flight per key, so a shared
        deployment hits 429 whenever two people answer at the same time. Those
        are transient, so back off and retry instead of failing the session.
        """
        delay = _RETRY_BASE_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                if attempt == _MAX_RETRIES - 1 or not _is_retryable(e):
                    raise
                time.sleep(delay + random.uniform(0, 0.4))
                delay *= 2
        return ""  # unreachable: the loop either returns or raises

    def extract_topics(self) -> list[str]:
        sample = _sample_text(self.text)
        raw = self._complete(
            [{"role": "user", "content": TOPIC_EXTRACTION_PROMPT.format(text=sample)}]
        )
        parsed = _parse_json(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed if t][:8]
        # Fallback: pull non-empty lines that look like topic names
        lines = [
            l.strip().strip('",-[]').strip()
            for l in _strip_thinking(raw).splitlines()
            if l.strip()
        ]
        return [l for l in lines if 3 < len(l) < 80][:8] or ["Main Content"]

    def _extract_concepts(self, topic: str, context: str) -> list[str]:
        raw = self._complete(
            [{"role": "user", "content": CONCEPT_EXTRACTION_PROMPT.format(topic=topic, context=context)}]
        )
        parsed = _parse_json(raw)
        if isinstance(parsed, list):
            concepts = [str(c).strip() for c in parsed if str(c).strip()][:5]
            if concepts:
                return concepts
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

    def _generate_question(self) -> str:
        self.current_concept = self._next_uncovered() or self.current_topic
        context = self.rag.retrieve(f"{self.current_topic} {self.current_concept}", k=4)
        asked = "\n".join(f"- {q}" for q in self.asked_questions) or "(none yet)"
        prompt = QUESTION_GENERATION_PROMPT.format(
            topic=self.current_topic or "the material",
            concept=self.current_concept,
            context=context,
            asked=asked,
        )
        q = _strip_thinking(self._complete([{"role": "user", "content": prompt}]))
        self.current_question = q
        self.asked_questions.append(q)
        return q

    def evaluate_answer(self, student_answer: str) -> dict:
        context = self.rag.retrieve(
            f"{self.current_question} {student_answer}", k=5
        )
        prompt = EVALUATION_PROMPT.format(
            context=context,
            concepts="\n".join(f"- {c}" for c in self.concepts),
            question=self.current_question,
            answer=student_answer,
        )
        raw = self._complete([{"role": "user", "content": prompt}])
        parsed = _parse_json(raw)

        if not isinstance(parsed, dict):
            parsed = {
                "score": "partial",
                "feedback": _strip_thinking(raw)[:300] or "Please try again with more detail.",
                "follow_up": "Can you elaborate on that?",
                "missing": None,
                "covered_concepts": [],
            }

        _mark_covered(self.concepts, parsed.get("covered_concepts") or [])
        if parsed.get("score") == "correct" and self.current_concept in self.concepts:
            self.concepts[self.current_concept] = True

        # No follow-up needed once the topic is complete; enforce the limit otherwise
        if self.topic_complete or self.follow_up_count >= self.max_follow_ups:
            parsed["follow_up"] = None

        if parsed.get("follow_up") and parsed.get("score") in ("partial", "incorrect"):
            self.follow_up_count += 1
            self.current_question = parsed["follow_up"]
            self.asked_questions.append(parsed["follow_up"])

        return parsed

    def next_question(self) -> str:
        self.follow_up_count = 0
        return self._generate_question()
