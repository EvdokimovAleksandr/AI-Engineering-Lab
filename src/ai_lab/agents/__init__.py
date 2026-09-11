"""Agent registry."""

from __future__ import annotations

from ai_lab.agents.base import BaseAgent
from ai_lab.agents.chief_engineer import ChiefEngineerAgent
from ai_lab.agents.red_team import RedTeamAgent
from ai_lab.agents.research import ResearchAgent
from ai_lab.agents.simulation import SimulationAgent
from ai_lab.agents.theorist import TheoristAgent
from ai_lab.agents.verification import VerificationAgent
from ai_lab.core.enums import AgentRole


def build_agents() -> dict[AgentRole, BaseAgent]:
    agents: list[BaseAgent] = [
        ChiefEngineerAgent(),
        ResearchAgent(),
        TheoristAgent(),
        SimulationAgent(),
        VerificationAgent(),
        RedTeamAgent(),
    ]
    return {a.role: a for a in agents}
