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

def test_correct_answer_masters_only_the_focus_concept():
    """Wrong answer, then a correct one: progress goes up by exactly one, even
    if the model says the answer also touched other concepts (2/5 -> 3/5)."""
    agent = make_agent(current_concept="C3", concepts={
        "C1": True, "C2": True, "C3": False, "C4": False, "C5": False,
    })
    mock_llm_response(agent, {
        "score": "incorrect", "feedback": "No.", "follow_up": "Try C3 again?",
        "follow_up_concept": "C3", "missing": "C3", "covered_concepts": ["C4"],
    })
    agent.evaluate_answer("wrong")
    assert agent.progress == (2, 5)

    mock_llm_response(agent, {
        "score": "correct", "feedback": "Yes.", "follow_up": None,
        "missing": None, "covered_concepts": ["C3", "C4", "C5"],
    })
    agent.evaluate_answer("right")
    assert agent.progress == (3, 5)
    assert agent.concepts["C3"] is True
    assert agent.concepts["C4"] is False


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
        "follow_up_concept": "3NF definition",
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

def test_follow_ups_continue_while_concept_is_open():
    """There is no follow-up limit any more: the student stays on the concept
    until it is correct or they skip it."""
    agent = make_agent()
    agent.follow_up_count = 10

    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Still not quite there.",
        "follow_up": "One more try — what makes it transitive?",
        "follow_up_concept": "3NF definition",
        "missing": "3NF definition",
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("Not sure.")

    assert result["follow_up"] == "One more try — what makes it transitive?"
    assert agent.follow_up_count == 11


def test_model_follow_up_and_feedback_are_kept():
    """A good follow-up that uses other words than the concept name must not
    be swapped for a canned text, and the model's feedback must survive."""
    agent = make_agent(current_concept="Primärschlüssel", concepts={"Primärschlüssel": False})
    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Fast: du hast Eindeutigkeit erwähnt, aber nicht NOT NULL.",
        "follow_up": "Kannst du ein konkretes Beispiel für einen Primärschlüssel nennen?",
        "follow_up_concept": "Primärschlüssel",
        "missing": "NOT NULL",
        "covered_concepts": [],
    })

    result = agent.evaluate_answer("Er ist eindeutig.")

    assert result["follow_up"] == "Kannst du ein konkretes Beispiel für einen Primärschlüssel nennen?"
    assert result["feedback"] == "Fast: du hast Eindeutigkeit erwähnt, aber nicht NOT NULL."


def test_drifting_follow_up_is_rewritten_for_the_focus_concept():
    """The evaluator says its follow-up is about another concept, so a narrow
    second prompt writes one about the focus concept; the feedback stays."""
    agent = make_agent()
    evaluation = json.dumps({
        "score": "partial",
        "feedback": "You described 2NF, not 3NF.",
        "follow_up": "What is a partial dependency in 2NF?",
        "follow_up_concept": "2NF definition",
        "missing": "transitive dependency",
        "covered_concepts": [],
    })
    agent._complete = MagicMock(side_effect=[evaluation, "Which dependency does 3NF remove?"])

    result = agent.evaluate_answer("No partial dependencies.")

    assert result["follow_up"] == "Which dependency does 3NF remove?"
    assert result["feedback"] == "You described 2NF, not 3NF."
    assert "Focus concept: 3NF definition" in agent._complete.call_args.args[0][0]["content"]
    assert agent.current_question == "Which dependency does 3NF remove?"


def test_missing_follow_up_falls_back_to_the_same_question():
    """No follow-up from the evaluator and none from the second prompt: ask the
    same question again, which is already in the material's language."""
    agent = make_agent(current_question="What is 3NF?")
    evaluation = json.dumps({
        "score": "incorrect",
        "feedback": "That is not right.",
        "follow_up": None,
        "missing": "3NF definition",
        "covered_concepts": [],
    })
    agent._complete = MagicMock(side_effect=[evaluation, ""])

    result = agent.evaluate_answer("No idea.")

    assert result["follow_up"] == "What is 3NF?"
    assert agent.current_question == "What is 3NF?"


def test_second_opinion_never_rewrites_the_follow_up():
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Second model's view.",
        "follow_up": "Off-focus question?",
        "follow_up_concept": "2NF definition",
        "missing": None,
        "covered_concepts": [],
    })

    agent.second_opinion("What is 3NF?", "Something.", "Other-Model")

    assert agent._complete.call_count == 1  # no extra follow-up call


def test_second_opinion_returns_the_other_models_feedback():
    agent = make_agent()
    mock_llm_response(agent, {
        "score": "partial",
        "feedback": "Specific feedback from the second model.",
        "follow_up": "Can you give an example?",
        "missing": "example",
        "covered_concepts": [],
    })

    result = agent.second_opinion("What is 3NF?", "Something.", "Other-Model")

    assert result["feedback"] == "Specific feedback from the second model."


def test_skip_keeps_concept_open_and_asks_it_again_later():
    agent = make_agent(current_concept="2NF definition", concepts={
        "1NF definition": True,
        "2NF definition": False,
        "3NF definition": False,
    })
    agent._complete = MagicMock(return_value="Next question?")

    agent.skip_question()
    assert agent.concepts["2NF definition"] is False  # skipping is not mastering
    assert agent.current_concept == "3NF definition"  # others first
    assert not agent.topic_complete

    agent.concepts["3NF definition"] = True
    agent.next_question()
    assert agent.current_concept == "2NF definition"  # comes back to the skipped one


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

# ── Model choice & usage ─────────────────────────────────────────────────────

def test_second_opinion_does_not_change_progress():
    agent = make_agent()
    mock_llm_response(agent, {"score": "correct", "feedback": "Good.",
                              "follow_up": None, "missing": None,
                              "covered_concepts": ["3NF definition"]})
    before = (dict(agent.concepts), agent.current_question, agent.follow_up_count)
    result = agent.second_opinion("What is 3NF?", "No transitive deps.", "Other-Model")
    assert result["score"] == "correct"
    assert (agent.concepts, agent.current_question, agent.follow_up_count) == before
    assert agent._complete.call_args.args[1] == "Other-Model"


def test_regenerate_question_uses_given_model_and_keeps_history():
    agent = make_agent()
    agent.asked_questions = ["What is 3NF?"]
    agent.follow_up_count = 2
    agent._complete = MagicMock(return_value="Explain transitive dependencies.")
    q = agent.regenerate_question("Other-Model")
    assert q == agent.current_question == "Explain transitive dependencies."
    assert agent.asked_questions == ["What is 3NF?", q]
    assert agent.follow_up_count == 0
    assert agent._complete.call_args.args[1] == "Other-Model"


def test_usage_recorded_per_call():
    agent = make_agent()
    resp = MagicMock()
    resp.choices[0].message.content = "{}"
    resp.usage.prompt_tokens = 120
    resp.usage.completion_tokens = 30
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = resp
    agent._complete([{"role": "user", "content": "hi"}], "M2")
    assert agent.usage == [{"model": "M2", "prompt_tokens": 120, "completion_tokens": 30}]
