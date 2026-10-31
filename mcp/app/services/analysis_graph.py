"""Image analysis as a LangGraph state graph.

    ┌─▶ describe ─────────┐
    START ─▶ check_provenance ─┼─▶ tag ─▶ END
    └─▶ embed ────────────┘

The vision model's caption and tags, the embedded-provenance check, and the
SigLIP embedding (on the GPU) do not depend on each other, so they run as
parallel branches; tagging then adds the "ai modified" and "ai info" system
tags to the model's tags and attaches the embedding.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.models import ImageAnalysis, MetadataRules
from app.integrations.embeddings import EmbeddingUnavailableError, SiglipEmbedder
from app.integrations.vision import OllamaVisionAnalyzer
from app.services.inspection import ImageInspector

logger = logging.getLogger(__name__)


class ImageAnalysisState(TypedDict, total=False):
    """One image's bytes and what each branch learned about them."""

    data: bytes
    modified: bool
    description: ImageAnalysis
    ai_generated: bool
    embedding: list[float] | None
    analysis: ImageAnalysis


class ImageAnalysisGraph:
    """Describe, check provenance, and embed an image in parallel, then merge the results."""

    AI_MODIFIED_TAG = "ai modified"
    # Marks images whose embedded provenance (C2PA, XMP, generator fields) says AI made them.
    AI_INFO_TAG = "ai info"

    def __init__(
        self,
        analyzer: OllamaVisionAnalyzer,
        inspector: ImageInspector,
        rules: MetadataRules,
        embedder: SiglipEmbedder | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.inspector = inspector
        self.rules = rules
        self.embedder = embedder
        graph = StateGraph(ImageAnalysisState)
        graph.add_node("describe", self.describe)
        graph.add_node("check_provenance", self.check_provenance)
        graph.add_node("embed", self.embed)
        graph.add_node("tag", self.tag)
        graph.add_edge(START, "describe")
        graph.add_edge(START, "check_provenance")
        graph.add_edge(START, "embed")
        graph.add_edge(["describe", "check_provenance", "embed"], "tag")
        graph.add_edge("tag", END)
        self.graph = graph.compile(name="visia-image-analysis")

    def run(self, data: bytes, modified: bool = False) -> ImageAnalysis:
        """Analyze bytes, tagging replacements and AI-generated images."""
        return self.graph.invoke({"data": data, "modified": modified})["analysis"]

    def describe(self, state: ImageAnalysisState) -> ImageAnalysisState:
        return {"description": self.analyzer.analyze(state["data"])}

    def check_provenance(self, state: ImageAnalysisState) -> ImageAnalysisState:
        return {"ai_generated": self.inspector.is_ai_generated(state["data"])}

    def embed(self, state: ImageAnalysisState) -> ImageAnalysisState:
        """The image's SigLIP embedding; None (never an error) when the GPU cannot make one."""
        if self.embedder is None:
            return {"embedding": None}
        try:
            return {"embedding": self.embedder.embed_image(state["data"])}
        except EmbeddingUnavailableError as error:
            logger.warning("Stored without an embedding: %s", error)
            return {"embedding": None}

    def tag(self, state: ImageAnalysisState) -> ImageAnalysisState:
        description = state["description"]
        extra = [self.AI_MODIFIED_TAG] if state["modified"] else []
        if state["ai_generated"]:
            extra.append(self.AI_INFO_TAG)
        update = {"embedding": state.get("embedding")}
        if extra:
            update["tags"] = self.rules.with_tags(description.tags, extra)
        return {"analysis": description.model_copy(update=update)}
