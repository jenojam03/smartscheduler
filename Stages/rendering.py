from typing import List, Optional, Dict, Any
from calendar_manager import SchedulingHorizon
import gui


def show_schedule_window(
    schedule_matrix: List[List[Optional[int]]],
    horizon: SchedulingHorizon,
    worker_profiles: list,
    satisfaction_scores: List[int],
    fairness_report: Optional[Dict[str, Any]] = None,
    refinement_res: Optional[Dict[str, Any]] = None,
    num_standard: int = 13,
    num_specialized: int = 0
):
    """
    Invocata dai file di test (test_use_case_a.py, test_use_case_b.py) per mostrare
    la GUI completa definita in gui.py al termine dell'esecuzione sul terminale.
    """
    if fairness_report is None:
        fairness_report = {
            "mean_satisfaction": round(sum(satisfaction_scores) / len(satisfaction_scores), 2),
            "std_deviation": 0.0,
            "workers_shift_distribution": [
                {
                    "worker_id": profile.worker_id,
                    "nights": sum(1 for d in range(horizon.total_days) if schedule_matrix[i][d] == 2),
                    "holidays": sum(1 for d in horizon.holiday_indices if schedule_matrix[i][d] is not None),
                    "score": satisfaction_scores[i]
                }
                for i, profile in enumerate(worker_profiles)
            ]
        }

    if refinement_res is None:
        refinement_res = {
            "total_iterations": 1,
            "initial_min_sat": min(satisfaction_scores),
            "final_min_sat": min(satisfaction_scores),
            "initial_gini": 0.0,
            "final_gini": 0.0,
        }

    gui.launch_gui_with_results(
        schedule_matrix=schedule_matrix,
        horizon=horizon,
        worker_profiles=worker_profiles,
        satisfaction_scores=satisfaction_scores,
        fairness_report=fairness_report,
        refinement_res=refinement_res,
        num_standard=num_standard,
        num_specialized=num_specialized
    )