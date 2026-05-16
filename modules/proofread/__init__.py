"""Proofread module (standalone integration stage)."""

from core.workspace_manager import WorkspaceManager

from .engine import ProofreadEngine
from .passes import (
    AbilityConsistencyPass,
    AppearancePass,
    AttributeValuesPass,
    BehaviorLogicPass,
    CausalityPass,
    CharacterCountPass,
    CharacterKnowledgePass,
    ContinuityPass,
    DialogueAttributionPass,
    ExtraCharactersPass,
    LanguageStylePass,
    LightingPass,
    LocationReachabilityPass,
    MissingEventsPass,
    NarrativeTonePass,
    PovConsistencyPass,
    PropContinuityPass,
    PunctuationPass,
    QuantityPass,
    RepetitionPass,
    RelationshipsPass,
    SensoryBalancePass,
    SentenceVarietyPass,
    TimelinePass,
    WeatherEnvPass,
)


def create_default_engine(workspace: WorkspaceManager, llm_client, config: dict | None = None) -> ProofreadEngine:
    return ProofreadEngine.from_llm_client(
        workspace=workspace,
        llm_client=llm_client,
        config=config,
        pass_instances=[
            LanguageStylePass(),
            RepetitionPass(),
            SensoryBalancePass(),
            SentenceVarietyPass(),
            PunctuationPass(),
            LocationReachabilityPass(),
            AttributeValuesPass(),
            DialogueAttributionPass(),
            ContinuityPass(),
            AppearancePass(),
            RelationshipsPass(),
            ExtraCharactersPass(),
            TimelinePass(),
            WeatherEnvPass(),
            LightingPass(),
            PropContinuityPass(),
            CharacterKnowledgePass(),
            AbilityConsistencyPass(),
            BehaviorLogicPass(),
            PovConsistencyPass(),
            NarrativeTonePass(),
            MissingEventsPass(),
            CausalityPass(),
            QuantityPass(),
            CharacterCountPass(),
        ],
    )


__all__ = ["ProofreadEngine", "create_default_engine"]
