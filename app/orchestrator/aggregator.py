# Result Aggregator
# Combines results from multiple agents

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import time
import structlog

from app.orchestrator.schemas import AggregatedResult

log = structlog.get_logger(__name__)


class AggregationStrategy(str, Enum):
    """
    Available aggregation strategies for combining agent results.
    """
    HIERARCHICAL = "hierarchical"
    WEIGHTED_AVERAGE = "weighted_average"
    MAJORITY_VOTE = "majority_vote"
    ENSEMBLE = "ensemble"


class ResultAggregator:
    """
    Aggregates results from multiple agents with multiple strategies.
    
    Aggregation Strategies:
    - hierarchical: Primary agent's result wins
    - weighted_average: Weighted combination by confidence
    - majority_vote: Most common result
    - ensemble: Combine all successful results
    """
    
    def __init__(self):
        self.strategies = {
            "hierarchical": self._hierarchical,
            "weighted_average": self._weighted_average,
            "majority_vote": self._majority_vote,
            "ensemble": self._ensemble,
        }
    
    def aggregate(
        self,
        results: Dict[str, Any],
        strategy: str = "hierarchical"
    ) -> AggregatedResult:
        """
        Aggregate results from multiple agents.
        
        Args:
            results: Dict mapping task_id to result
            strategy: Aggregation strategy to use
            
        Returns:
            AggregatedResult with combined insights
        """
        start_time = time.perf_counter()
        
        # Get aggregation function
        agg_func = self.strategies.get(strategy, self._hierarchical)
        
        # Aggregate insights
        if strategy in self.strategies:
            aggregated_insights = agg_func(results)
        else:
            log.warning(
                "aggregator.unknown_strategy",
                strategy=strategy,
                using="hierarchical"
            )
            aggregated_insights = self._hierarchical(results)
        
        # Collect warnings and errors
        warnings, errors = self._collect_warnings_errors(results)
        
        # Calculate overall confidence
        confidence_score = self._calculate_confidence(results)
        
        execution_time_ms = int((time.perf_counter() - start_time) * 1000)
        
        return AggregatedResult(
            primary_results=results,
            aggregated_insights=aggregated_insights,
            confidence_score=confidence_score,
            execution_time_ms=execution_time_ms,
            warnings=warnings,
            errors=errors
        )
    
    def _hierarchical(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Primary agent's result wins.
        
        First agent (by order) with successful execution provides the result.
        """
        for task_id, result in results.items():
            if isinstance(result, dict) and result.get("status") == "success":
                return {
                    "strategy": "hierarchical",
                    "primary_agent": task_id,
                    "primary_result": result.get("result", {}),
                    "note": f"Primary result from {task_id}"
                }
        
        return {
            "strategy": "hierarchical",
            "error": "All agents failed",
            "failed_count": len(results)
        }
    
    def _weighted_average(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Combine results weighted by agent confidence.
        
        Useful when agents have varying reliability.
        """
        weighted_sum = 0.0
        total_weight = 0.0
        scores: List[float] = []
        confidences: List[float] = []
        
        for task_id, result in results.items():
            if isinstance(result, dict) and result.get("status") == "success":
                res = result.get("result", {})
                
                # Extract score (common pattern)
                score = res.get("score") or res.get("risk_level") or 0.5
                confidence = res.get("confidence", 0.5)
                
                if isinstance(score, (int, float)):
                    weighted_sum += score * confidence
                    total_weight += confidence
                    scores.append(score)
                    confidences.append(confidence)
        
        if total_weight > 0:
            weighted_avg = weighted_sum / total_weight
        else:
            weighted_avg = 0.0
        
        return {
            "strategy": "weighted_average",
            "weighted_score": round(weighted_avg, 4),
            "individual_scores": list(zip([str(k) for k in results.keys()], scores, confidences)),
            "max_score": max(scores) if scores else None,
            "min_score": min(scores) if scores else None
        }
    
    def _majority_vote(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use the most common result.
        
        Useful for categorical outcomes.
        """
        votes: Dict[str, int] = {}
        total_votes = 0
        
        for task_id, result in results.items():
            if isinstance(result, dict) and result.get("status") == "success":
                res = result.get("result", {})
                
                # Get the vote key (could be risk_level, category, etc.)
                vote_key = res.get("risk_level") or res.get("category") or str(res)
                
                votes[vote_key] = votes.get(vote_key, 0) + 1
                total_votes += 1
        
        if total_votes == 0:
            return {
                "strategy": "majority_vote",
                "error": "No successful results to vote on"
            }
        
        # Find majority
        majority = max(votes.items(), key=lambda x: x[1])
        
        return {
            "strategy": "majority_vote",
            "winner": majority[0],
            "winner_votes": majority[1],
            "total_votes": total_votes,
            "distribution": votes,
            "consensus": majority[1] / total_votes
        }
    
    def _ensemble(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Combine all successful results into an ensemble.
        
        Preserves all information from successful agents.
        """
        successful_results = []
        failed_tasks = []
        
        for task_id, result in results.items():
            if isinstance(result, dict) and result.get("status") == "success":
                successful_results.append({
                    "task_id": task_id,
                    "result": result.get("result", {}),
                    "confidence": result.get("result", {}).get("confidence", 0.5)
                })
            else:
                failed_tasks.append({
                    "task_id": task_id,
                    "error": result.get("error", "Unknown error")
                })
        
        # Extract key metrics if available
        key_metrics = {}
        for sr in successful_results:
            r = sr["result"]
            # Common metrics across agents
            for metric in ["risk_level", "score", "velocity", "confidence"]:
                if metric in r:
                    if metric not in key_metrics:
                        key_metrics[metric] = []
                    key_metrics[metric].append({
                        "task_id": sr["task_id"],
                        "value": r[metric]
                    })
        
        return {
            "strategy": "ensemble",
            "successful_count": len(successful_results),
            "failed_count": len(failed_tasks),
            "results": successful_results,
            "failed_tasks": failed_tasks,
            "key_metrics": key_metrics,
            "ensemble_size": len(successful_results)
        }
    
    def _collect_warnings_errors(
        self,
        results: Dict[str, Any]
    ) -> tuple[List[str], List[str]]:
        """
        Collect all warnings and errors from results.
        
        Returns:
            Tuple of (warnings list, errors list)
        """
        warnings = []
        errors = []
        
        for task_id, result in results.items():
            if isinstance(result, dict):
                status = result.get("status", "unknown")
                
                if status == "error":
                    error_msg = result.get("error", "Unknown error")
                    errors.append(f"{task_id}: {error_msg}")
                elif status == "timeout":
                    errors.append(f"{task_id}: Task timed out")
                elif "warning" in result:
                    warnings.append(f"{task_id}: {result['warning']}")
        
        return warnings, errors
    
    def _calculate_confidence(self, results: Dict[str, Any]) -> float:
        """
        Calculate overall confidence from agent results.
        
        Returns:
            Average confidence score from successful agents
        """
        confidences = []
        
        for result in results.values():
            if isinstance(result, dict) and result.get("status") == "success":
                conf = result.get("result", {}).get("confidence")
                if conf is not None:
                    confidences.append(conf)
        
        if not confidences:
            return 0.0
        
        return round(sum(confidences) / len(confidences), 4)
