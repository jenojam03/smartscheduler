from typing import List, Dict, Any, Optional
from ortools.sat.python import cp_model
from models import SHIFT_MAP
from calendar_manager import SchedulingHorizon
from worker_agent import FormalizedWorkerProfile
from drafting_agent import ScheduleDraftingAgent
from verification_agent import HardConstraintVerificationAgent, SymbolicFairnessVerificationAgent
from config_loader import SchedulingConfig



class ScheduleRefinementAgent:
    """
    Stage 4: Schedule Refinement.
    Implementa:
    1. Satisfaction Ratio Normalizzato per identificare i veri lavoratori svantaggiati.
    2. Bilanciamento Equo dei Carichi Gravosi (Notti e Festivi).
    3. Minimizzazione del Divario (Max Sat - Min Sat) per abbassare l'indice di Gini.
    Tutti i parametri di scheduling sono letti dal SchedulingConfig.
    """

    def __init__(
        self,
        horizon: SchedulingHorizon,
        worker_profiles: List[FormalizedWorkerProfile],
        config: SchedulingConfig,
    ):
        self.horizon = horizon
        self.worker_profiles = worker_profiles
        self.config = config
        self.num_workers = len(worker_profiles)

        self.drafting_agent = ScheduleDraftingAgent(
            horizon=horizon,
            worker_profiles=worker_profiles,
            config=config,
        )
        self.hard_verifier = HardConstraintVerificationAgent(
            horizon=horizon,
            worker_profiles=worker_profiles,
            config=config,
        )
        self.fairness_verifier = SymbolicFairnessVerificationAgent(horizon, worker_profiles)

    # ------------------------------------------------------------------
    # Satisfaction Ratio Normalizzato
    # ------------------------------------------------------------------
    def calculate_satisfaction_ratios(self, scores: List[int]) -> List[float]:
        """
        Calcola il ratio normalizzato (0.0 -> 1.0) evitando che chi ha score 0
        per assenza di preferenze venga falsamente etichettato come svantaggiato.
        """
        ratios = []
        for idx, profile in enumerate(self.worker_profiles):
            has_preferences = (
                len(profile.raw_preference.preferred_shifts) > 0 or
                len(profile.raw_preference.availability.preferred_rest_days) > 0 or
                len(profile.raw_preference.shift_tolerance.disliked_shift_types) > 0 or
                profile.raw_preference.shift_tolerance.max_tolerated_holidays == 0
            )

            if not has_preferences:
                ratios.append(1.0)
            else:
                raw_score   = scores[idx]
                max_possible = (
                    len(profile.raw_preference.preferred_shifts) * 250
                    + len(profile.raw_preference.availability.preferred_rest_days) * 15
                )
                min_possible = -(
                    len(profile.raw_preference.shift_tolerance.disliked_shift_types) * 250
                    + (300 if profile.raw_preference.shift_tolerance.max_tolerated_holidays == 0 else 0)
                )
                denom     = max_possible - min_possible if max_possible != min_possible else 1
                norm_ratio = (raw_score - min_possible) / denom
                ratios.append(round(max(0.0, min(1.0, norm_ratio)), 4))
        return ratios

    # ------------------------------------------------------------------
    # Refinement Step
    # ------------------------------------------------------------------
    def solve_refined_step(
        self,
        locked_min_satisfaction: int,
        target_worker_idx: Optional[int] = None,
        max_night_gap: int = 2,
        max_holiday_gap: int = 3,
        time_limit_seconds: int = 30
    ) -> Dict[str, Any]:
        model, shifts, worker_satisfactions, min_sat_var = self.drafting_agent.build_ortools_model()
        num_days  = self.horizon.total_days
        night_idx = SHIFT_MAP["night"]
        cfg       = self.config

        # Non peggioramento: nessun lavoratore può scendere sotto la soglia minima
        # Il miglioramento della soddisfazione del worker più svantaggiato non deve abbassare
        # la soglia minima di soddisfazione già esistente
        for sat_var in worker_satisfactions:
            model.add(sat_var >= locked_min_satisfaction)

        # 1. Bilanciamento equo dei turni di notte (carico faticoso 1)
        night_counts = []
        for w in range(self.num_workers):
            n_nights = model.new_int_var(0, num_days, f"nights_w{w}")
            model.add(n_nights == sum(shifts[(w, d, night_idx)] for d in range(num_days)))
            night_counts.append(n_nights)

        max_nights = model.new_int_var(0, num_days, "max_nights")
        min_nights = model.new_int_var(0, num_days, "min_nights")
        model.add_max_equality(max_nights, night_counts)
        model.add_min_equality(min_nights, night_counts)
        model.add(max_nights - min_nights <= max_night_gap)

        # 2. Bilanciamento equo dei turni festivi e weekend (carico faticoso 2)
        holiday_counts = []
        for w in range(self.num_workers):
            h_shifts = model.new_int_var(
                0, len(self.horizon.holiday_indices) * cfg.num_shifts, f"holidays_w{w}"
            )
            model.add(h_shifts == sum(
                shifts[(w, d, s)]
                for d in self.horizon.holiday_indices
                for s in range(cfg.num_shifts)
            ))
            holiday_counts.append(h_shifts)

        max_holidays = model.new_int_var(
            0, len(self.horizon.holiday_indices) * cfg.num_shifts, "max_holidays"
        )
        min_holidays = model.new_int_var(
            0, len(self.horizon.holiday_indices) * cfg.num_shifts, "min_holidays"
        )
        model.add_max_equality(max_holidays, holiday_counts)
        model.add_min_equality(min_holidays, holiday_counts)
        model.add(max_holidays - min_holidays <= max_holiday_gap)

        # Riduzione della disparita' di soddisfazione
        max_sat = model.new_int_var(-10000, 10000, "max_sat")
        min_sat = model.new_int_var(-10000, 10000, "min_sat")
        model.add_max_equality(max_sat, worker_satisfactions)
        model.add_min_equality(min_sat, worker_satisfactions)

        total_sat = sum(worker_satisfactions)

        # Nuova funzione obiettivo definita su priorità:
        # 1. Massimizza la soddisfazione del lavoratore svantaggiato e penalizza la presenza di suoi turni sgraditi (riassegnazione)
        # 2. Mantiene alto il punteggio minimo generale
        # 3. Riduzione del divario tra lavoratore più soddisfatto e meno soddisfatto
        # 4. Aumento della soddisfazione totale
        if target_worker_idx is not None:
            target_sat = worker_satisfactions[target_worker_idx]
            
            # Scarica i turni sgraditi dal lavoratore svantaggiato spingendone la riassegnazione ad altri
            target_profile = self.worker_profiles[target_worker_idx]
            disliked_types = target_profile.raw_preference.shift_tolerance.disliked_shift_types
            if disliked_types:
                disliked_indices = [SHIFT_MAP[st] for st in disliked_types]
                target_disliked_assigned = sum(
                    shifts[(target_worker_idx, d, s_idx)]
                    for d in range(num_days)
                    for s_idx in disliked_indices
                )
                model.maximize(target_sat * 3000 - target_disliked_assigned * 1500 + min_sat * 1000 - (max_sat - min_sat) * 100 + total_sat)
            else:
                model.maximize(target_sat * 3000 + min_sat * 1000 - (max_sat - min_sat) * 100 + total_sat)
        else:
            model.maximize(min_sat * 2000 - (max_sat - min_sat) * 100 + total_sat)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = 8

        status = solver.solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            schedule_matrix = []
            for w in range(self.num_workers):
                worker_schedule = []
                for d in range(num_days):
                    assigned_shift = None
                    for s in range(cfg.num_shifts):
                        if solver.value(shifts[(w, d, s)]) == 1:
                            assigned_shift = s
                            break
                    worker_schedule.append(assigned_shift)
                schedule_matrix.append(worker_schedule)

            scores = [solver.value(sat) for sat in worker_satisfactions]
            return {
                "status": "FEASIBLE" if status == cp_model.FEASIBLE else "OPTIMAL",
                "schedule_matrix": schedule_matrix,
                "satisfaction_scores": scores,
                "min_satisfaction": min(scores),
                "total_satisfaction": sum(scores)
            }
        return {"status": "INFEASIBLE"}

    # ------------------------------------------------------------------
    # Iterative Refinement Loop
    # ------------------------------------------------------------------
    def run_refinement_loop(
        self,
        initial_draft_result: Dict[str, Any],
        max_iterations: Optional[int] = None,
        step_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        current_schedule = initial_draft_result["schedule_matrix"]
        current_scores   = initial_draft_result["satisfaction_scores"]

        history   = []
        iteration = 0
        completed_iterations = 0
        seen_schedules = {tuple(tuple(row) for row in current_schedule)}

        while max_iterations is None or iteration < max_iterations:
            iteration += 1

            # 1. Verifica Hard Constraints
            hard_verif = self.hard_verifier.verify(current_schedule)
            if not hard_verif["is_valid"]:
                return {
                    "success": False,
                    "reason": "Hard constraints violate durante il loop.",
                    "violations": hard_verif["violations"],
                    "total_iterations": completed_iterations,
                    "completed_iterations": completed_iterations,
                    "attempted_iterations": iteration - 1,
                }

            # 2. Fairness & Satisfaction Ratios
            fairness_eval = self.fairness_verifier.evaluate(current_schedule, current_scores)
            ratios        = self.calculate_satisfaction_ratios(current_scores)
            min_ratio     = min(ratios)

            disadvantaged_indices = [i for i, r in enumerate(ratios) if r == min_ratio]
            target_idx = disadvantaged_indices[0]
            target_worker_id = self.worker_profiles[target_idx].worker_id

            history.append({
                "iteration":        iteration,
                "min_satisfaction": fairness_eval["min_satisfaction"],
                "min_ratio":        min_ratio,
                "mean_satisfaction":fairness_eval["mean_satisfaction"],
                "gini_index":       fairness_eval["gini_index"],
                "std_deviation":    fairness_eval["std_deviation"],
                "target_worker":    target_worker_id
            })

            # 3. Callback di raffinamento
            refine_result = self.solve_refined_step(
                locked_min_satisfaction=fairness_eval["min_satisfaction"],
                target_worker_idx=target_idx,
                max_night_gap=2,
                max_holiday_gap=3,
                time_limit_seconds=20
            )

            if refine_result["status"] not in ("FEASIBLE", "OPTIMAL"):
                if step_callback:
                    step_callback({
                        "iteration": iteration,
                        "status": "infeasible",
                        "improved": False,
                        "target_worker": target_worker_id,
                    })
                break

            refined_scores = refine_result["satisfaction_scores"]
            refined_ratios = self.calculate_satisfaction_ratios(refined_scores)
            refined_fairness = self.fairness_verifier.evaluate(
                refine_result["schedule_matrix"], refined_scores
            )

            curr_min_score = fairness_eval["min_satisfaction"]
            ref_min_score  = refined_fairness["min_satisfaction"]
            curr_min_ratio = min_ratio
            ref_min_ratio  = min(refined_ratios)
            curr_gini      = fairness_eval["gini_index"]
            ref_gini       = refined_fairness["gini_index"]
            curr_gap       = fairness_eval["max_satisfaction"] - fairness_eval["min_satisfaction"]
            ref_gap        = refined_fairness["max_satisfaction"] - refined_fairness["min_satisfaction"]

            # Conteggio dei lavoratori fermi al livello minimo di soddisfazione
            curr_disadvantaged_count = sum(1 for r in ratios if r <= curr_min_ratio + 1e-4)
            ref_disadvantaged_count  = sum(1 for r in refined_ratios if r <= curr_min_ratio + 1e-4)

            # Condizione necessaria: la soddisfazione minima globale non deve peggiorare
            min_not_worsened = (ref_min_score >= curr_min_score) and (ref_min_ratio >= curr_min_ratio - 1e-4)

            # Criteri di progresso reale della fairness complessiva:
            # 1. Rawlsian / Max-Min: innalzamento effettivo della soglia minima globale
            floor_improved = (ref_min_score > curr_min_score) or (ref_min_ratio > curr_min_ratio + 1e-4)

            # 2. Leximin: riduzione della platea dei lavoratori svantaggiati (senza che altri scendano al minimo)
            disadvantaged_reduced = (
                ref_disadvantaged_count < curr_disadvantaged_count and
                refined_ratios[target_idx] > ratios[target_idx]
            )

            # 3. Indice di Gini: diminuzione misurabile della disuguaglianza complessiva dell'organico
            gini_improved = (ref_gini < curr_gini - 0.001)

            # 4. Divario Max-Min: compressione della forbice complessiva a parità o aumento del totale
            gap_improved = (ref_gap < curr_gap) and (sum(refined_scores) >= sum(current_scores))

            improved = min_not_worsened and (
                floor_improved or
                disadvantaged_reduced or
                gini_improved or
                gap_improved
            )

            # Se non è possibile migliorare la fairness, stop
            if not improved:
                if step_callback:
                    step_callback({
                        "iteration": iteration,
                        "status": "not_improved",
                        "improved": False,
                        "target_worker": target_worker_id,
                    })
                break

            # Controllo anti-ciclo: se la nuova matrice è già stata visitata, stop
            schedule_key = tuple(tuple(row) for row in refine_result["schedule_matrix"])
            if schedule_key in seen_schedules:
                if step_callback:
                    step_callback({
                        "iteration": iteration,
                        "status": "cycle_detected",
                        "improved": False,
                        "target_worker": target_worker_id,
                    })
                break
            seen_schedules.add(schedule_key)

            current_schedule = refine_result["schedule_matrix"]
            current_scores   = refined_scores
            completed_iterations += 1

            if step_callback:
                step_callback({
                    "iteration": iteration,
                    "completed_iterations": completed_iterations,
                    "status": "improved",
                    "improved": True,
                    "target_worker": target_worker_id,
                    "min_satisfaction": min(refined_scores),
                    "refined_scores": refined_scores,
                })

        final_fairness = self.fairness_verifier.evaluate(current_schedule, current_scores)

        return {
            "success":              True,
            "total_iterations":     completed_iterations,
            "completed_iterations": completed_iterations,
            "attempted_iterations": iteration,
            "initial_min_sat":      history[0]["min_satisfaction"],
            "final_min_sat":        final_fairness["min_satisfaction"],
            "initial_gini":         history[0]["gini_index"],
            "final_gini":           final_fairness["gini_index"],
            "final_schedule":       current_schedule,
            "final_scores":         current_scores,
            "final_fairness_report":final_fairness,
            "loop_history":         history
        }