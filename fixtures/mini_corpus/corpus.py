"""The offline mini-corpus.

Small, synthetic and deliberately structured so that the maintenance properties are
*checkable* rather than merely demonstrable:

* ``BASE_DOCS``    - the initial corpus.
* ``UPDATED_DOCS`` - later versions of two base documents. ``northwind`` is a **local
  edit** (one paragraph changes, so most chunks are reused and the update is cheap);
  ``kestrel-systems`` is a **rewrite** (almost nothing is reused). Both paths matter: the
  first is the cost claim, the second is the honesty check on it.
* ``NEW_DOCS``     - documents that arrive after the base index was built.
* ``DELETION_SET`` - documents whose facts are directly queried, so deleting them must
  change the answers.

Two properties are engineered in on purpose:

1. Some facts are asserted by exactly **one** document (they must disappear on delete) and
   others by **two** (they must survive, with weakened support). A deletion test with only
   the first kind passes by deleting too much.
2. The graph has genuinely separable regions (Helios/Aurora, Northwind, Kestrel/Vantage),
   so "only the affected communities were recomputed" is a claim with something to be
   affected.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    question: str
    answer: str
    # Strings an answer must contain to count as correct.
    must_include: tuple[str, ...] = ()
    # Strings that must NOT appear. After a deletion these are the stale-fact detector.
    must_exclude: tuple[str, ...] = ()
    supported_by: tuple[str, ...] = ()
    kind: str = "base"


BASE_DOCS: dict[str, str] = {
    "helios-overview": """Helios Labs is an independent systems research organization based in Trondheim.
Helios Labs was founded by Marit Solberg in 2019 as a spin-out of the Trondheim Institute.

Helios Labs develops Aurora Engine, a streaming graph database used for real-time analytics.
Aurora Engine is the flagship product of Helios Labs and powers several commercial deployments.

Marit Solberg leads the systems group at Helios Labs. The systems group maintains Aurora
Engine and the Fjord Protocol, and publishes an annual reliability report.

Helios Labs employs roughly ninety researchers. Helios Labs is funded by the Nordic Research
Council and by revenue from Aurora Engine licences.

Helios Labs partnered with Kestrel Systems on the Meridian project in 2023. The Meridian
project is sponsored jointly by both organizations.""",
    "aurora-engine": """Aurora Engine is a streaming graph database built for continuous analytical queries.
Aurora Engine was released in 2021 by Helios Labs after two years of internal development.

Aurora Engine uses the Fjord Protocol for replication between clusters. The Fjord Protocol
gives Aurora Engine strong consistency across regions.

The Fjord Protocol replaced the older Sable Protocol in 2022. The Sable Protocol was
deprecated because of its weak consistency guarantees under partition.

Northwind Analytics deployed Aurora Engine for its fraud detection platform in 2022.
Northwind Analytics is headquartered in Rotterdam and runs Aurora Engine on three clusters.

Aurora Engine exposes a query language called Lumen. Lumen was designed by the systems
group at Helios Labs.""",
    "northwind": """Northwind Analytics is a data company headquartered in Rotterdam.
Northwind Analytics was founded by Pieter Vos in 2015.

Northwind Analytics uses Aurora Engine for fraud detection across European payment networks.
Northwind Analytics processes several million transactions per day.

Northwind Analytics partnered with Helios Labs in 2022. The partnership covers joint
engineering on the Lumen query language.

Pieter Vos leads the platform team at Northwind Analytics. The platform team operates the
Aurora Engine clusters and the ingestion pipeline.

Northwind Analytics also maintains an internal risk model called Tideline. Tideline consumes
features produced by Aurora Engine.""",
    "fjord-protocol": """The Fjord Protocol is a replication protocol for distributed graph stores.
The Fjord Protocol was developed by Helios Labs and published as an open specification.

The Fjord Protocol replaced the Sable Protocol. The Sable Protocol was deprecated in 2022
after a series of consistency incidents.

The Fjord Protocol uses a quorum commit design. The quorum commit design trades write
latency for cross-region consistency.

Kestrel Systems adopted the Fjord Protocol for its edge deployments. Kestrel Systems
contributed the compaction extension to the Fjord Protocol specification.

The Fjord Protocol specification is maintained by the systems group at Helios Labs.""",
    "kestrel-systems": """Kestrel Systems builds edge computing hardware in Porto.
Kestrel Systems was founded by Ines Duarte in 2017.

Kestrel Systems adopted the Fjord Protocol in 2023 for synchronising its edge fleet.
Kestrel Systems contributed the compaction extension back to the specification.

Kestrel Systems partnered with Helios Labs on the Meridian project. The Meridian project
develops low-latency replication for edge devices.

Ines Duarte is the chief executive of Kestrel Systems. Ines Duarte previously worked at the
Trondheim Institute.

Kestrel Systems ships the Harrier gateway, an edge appliance used in industrial sites.""",
}

# Later versions of base documents.
UPDATED_DOCS: dict[str, str] = {
    # LOCAL EDIT: only the fourth paragraph changes. Every other chunk keeps its
    # content-addressed id, so it is neither re-embedded nor re-extracted.
    "northwind": """Northwind Analytics is a data company headquartered in Rotterdam.
Northwind Analytics was founded by Pieter Vos in 2015.

Northwind Analytics uses Aurora Engine for fraud detection across European payment networks.
Northwind Analytics processes several million transactions per day.

Northwind Analytics partnered with Helios Labs in 2022. The partnership covers joint
engineering on the Lumen query language.

Sofia Almeida leads the platform team at Northwind Analytics. Pieter Vos moved to the board
of Northwind Analytics in 2024.

Northwind Analytics also maintains an internal risk model called Tideline. Tideline consumes
features produced by Aurora Engine.""",
    # REWRITE: almost nothing survives, so this update costs about what a fresh add costs.
    # That is the honest half of the cost story.
    "kestrel-systems": """Kestrel Systems is an edge computing company that relocated from Porto to Lisbon in 2024.
Ines Duarte remains the chief executive of Kestrel Systems.

In 2024 Kestrel Systems replaced the Fjord Protocol with the Tundra Protocol across its edge
fleet. The migration took nine months.

The Tundra Protocol was developed by Kestrel Systems together with Vantage Robotics.
The Tundra Protocol targets intermittently connected devices.

Vantage Robotics builds autonomous inspection drones in Bilbao. Vantage Robotics uses the
Tundra Protocol for fleet synchronisation.

Kestrel Systems discontinued the Harrier gateway in 2024 and replaced it with the Kite
appliance.""",
}

NEW_DOCS: dict[str, str] = {
    "meridian-project": """The Meridian project is a joint effort between Helios Labs and Kestrel Systems.
The Meridian project develops low-latency replication for edge devices.

Marit Solberg sponsors the Meridian project on behalf of Helios Labs. Ines Duarte co-leads
the Meridian project on behalf of Kestrel Systems.

The Meridian project builds on the Fjord Protocol. The Meridian project evaluates the Tundra
Protocol for intermittently connected fleets.

The Meridian project published its first technical report in 2024. The report describes a
hybrid commit scheme for edge replication.""",
    "vantage-robotics": """Vantage Robotics builds autonomous inspection drones in Bilbao.
Vantage Robotics was founded by Nuria Castell in 2020.

Vantage Robotics uses the Tundra Protocol for fleet synchronisation. Vantage Robotics
operates several hundred drones across industrial sites.

Vantage Robotics partnered with Kestrel Systems in 2024. The partnership produced the Tundra
Protocol specification.

Nuria Castell previously worked at Northwind Analytics. Nuria Castell led the drone telemetry
programme before founding Vantage Robotics.""",
}

# Documents whose facts are directly queried, so deleting them must change the answers.
DELETION_SET: tuple[str, ...] = ("kestrel-systems", "northwind")


@dataclass
class QuestionSet:
    base: list[Question] = field(default_factory=list)
    new: list[Question] = field(default_factory=list)
    deletion: list[Question] = field(default_factory=list)


QUESTIONS = QuestionSet(
    base=[
        Question(
            question="Who founded Helios Labs?",
            answer="Marit Solberg founded Helios Labs in 2019.",
            must_include=("Marit Solberg",),
            supported_by=("helios-overview",),
        ),
        Question(
            question="Which protocol does Aurora Engine use for replication between clusters?",
            answer="Aurora Engine uses the Fjord Protocol for replication between clusters.",
            must_include=("Fjord Protocol",),
            supported_by=("aurora-engine",),
        ),
        Question(
            question="Which protocol did the Fjord Protocol replace?",
            answer="The Fjord Protocol replaced the Sable Protocol.",
            must_include=("Sable Protocol",),
            supported_by=("aurora-engine", "fjord-protocol"),
        ),
        Question(
            question="Where is Northwind Analytics headquartered?",
            answer="Northwind Analytics is headquartered in Rotterdam.",
            must_include=("Rotterdam",),
            supported_by=("aurora-engine", "northwind"),
        ),
        Question(
            question="Which company develops Aurora Engine?",
            answer="Helios Labs develops Aurora Engine.",
            must_include=("Helios Labs",),
            supported_by=("helios-overview", "aurora-engine"),
        ),
        Question(
            question="What is the name of the query language exposed by Aurora Engine?",
            answer="Aurora Engine exposes a query language called Lumen.",
            must_include=("Lumen",),
            supported_by=("aurora-engine", "northwind"),
        ),
    ],
    new=[
        Question(
            question="Who leads the platform team at Northwind Analytics?",
            answer="Sofia Almeida leads the platform team at Northwind Analytics.",
            must_include=("Sofia Almeida",),
            supported_by=("northwind",),
            kind="new",
        ),
        Question(
            question="Which protocol did Kestrel Systems adopt across its edge fleet in 2024?",
            answer="Kestrel Systems replaced the Fjord Protocol with the Tundra Protocol.",
            must_include=("Tundra Protocol",),
            supported_by=("kestrel-systems",),
            kind="new",
        ),
        Question(
            question="Who co-leads the Meridian project?",
            answer="Ines Duarte co-leads the Meridian project.",
            must_include=("Ines Duarte",),
            supported_by=("meridian-project",),
            kind="new",
        ),
        Question(
            question="Which company builds autonomous inspection drones in Bilbao?",
            answer="Vantage Robotics builds autonomous inspection drones in Bilbao.",
            must_include=("Vantage Robotics",),
            supported_by=("vantage-robotics",),
            kind="new",
        ),
        Question(
            question="Who founded Vantage Robotics?",
            answer="Nuria Castell founded Vantage Robotics in 2020.",
            must_include=("Nuria Castell",),
            supported_by=("vantage-robotics",),
            kind="new",
        ),
    ],
    # Asked AFTER the deletion set is removed. Every `must_exclude` string below is
    # supported ONLY by a deleted document - checked by test_benchmark_facts_are_unique.
    # "Ines Duarte" would have been the obvious choice for the Kestrel question and is
    # wrong: the surviving meridian-project document also names her, so an index that
    # correctly retracted the founding fact would still be scored as stale.
    deletion=[
        Question(
            question="Which city did Kestrel Systems relocate to?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Lisbon",),
            supported_by=("kestrel-systems",),
            kind="deletion",
        ),
        Question(
            question="Who founded Northwind Analytics?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Pieter Vos",),
            supported_by=("northwind",),
            kind="deletion",
        ),
        Question(
            question="Which internal risk model does Northwind Analytics maintain?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Tideline",),
            supported_by=("northwind",),
            kind="deletion",
        ),
        Question(
            question="Which edge appliance did Kestrel Systems discontinue in 2024?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Harrier",),
            supported_by=("kestrel-systems",),
            kind="deletion",
        ),
        Question(
            question="Who leads the platform team at Northwind Analytics?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Sofia Almeida",),
            supported_by=("northwind",),
            kind="deletion",
        ),
        Question(
            question="Which appliance replaced the Harrier gateway at Kestrel Systems?",
            answer="The indexed sources do not contain this information.",
            must_exclude=("Kite",),
            supported_by=("kestrel-systems",),
            kind="deletion",
        ),
    ],
)

# Facts that survive the deletion because a second document also supports them. Deleting
# too much is exactly as wrong as deleting too little, so the deletion scenario checks both.
SURVIVING_FACTS: tuple[Question, ...] = (
    Question(
        question="Which protocol does Aurora Engine use for replication between clusters?",
        answer="Aurora Engine uses the Fjord Protocol.",
        must_include=("Fjord Protocol",),
        supported_by=("aurora-engine",),
        kind="survivor",
    ),
    Question(
        question="Who founded Helios Labs?",
        answer="Marit Solberg founded Helios Labs.",
        must_include=("Marit Solberg",),
        supported_by=("helios-overview",),
        kind="survivor",
    ),
    Question(
        question="Which protocol did the Fjord Protocol replace?",
        answer="The Fjord Protocol replaced the Sable Protocol.",
        must_include=("Sable Protocol",),
        supported_by=("aurora-engine", "fjord-protocol"),
        kind="survivor",
    ),
)


def all_documents() -> dict[str, str]:
    return {**BASE_DOCS, **NEW_DOCS}
