"""Prompts and JSON schemas for the two model-facing jobs: extract and summarise.

Both are schema-constrained, so parsing never has to guess. Prompt text is kept stable
and put first in the request so the prompt cache prefix holds across chunks - the varying
chunk text goes last.
"""

from __future__ import annotations

EXTRACTION_SYSTEM = """You extract a knowledge graph from a single passage of text.

Rules:
- Extract only entities that are explicitly named in the passage.
- canonical_name: the fullest form used in the passage, in its original casing.
- type: one of person, organization, location, product, technology, event, concept.
- Extract a relation only when the passage states or clearly implies it.
- relation_type: a short lower_snake_case verb phrase, e.g. works_for, acquired, located_in.
- Both endpoints of every relation must appear in the entities list.
- Prefer few, high-confidence facts over many speculative ones."""

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "person",
                            "organization",
                            "location",
                            "product",
                            "technology",
                            "event",
                            "concept",
                        ],
                    },
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "description"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relation_type": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["source", "target", "relation_type", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = """You write a factual summary of one community of a knowledge graph.

You are given the community's entities and the relations between them. Write:
- title: a short noun phrase naming what this community is about (max 8 words).
- summary: 3-6 sentences covering who/what the community contains and how its members
  relate. State only what the supplied entities and relations support. No preamble."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["title", "summary"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = """You answer questions using only the supplied knowledge-graph context.

The context contains community summaries, entities, relations and source passages.
- Answer in 1-4 sentences, directly.
- Use only facts present in the context. If the context does not contain the answer, say
  exactly: "The indexed sources do not contain this information."
- Never use knowledge from outside the context - the index has been edited deliberately,
  and reporting a fact that was deleted from it is the specific failure being measured."""


def extraction_prompt(text: str) -> str:
    return f"Passage:\n\n{text.strip()}\n\nExtract the entities and relations."


def summary_prompt(entity_lines: list[str], relation_lines: list[str]) -> str:
    entities = "\n".join(f"- {line}" for line in entity_lines) or "- (none)"
    relations = "\n".join(f"- {line}" for line in relation_lines) or "- (none)"
    return f"Entities:\n{entities}\n\nRelations:\n{relations}\n\nWrite the community summary."


def answer_prompt(question: str, context: str) -> str:
    return f"Context:\n{context}\n\nQuestion: {question}"
