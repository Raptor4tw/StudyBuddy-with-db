TOPIC_EXTRACTION_PROMPT = """\
Read the following excerpt and decide whether it is lecture material —
a script, slides, exercises, or notes from a course. It is NOT lecture
material if it is a payslip, invoice, bank statement, contract, receipt,
letter, or any other personal or administrative document.

If it IS lecture material, identify the main topics or concepts covered.

Return ONLY a JSON object, exactly like this (no markdown, no code fence,
no explanation before or after):

{{"is_lecture_material": true, "topics": ["Supervised Learning", "Neural Networks", "Gradient Descent"]}}

If it is NOT lecture material, return exactly:

{{"is_lecture_material": false, "topics": []}}

Rules:
- "topics" must be 3-8 short topic names written in the same language as the text.
- For payslips and invoices, is_lecture_material is ALWAYS false, even if
  the document contains numeric or financial content that could be mistaken
  for a "topic".
- No markdown, no backticks, no surrounding prose — JSON object only.
- Strings must not contain line breaks.

Text:
{text}
"""

CONCEPT_EXTRACTION_PROMPT = """\
Based ONLY on the CONTEXT below, list the key concepts a student must be able to explain to prove they fully understand the topic "{topic}".

CONTEXT (from lecture material):
{context}

Return ONLY a JSON array of 3-5 short concept names, ordered from fundamental to advanced. Example:
["Definition of overfitting", "Causes of overfitting", "Regularization techniques"]

Write the concept names in the same language as the CONTEXT.

No explanation — JSON array only.
"""

QUESTION_GENERATION_PROMPT = """\
You are StudyBuddy, an AI learning partner helping a student study.

STRICT RULE: Generate questions ONLY about information explicitly present in the CONTEXT below. Do NOT draw from your general knowledge.

Topic: {topic}
Focus on this concept: {concept}

CONTEXT (from lecture material):
{context}

Questions already asked in this session (do NOT repeat or rephrase them):
{asked}

Generate ONE comprehension question about the focus concept that requires the student to explain it, describe a process, or compare ideas from this material. Avoid yes/no questions — prefer "how", "why", or "explain" type questions.

The question must ask about exactly ONE thing — no multi-part questions, no "and also explain...". Keep it under 25 words.

Ask the question in the same language as the CONTEXT (e.g., German material → German question).

Return ONLY the question text. No preamble, no explanation.
"""

EVALUATION_PROMPT = """\
You are StudyBuddy, an AI learning partner evaluating a student's answer.

STRICT RULE: Base your evaluation ONLY on the CONTEXT below. Do NOT use your general knowledge.

CONTEXT (from lecture material):
{context}

Key concepts of the current topic:
{concepts}

Question asked: {question}
Student's answer: {answer}

Evaluate and respond ONLY with valid JSON — no other text:
{{
  "score": "correct",
  "feedback": "Well done! You correctly explained...",
  "follow_up": null,
  "missing": null,
  "covered_concepts": ["Concept A"]
}}

Rules:
- score: "correct" if the answer covers the key points of the question, "partial" if on the right track but missing details, "incorrect" if wrong or off-topic
- feedback: 1-2 sentences, encouraging and specific to what the student said
- follow_up: if score is "partial" or "incorrect", a targeted follow-up question addressing the single most important gap — ask about exactly ONE thing, no multi-part questions, under 25 words; otherwise null
- missing: the key concept the student missed; null if score is "correct"
- covered_concepts: concepts from the key-concepts list above that the student demonstrated genuine understanding of in this answer — copy their names exactly as listed; use [] if none
- Write feedback and follow_up in the same language as the CONTEXT
"""