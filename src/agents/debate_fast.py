"""
Optimized Debate Runner with parallel Bull/Bear analysis.

This version runs Steps 1 & 2 (Bull and Bear) in parallel to reduce
ensemble latency by ~30-40% (~45-60 seconds saved per market).

Trade-off: Bull and Bear don't see each other's arguments before
presenting their cases. This may reduce debate quality slightly.
"""

import asyncio
import time
from typing import Callable, Dict, Optional

from src.agents.base_agent import BaseAgent
from src.agents.bull_researcher import BullResearcher
from src.agents.bear_researcher import BearResearcher
from src.agents.risk_manager_agent import RiskManagerAgent
from src.agents.trader_agent import TraderAgent
from src.agents.forecaster_agent import ForecasterAgent
from src.agents.news_analyst_agent import NewsAnalystAgent
from src.utils.logging_setup import get_trading_logger

logger = get_trading_logger("debate_fast")


class FastDebateRunner:
    """
    Optimized debate runner with parallel Bull/Bear analysis.
    
    Reduces per-market analysis time from ~180s to ~120s by running
    independent steps in parallel.
    """

    def __init__(
        self,
        agents: Optional[Dict[str, BaseAgent]] = None,
    ):
        self.agents: Dict[str, BaseAgent] = agents or self._default_agents()

    def _default_agents(self) -> Dict[str, BaseAgent]:
        return {
            "forecaster": ForecasterAgent(),
            "news_analyst": NewsAnalystAgent(),
            "bull_researcher": BullResearcher(),
            "bear_researcher": BearResearcher(),
            "risk_manager": RiskManagerAgent(),
            "trader": TraderAgent(),
        }

    async def run_debate(
        self,
        market_data: dict,
        get_completions: Dict[str, Callable],
        context: Optional[dict] = None,
    ) -> dict:
        """
        Run optimized debate with parallel Bull/Bear steps.
        """
        start = time.time()
        context = dict(context or {})
        transcript_parts = []
        step_results = {}

        market_title = market_data.get("title", "Unknown")[:80]
        logger.info("Fast debate starting", market=market_title)

        # ==================================================================
        # Pre-step: Forecaster + News Analyst (parallel)
        # ==================================================================
        pre_results = await self._run_pre_analysis(
            market_data, context, get_completions
        )
        if pre_results.get("forecaster_result"):
            context["forecaster_result"] = pre_results["forecaster_result"]
            step_results["forecaster"] = pre_results["forecaster_result"]
            transcript_parts.append(
                self._format_step("PRE-ANALYSIS: Forecaster", pre_results["forecaster_result"])
            )
        if pre_results.get("news_result"):
            context["news_result"] = pre_results["news_result"]
            step_results["news_analyst"] = pre_results["news_result"]
            transcript_parts.append(
                self._format_step("PRE-ANALYSIS: News Analyst", pre_results["news_result"])
            )

        # ==================================================================
        # OPTIMIZATION: Run Bull + Bear in PARALLEL
        # ==================================================================
        logger.info("Steps 1-2: Running BULL and BEAR in parallel")
        
        bull_task = asyncio.create_task(
            self._run_step_safe("bull_researcher", market_data, context, get_completions)
        )
        bear_task = asyncio.create_task(
            self._run_step_safe("bear_researcher", market_data, context, get_completions)
        )
        
        # Wait for both to complete
        bull_result, bear_result = await asyncio.gather(bull_task, bear_task)
        
        # Process Bull result
        step_results["bull_researcher"] = bull_result
        context["bull_result"] = bull_result
        if "error" not in bull_result:
            logger.info("Step 1: BULL CASE completed")
            transcript_parts.append(self._format_step("STEP 1 -- BULL CASE", bull_result))
        else:
            logger.warning("Step 1: BULL CASE failed", error=bull_result.get("error"))

        # Process Bear result
        step_results["bear_researcher"] = bear_result
        context["bear_result"] = bear_result
        if "error" not in bear_result:
            logger.info("Step 2: BEAR CASE completed")
            transcript_parts.append(self._format_step("STEP 2 -- BEAR CASE", bear_result))
        else:
            logger.warning("Step 2: BEAR CASE failed", error=bear_result.get("error"))

        # ==================================================================
        # Step 3: Risk manager (sequential - needs both sides)
        # ==================================================================
        risk_result = await self._run_step_safe(
            "risk_manager", market_data, context, get_completions
        )
        step_results["risk_manager"] = risk_result
        context["risk_result"] = risk_result
        if "error" not in risk_result:
            logger.info("Step 3: RISK ASSESSMENT completed")
            transcript_parts.append(self._format_step("STEP 3 -- RISK ASSESSMENT", risk_result))
        else:
            logger.warning("Step 3: RISK ASSESSMENT failed", error=risk_result.get("error"))

        # ==================================================================
        # Step 4: Trader makes final call (sequential)
        # ==================================================================
        trader_result = await self._run_step_safe(
            "trader", market_data, context, get_completions
        )
        step_results["trader"] = trader_result
        if "error" not in trader_result:
            logger.info("Step 4: FINAL DECISION completed")
            transcript_parts.append(self._format_step("STEP 4 -- FINAL DECISION", trader_result))
        else:
            logger.warning("Step 4: FINAL DECISION failed", error=trader_result.get("error"))

        elapsed = time.time() - start
        transcript = "\n\n".join(transcript_parts)

        logger.info(
            "Fast debate complete",
            action=trader_result.get("action", "SKIP"),
            confidence=trader_result.get("confidence", 0.0),
            elapsed=round(elapsed, 2),
        )

        # Build final output
        if "error" in trader_result:
            return self._skip_decision(
                reasoning=f"Trader failed: {trader_result.get('error')}",
                transcript=transcript,
                step_results=step_results,
                elapsed=elapsed,
            )

        trader_reasoning = trader_result.get("reasoning", "")
        full_reasoning = f"{trader_reasoning}\n\n--- DEBATE TRANSCRIPT ---\n{transcript}"

        return {
            "action": trader_result.get("action", "SKIP"),
            "side": trader_result.get("side", "YES"),
            "limit_price": trader_result.get("limit_price", 50),
            "confidence": trader_result.get("confidence", 0.0),
            "position_size_pct": trader_result.get("position_size_pct", 0.0),
            "reasoning": full_reasoning,
            "debate_transcript": transcript,
            "step_results": step_results,
            "elapsed_seconds": round(elapsed, 2),
            "error": None,
        }

    async def _run_pre_analysis(
        self,
        market_data: dict,
        context: dict,
        get_completions: Dict[str, Callable],
    ) -> dict:
        """Run forecaster and news analyst in parallel."""
        results = {}
        tasks = {}

        if "forecaster" in self.agents and "forecaster" in get_completions:
            tasks["forecaster_result"] = asyncio.create_task(
                self._run_agent_safe("forecaster", market_data, context, get_completions["forecaster"])
            )
        if "news_analyst" in self.agents and "news_analyst" in get_completions:
            tasks["news_result"] = asyncio.create_task(
                self._run_agent_safe("news_analyst", market_data, context, get_completions["news_analyst"])
            )

        if tasks:
            done = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, res in zip(tasks.keys(), done):
                if isinstance(res, Exception):
                    logger.warning(f"Pre-analysis {key} failed", error=str(res))
                elif "error" not in res:
                    results[key] = res
                else:
                    logger.warning(f"Pre-analysis {key} returned error", error=res.get("error"))

        return results

    async def _run_step_safe(
        self,
        role: str,
        market_data: dict,
        context: dict,
        get_completions: Dict[str, Callable],
    ) -> dict:
        """Run a single step with error handling."""
        if role not in self.agents:
            return {"error": f"No agent for role '{role}'", "_agent": role}
        if role not in get_completions:
            return {"error": f"No completion callable for role '{role}'", "_agent": role}
        return await self._run_agent_safe(role, market_data, context, get_completions[role])

    async def _run_agent_safe(
        self,
        role: str,
        market_data: dict,
        context: dict,
        get_completion: Callable,
    ) -> dict:
        """Run a single agent with full error handling."""
        agent = self.agents.get(role)
        if agent is None:
            return {"error": f"No agent for role '{role}'", "_agent": role}
        try:
            return await agent.analyze(market_data, context, get_completion)
        except Exception as exc:
            logger.error("Agent failed", role=role, error=str(exc))
            return {"error": str(exc), "_agent": role}

    def _format_step(self, label: str, result: dict) -> str:
        """Format a debate step for the transcript."""
        reasoning = result.get("reasoning", "")
        return f"=== {label} ===\n{reasoning}"

    def _skip_decision(
        self,
        reasoning: str,
        transcript: str,
        step_results: dict,
        elapsed: float,
    ) -> dict:
        """Return a SKIP decision."""
        return {
            "action": "SKIP",
            "side": "YES",
            "limit_price": 50,
            "confidence": 0.0,
            "position_size_pct": 0.0,
            "reasoning": reasoning,
            "debate_transcript": transcript,
            "step_results": step_results,
            "elapsed_seconds": round(elapsed, 2),
            "error": None,
        }
