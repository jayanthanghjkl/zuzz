"""Multi-Agent Specialist Agents — 10 specialist roles.

Each agent wraps the Cerebrum and implements a single ``execute`` method
for its specialty. The orchestrator composes teams from these agents.

Available agents:
- ResearcherAgent — Web research & information retrieval
- WriterAgent — Content drafting & prose creation
- AnalystAgent — Data analysis & evaluation
- PlannerAgent — Strategic roadmapping & task planning
- EditorAgent — Text refinement, proofreading & grammar
- TranslatorAgent — Language translation
- SummarizerAgent — Document & conversation summarization
- FactCheckerAgent — Fact checking & claim validation
- BrainstormerAgent — Idea generation & ideation
- SchedulerAgent — Timeline planning & calendar scheduling
"""

from multi_agent.agents.researcher import ResearcherAgent
from multi_agent.agents.writer import WriterAgent
from multi_agent.agents.analyst import AnalystAgent
from multi_agent.agents.planner import PlannerAgent
from multi_agent.agents.editor import EditorAgent
from multi_agent.agents.translator import TranslatorAgent
from multi_agent.agents.summarizer import SummarizerAgent
from multi_agent.agents.fact_checker import FactCheckerAgent
from multi_agent.agents.brainstormer import BrainstormerAgent
from multi_agent.agents.scheduler import SchedulerAgent

__all__ = [
    "ResearcherAgent",
    "WriterAgent",
    "AnalystAgent",
    "PlannerAgent",
    "EditorAgent",
    "TranslatorAgent",
    "SummarizerAgent",
    "FactCheckerAgent",
    "BrainstormerAgent",
    "SchedulerAgent",
]