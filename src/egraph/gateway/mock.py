"""Deterministic offline backend.

Not a stub that returns fixed strings: a real rule-based extractor, summariser and
extractive answerer. That matters because the whole maintenance thesis - deletion removes
exactly the right subgraph, only dirty communities are recomputed - has to be provable in
CI on a laptop with no API key and no GPU. Swap ``EGRAPH_LLM_BACKEND=anthropic`` and the
same pipeline runs against a real model; the churn semantics are identical either way.
"""

from __future__ import annotations

import re

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "was",
    "were",
    "which",
    "who",
    "will",
    "with",
    "after",
    "before",
    "when",
    "while",
    "also",
    "into",
    "than",
    "then",
    "there",
    "these",
    "those",
    "we",
    "you",
    "your",
    "our",
    "not",
    "no",
    "can",
    "could",
    "would",
    "should",
}

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
CHUNK_PREFIX = re.compile(r"^\[[^\]]+\]\s*")
RELATION_SUFFIX = re.compile(r"\s*\[relations:[^\]]*\]\s*$")

NO_ANSWER = "The indexed sources do not contain this information."
# Below this match strength the context does not actually address the question. Answering
# anyway is how an index keeps "answering" questions whose sources were deleted.
RELEVANCE_FLOOR = 0.25
PROPER_NOUN = re.compile(r"\b(?:[A-Z][\w&.-]*)(?:\s+(?:of|for|and|de|the))?(?:\s+[A-Z][\w&.-]*)*\b")

TYPE_HINTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(inc|ltd|llc|corp|corporation|university|institute|labs?|group|foundation)\b", re.I
        ),
        "organization",
    ),
    (re.compile(r"\b(city|state|province|country|region|valley|river)\b", re.I), "location"),
    (
        re.compile(
            r"\b(protocol|engine|platform|framework|database|model|algorithm|system|api)\b", re.I
        ),
        "technology",
    ),
    (re.compile(r"\b(conference|summit|launch|merger|acquisition|release)\b", re.I), "event"),
]

# Verb patterns worth turning into a typed edge. Ordered: first match wins.
RELATION_VERBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bacquir\w*\b", re.I), "acquired"),
    (re.compile(r"\b(founded|co-founded|established)\b", re.I), "founded"),
    (re.compile(r"\b(leads?|led|heads?|headed|manages?|directs?)\b", re.I), "leads"),
    (re.compile(r"\b(works? for|employed by|joined|hired by)\b", re.I), "works_for"),
    (re.compile(r"\b(reports? to)\b", re.I), "reports_to"),
    (re.compile(r"\b(built|builds|develops?|developed|created?|maintains?)\b", re.I), "develops"),
    (re.compile(r"\b(uses?|adopted|deployed|runs? on|powered by)\b", re.I), "uses"),
    (re.compile(r"\b(located in|based in|headquartered in|opened in)\b", re.I), "located_in"),
    (re.compile(r"\b(partnered|collaborat\w+|works? with)\b", re.I), "partners_with"),
    (re.compile(r"\b(replaced|succeeded|superseded)\b", re.I), "replaced"),
    (re.compile(r"\b(owns?|owned)\b", re.I), "owns"),
    (re.compile(r"\b(published|released|announced|shipped)\b", re.I), "released"),
]


def _guess_type(name: str, sentence: str) -> str:
    for pattern, etype in TYPE_HINTS:
        if pattern.search(name):
            return etype
    window = sentence.lower()
    if re.search(rf"(?:dr\.?|mr\.?|ms\.?|prof\.?)\s+{re.escape(name.lower())}", window):
        return "person"
    if re.search(
        rf"{re.escape(name.lower())},?\s+(?:the\s+)?(?:ceo|cto|founder|engineer|researcher|professor|director)",
        window,
    ):
        return "person"
    # Two capitalised words is a weak person signal and fires on "Northwind Analytics"
    # as readily as on "Marit Solberg", so require a person-shaped context.
    if len(name.split()) == 2 and all(w[:1].isupper() for w in name.split()):
        lowered = name.lower()
        person_context = re.search(
            rf"{re.escape(lowered)}\s+(?:leads?|led|founded|co-founded|sponsors?|joined|"
            rf"works?|worked|remains?|moved|is the|previously)",
            window,
        ) or re.search(
            rf"(?:founded|led|sponsored|co-led|hired)\s+by\s+{re.escape(lowered)}", window
        )
        if person_context:
            return "person"
    return "concept"


def _candidates(sentence: str) -> list[str]:
    """Proper-noun phrases, minus sentence-initial common words."""
    found: list[str] = []
    for match in PROPER_NOUN.finditer(sentence):
        name = match.group(0).strip(" .,;:")
        if not name or name.lower() in STOPWORDS:
            continue
        # A sentence-initial single capitalised common word is not an entity.
        if match.start() == 0 and len(name.split()) == 1 and name.lower() in STOPWORDS:
            continue
        if len(name) < 3 or name.isupper() and len(name) < 3:
            continue
        words = name.split()
        while words and words[0].lower() in STOPWORDS:
            words = words[1:]
        while words and words[-1].lower() in STOPWORDS:
            words = words[:-1]
        if not words:
            continue
        name = " ".join(words)
        if match.start() == 0 and len(words) == 1 and name.lower() in STOPWORDS:
            continue
        if name not in found:
            found.append(name)
    return found


def extract(text: str) -> dict:
    """Rule-based entity/relation extraction over a chunk. Pure and deterministic."""
    entities: dict[str, dict] = {}
    relations: list[dict] = []
    for sentence in SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        names = _candidates(sentence)
        for name in names:
            if name not in entities:
                entities[name] = {
                    "name": name,
                    "type": _guess_type(name, sentence),
                    "description": sentence[:240],
                }
        relation_type = "related_to"
        for pattern, label in RELATION_VERBS:
            if pattern.search(sentence):
                relation_type = label
                break
        # Chain consecutive entities in the sentence: A -> B, B -> C.
        for left, right in zip(names, names[1:], strict=False):
            if left == right:
                continue
            relations.append(
                {
                    "source": left,
                    "target": right,
                    "relation_type": relation_type,
                    "description": sentence[:240],
                }
            )
    return {"entities": list(entities.values()), "relations": relations}


def summarise(entity_lines: list[str], relation_lines: list[str]) -> dict:
    """Template summary. Deterministic, and faithful to the members it was given."""
    names = [line.split(" (")[0] for line in entity_lines]
    head = ", ".join(names[:3]) if names else "an empty region of the graph"
    title = f"{names[0]} and related entities" if names else "Empty community"
    body = [
        f"This community covers {len(names)} entities, centred on {head}.",
    ]
    if relation_lines:
        body.append("Recorded relations: " + "; ".join(relation_lines[:8]) + ".")
    if len(names) > 3:
        body.append("It also includes " + ", ".join(names[3:12]) + ".")
    return {"title": title[:80], "summary": " ".join(body)}


def answer(question: str, context: str) -> str:
    """Extractive answer: the context line that best matches the question.

    Deliberately incapable of answering from anything but the supplied context - which is
    exactly the property the deletion benchmark tests - and it abstains below
    ``RELEVANCE_FLOOR`` rather than returning the least-bad line. Abstention matters: after
    a document is deleted, retrieval still returns *something*, and a reader that always
    answers would look like it never noticed the deletion.
    """
    q_tokens = {w for w in re.findall(r"\w+", question.lower()) if w not in STOPWORDS}
    if not q_tokens:
        return NO_ANSWER
    scored: list[tuple[float, str]] = []
    for raw in context.splitlines():
        line = _clean_line(raw)
        if len(line) < 8:
            continue
        tokens = {w for w in re.findall(r"\w+", line.lower()) if w not in STOPWORDS}
        if not tokens:
            continue
        overlap = len(q_tokens & tokens)
        if overlap:
            # Cosine-style normalisation. Without the line-length term a long community
            # summary outscores the one precise passage that actually answers the
            # question, purely by covering more words.
            scored.append((overlap / ((len(q_tokens) * len(tokens)) ** 0.5), line))
    if not scored:
        return NO_ANSWER
    scored.sort(key=lambda pair: -pair[0])
    best_score, best_line = scored[0]
    if best_score < RELEVANCE_FLOOR:
        return NO_ANSWER
    # A near-tie usually means the answer is split across two passages; anything further
    # down is noise and only costs precision.
    answer_lines = [best_line]
    if len(scored) > 1 and scored[1][0] >= best_score * 0.85:
        answer_lines.append(scored[1][1])
    return " ".join(answer_lines)[:400]


def _clean_line(line: str) -> str:
    """Strip the context markup so the answer reads as prose, not as a data structure."""
    line = line.strip().lstrip("-").strip()
    line = CHUNK_PREFIX.sub("", line)
    line = RELATION_SUFFIX.sub("", line)
    return line.strip()
