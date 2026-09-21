"""
tests/test_evaluation_state_machine.py

Unit tests for StudyBuddyAgent.evaluate_answer()'s state machine: concept
coverage, follow-up limits, topic completion, and the malformed-response
fallback. The LLM call itself (_complete) is mocked so these tests are
fast, deterministic, and never hit the real API.

What this deliberately does NOT test: whether the LLM correctly judges a
given answer as correct/partial/incorrect/off-topic. That's a model-quality
question, not app logic — see the "curated eval set" script instead for
checking real scoring behavior on right/wrong/off-topic answers.
"""
import json
from unittest.mock import MagicMock

import pytest

from agent.study_agent import StudyBuddyAgent


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch):
    """StudyBuddyAgent.__init__ constructs an OpenAI client immediately and
    raises if no API key is present — even though these tests mock _complete()
    and never make a real call. Set a dummy key so construction succeeds."""
    monkeypatch.setenv("API_KEY", "test-key-not-used")
    monkeypatch.setenv("BASE_URL", "https://example.invalid/v1")


class FakeRAG:
    ...
    """A stand-in for RAGIndex that skips embeddings entirely — this suite
    tests evaluate_answer()'s logic, not retrieval, so a fixed string is
    all _complete()'s prompt-building needs."""

    def retrieve(self, query: str, k: int = 4) -> str:
        return "This document explains normalization, keys, and dependencies."


def make_agent(concepts=None, current_topic="Normalization",
               current_concept="3NF definition", current_question="What is 3NF?"):
    agent = StudyBuddyAgent(text="x" * 100, rag=FakeRAG())
    agent.current_topic = current_topic
    agent.current_concept = current_concept
    agent.current_question = current_question
    agent.concepts = concepts if concepts is not None else {
        "1NF definition": True,
        "2NF definition": True,
        "3NF definition": False,
    }
    return agent


def mock_llm_response(agent: StudyBuddyAgent, response: dict):
    """Replace the LLM call with a canned JSON response."""
    agent._complete = MagicMock(return_value=json.dumps(response))


# ── Correct answers ──────────────────────────────────────────────────────────

def test_correct_answer_marks_concept_via_covered_concepts():
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "correct",
        "feedback": "Exactly right.",
        "follow_up": None,
        "missing": None,
        "covered_concepts": ["3NF definition"],
    })

    result = agent.evaluate_answer("3NF removes transitive dependencies.")

    assert result["score"] == "correct"
    assert agent.concepts["3NF definition"] is True
    assert agent.topic_complete is True


def test_correct_answer_marks_current_concept_even_without_covered_concepts():
    """evaluate_answer() has a fallback: a 'correct' score marks current_concept
    covered even if the LLM forgot to list it in covered_concepts."""
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "correct",
        "feedback": "Good.",
        "follow_up": None,
        "missing": None,
        "covered_concepts": [],
    })

    agent.evaluate_answer("3NF removes transitive dependencies.")

    assert agent.concepts["3NF definition"] is True


# ── Partial / incorrect answers ─────────────────────────────────────────────

def test_partial_answer_sets_follow_up_and_increments_count():
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "You're close, but missing a detail.",
        "follow_up": "Can you give an example of a transitive dependency?",
        "missing": "example of transitive dependency",
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("It's about normal forms I think.")

    assert result["follow_up"] == "Can you give an example of a transitive dependency?"
    assert agent.follow_up_count == 1
    assert agent.current_question == result["follow_up"]
    assert agent.current_question in agent.asked_questions
    assert agent.concepts["3NF definition"] is False  # not covered yet


def test_incorrect_offtopic_answer_does_not_cover_any_concept():
    """Simulates a student answer that has nothing to do with the question —
    the LLM is expected to return score='incorrect' with no covered concepts."""
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "incorrect",
        "feedback": "This doesn't address the question about 3NF.",
        "follow_up": "Let's refocus — what defines 3NF specifically?",
        "missing": "3NF definition",
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("I like pizza and the weather is nice today.")

    assert result["score"] == "incorrect"
    assert agent.concepts["3NF definition"] is False
    assert agent.follow_up_count == 1  # still counts as a follow-up attempt


# ── Follow-up limit ──────────────────────────────────────────────────────────

def test_follow_up_suppressed_after_max_follow_ups_reached():
    agent = make_agent()
    agent.follow_up_count = agent.max_follow_ups  # already at the limit

    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Still not quite there.",
        "follow_up": "One more try — what makes it transitive?",
        "missing": "3NF definition",
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("Not sure.")

    assert result["follow_up"] is None  # suppressed by the app.py "retry from new angle" path
    assert agent.follow_up_count == agent.max_follow_ups  # not incremented further


def test_follow_up_suppressed_once_topic_is_complete():
    """Even a low-confidence score shouldn't trigger a follow-up once every
    concept is already covered — there's nothing left to dig into."""
    agent = make_agent(concepts={
        "1NF definition": True,
        "2NF definition": True,
        "3NF definition": True,  # already fully covered
    })
    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Fine, but topic is already complete.",
        "follow_up": "Want to go deeper?",
        "missing": None,
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("Some answer.")

    assert result["follow_up"] is None
    assert agent.follow_up_count == 0  # never incremented since follow-up was suppressed


# ── Malformed / unparseable LLM response ────────────────────────────────────

def test_malformed_response_falls_back_to_neutral_partial():
    """If the LLM returns something _parse_json can't parse at all, evaluate_answer
    must never crash — it should fall back to a neutral 'partial' evaluation."""
    agent = make_agent()
    agent._complete = MagicMock(return_value="Sorry, I cannot help with that request.")

    result = agent.evaluate_answer("asdkjhaskjdh")

    assert result["score"] == "partial"
    assert result["follow_up"] is not None
    assert agent.concepts["3NF definition"] is False


def test_empty_response_falls_back_gracefully():
    agent = make_agent()
    agent._complete = MagicMock(return_value="")

    result = agent.evaluate_answer("")

    assert result["score"] == "partial"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])