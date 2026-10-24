"""Image analysis as a LangGraph state graph.

    ┌─▶ describe ─────────┐
    START                  ├─▶ tag ─▶ END
    └─▶ check_provenance ─┘

The vision model's caption and tags and the embedded-provenance check do not
depend on each other, so they run as parallel branches; tagging then adds the
"ai modified" and "ai info" system tags to the model's tags.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.models import ImageAnalysis, MetadataRules
from app.integrations.vision import OllamaVisionAnalyzer
from app.services.inspection import ImageInspector


class ImageAnalysisState(TypedDict, total=False):
    """One image's bytes and what each branch learned about them."""

    data: bytes
    modified: bool
    description: ImageAnalysis
    ai_generated: bool
    analysis: ImageAnalysis


class ImageAnalysisGraph:
    """Describe an image and check its provenance in parallel, then merge the tags."""

    AI_MODIFIED_TAG = "ai modified"
    # Marks images whose embedded provenance (C2PA, XMP, generator fields) says AI made them.
    AI_INFO_TAG = "ai info"

    def __init__(
        self, analyzer: OllamaVisionAnalyzer, inspector: ImageInspector, rules: MetadataRules
    ) -> None:
        self.analyzer = analyzer
        self.inspector = inspector
        self.rules = rules
        graph = StateGraph(ImageAnalysisState)
        graph.add_node("describe", self.describe)
        graph.add_node("check_provenance", self.check_provenance)
        graph.add_node("tag", self.tag)
        graph.add_edge(START, "describe")
        graph.add_edge(START, "check_provenance")
        graph.add_edge(["describe", "check_provenance"], "tag")
        graph.add_edge("tag", END)
        self.graph = graph.compile(name="visia-image-analysis")

    def run(self, data: bytes, modified: bool = False) -> ImageAnalysis:
        """Analyze bytes, tagging replacements and AI-generated images."""
        return self.graph.invoke({"data": data, "modified": modified})["analysis"]

    def describe(self, state: ImageAnalysisState) -> ImageAnalysisState:
        return {"description": self.analyzer.analyze(state["data"])}

    def check_provenance(self, state: ImageAnalysisState) -> ImageAnalysisState:
        return {"ai_generated": self.inspector.is_ai_generated(state["data"])}

    def tag(self, state: ImageAnalysisState) -> ImageAnalysisState:
        description = state["description"]
        extra = [self.AI_MODIFIED_TAG] if state["modified"] else []
        if state["ai_generated"]:
            extra.append(self.AI_INFO_TAG)
        if not extra:
            return {"analysis": description}
        return {"analysis": description.model_copy(update={"tags": self.rules.with_tags(description.tags, extra)})}
