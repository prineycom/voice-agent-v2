"""Frozen outcome-based scoring for deterministic E4.1 research tasks.

The scorer intentionally does not prescribe wording, provider, or tool order.
It checks useful outcome concepts, refined queries, independently fetched
origins, citation coverage/traceability, and the release decision bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from .agent_run import AgentRunResult


@dataclass(frozen=True, slots=True)
class FrozenResearchTask:
    task_id: str
    language: str
    outcome_concepts: tuple[tuple[str, ...], ...]
    minimum_independent_sources: int = 2
    require_query_refinement: bool = True


@dataclass(frozen=True, slots=True)
class ResearchScore:
    passed: bool
    useful_outcome: bool
    query_refined: bool
    independent_sources: int
    citations_traceable: bool
    citation_coverage: bool
    bounded: bool

    def document(self) -> dict[str, object]:
        return {
            "passed": self.passed, "useful_outcome": self.useful_outcome,
            "query_refined": self.query_refined,
            "independent_sources": self.independent_sources,
            "citations_traceable": self.citations_traceable,
            "citation_coverage": self.citation_coverage, "bounded": self.bounded,
        }


def _contains_concept(text: str, alternatives: Iterable[str]) -> bool:
    folded = text.casefold()
    return any(value.casefold() in folded for value in alternatives)


def score_frozen_research(task: FrozenResearchTask, result: AgentRunResult) -> ResearchScore:
    receipts: dict[str, Mapping[str, object]] = {
        str(item.get("receipt_id")): item for item in result.research_receipts
    }
    searches = {
        str(item.get("query_sha256")) for item in result.research_receipts
        if item.get("kind") == "web_search" and item.get("outcome") == "completed"
        and isinstance(item.get("query_sha256"), str)
    }
    cited_origins = {
        (urlsplit(item.displayed_url).scheme.lower(), urlsplit(item.displayed_url).hostname)
        for item in result.citations
        if urlsplit(item.displayed_url).hostname
        and receipts.get(item.receipt_id, {}).get("kind") == "web_fetch"
    }
    useful = all(_contains_concept(result.answer, group) for group in task.outcome_concepts)
    traceable = bool(result.citations) and all(
        item.receipt_id in receipts
        and receipts[item.receipt_id].get("outcome") == "completed"
        and receipts[item.receipt_id].get("artifact_sha256") == item.sha256
        and receipts[item.receipt_id].get("display_url") == item.displayed_url
        for item in result.citations
    )
    coverage = all(
        any(
            _contains_concept(" ".join((*citation.claims, *citation.spans)), group)
            for citation in result.citations
        )
        for group in task.outcome_concepts
    )
    refined = len(searches) >= 2
    bounded = result.decisions <= 24 and result.operations < result.decisions
    passed = (
        useful and traceable and coverage and bounded
        and len(cited_origins) >= task.minimum_independent_sources
        and (refined or not task.require_query_refinement)
    )
    return ResearchScore(
        passed, useful, refined, len(cited_origins), traceable, coverage, bounded
    )
