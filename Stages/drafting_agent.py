from typing import List, Dict, Any, Optional
from ortools.sat.python import cp_model
from models import SHIFT_MAP
from calendar_manager import SchedulingHorizon
from worker_agent import FormalizedWorkerProfile
from config_loader import SchedulingConfig


class ScheduleDraftingAgent:
    """
    Agente di Schedule Drafting (Stage 2).
    Genera il piano iniziale codificato con specifica Google OR-Tools CP-SAT,
    garantendo il rispetto di tutti gli Hard Constraints letti dal file di config
    e massimizzando la soddisfazione di tutti i lavoratori.
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

        self.scenario_type = config.scenario_type
        self.num_standard  = config.num_standard_workers
        self.num_specialized = config.num_specialized_workers
        self.num_workers = len(worker_profiles)

        # Indici per scenario B (lavoratori specializzati)
        self.standard_indices    = list(range(self.num_standard))
        self.specialized_indices = list(range(self.num_standard, self.num_workers))

        # Convenzioni sui turni (da config)
        self.num_shifts = 3           # morning=0, afternoon=1, night=2
        # Pesi turni equivalenti: {0: peso_morning, 1: peso_afternoon, 2: peso_night}
        self._shift_weights = {
            SHIFT_MAP[sh]: self.config.shift_weights.get(sh, 1)
            for sh in self.config.shifts
        }
        # Ore per turno: {0: h_morning, 1: h_afternoon, 2: h_night}
        self._shift_hours = {
            SHIFT_MAP[sh]: self.config.shift_hours.get(sh, self.config.shift_duration_hours)
            for sh in self.config.shifts
        }

    # ------------------------------------------------------------------
    # Costruzione del modello CP-SAT
    # ------------------------------------------------------------------
    def build_ortools_model(self) -> tuple[cp_model.CpModel, Dict[tuple, Any], List[Any], Any]:
        """
        Costruisce il modello simbolico completo con OR-Tools.
        Ritorna: (model, shifts, worker_satisfactions, min_satisfaction_var)
        """
        model    = cp_model.CpModel()
        num_days = self.horizon.total_days
        cfg      = self.config

        # ---------------------------------------------------------
        # 1. VARIABILI DECISIONALI
        # shifts[(w, d, s)] = 1 se il worker w copre il turno s nel giorno d
        # ---------------------------------------------------------
        shifts = {}
        for w in range(self.num_workers):
            for d in range(num_days):
                for s in range(self.num_shifts):
                    shifts[(w, d, s)] = model.new_bool_var(f"w{w}_d{d}_s{s}")

        # ---------------------------------------------------------
        # 2. HARD CONSTRAINTS (da config)
        # ---------------------------------------------------------

        # A. Copertura dei turni (Staffing Requirements)
        for d in range(num_days):
            for s in range(self.num_shifts):
                if self.scenario_type == "A":
                    model.add(
                        sum(shifts[(w, d, s)] for w in range(self.num_workers))
                        >= cfg.min_workers_per_shift
                    )
                elif self.scenario_type == "B":
                    # Almeno min_specialized_per_shift specializzati
                    model.add(
                        sum(shifts[(w, d, s)] for w in self.specialized_indices)
                        >= cfg.min_specialized_per_shift
                    )
                    # Totale >= min_workers_per_shift + min_specialized_per_shift
                    # (il config usa min_workers_per_shift = 2 standard,
                    # più 1 spec = 3 totali)
                    model.add(
                        sum(shifts[(w, d, s)] for w in range(self.num_workers))
                        >= cfg.min_workers_per_shift + cfg.min_specialized_per_shift
                    )

        # B. Massimo 1 turno al giorno per lavoratore (se one_shift_per_day)
        if cfg.one_shift_per_day:
            for w in range(self.num_workers):
                for d in range(num_days):
                    model.add_at_most_one([shifts[(w, d, s)] for s in range(self.num_shifts)])


        # C. Riposo obbligatorio dopo turno di Notte
        rest_days = cfg.mandatory_rest_days_after_night
        night_idx = SHIFT_MAP["night"]
        for w in range(self.num_workers):
            for d in range(num_days):
                for offset in range(1, rest_days + 1):
                    if d + offset < num_days:
                        for next_s in range(self.num_shifts):
                            model.add(
                                shifts[(w, d + offset, next_s)] == 0
                            ).only_enforce_if(shifts[(w, d, night_idx)])

        # D. Equità turni di notte (gap max 2 tra worker)
        night_counts = []
        for w in range(self.num_workers):
            n_nights = model.new_int_var(0, num_days, f"nights_w{w}")
            model.add(n_nights == sum(shifts[(w, d, night_idx)] for d in range(num_days)))
            night_counts.append(n_nights)

        max_nights = model.new_int_var(0, num_days, "max_nights")
        min_nights = model.new_int_var(0, num_days, "min_nights")
        model.add_max_equality(max_nights, night_counts)
        model.add_min_equality(min_nights, night_counts)
        model.add(max_nights - min_nights <= 2)

        # E. Equità carico festivo (gap max 3 tra worker)
        holiday_counts = []
        for w in range(self.num_workers):
            h_shifts = model.new_int_var(
                0, len(self.horizon.holiday_indices) * self.num_shifts, f"holidays_w{w}"
            )
            model.add(h_shifts == sum(
                shifts[(w, d, s)]
                for d in self.horizon.holiday_indices
                for s in range(self.num_shifts)
            ))
            holiday_counts.append(h_shifts)

        max_holidays = model.new_int_var(
            0, len(self.horizon.holiday_indices) * self.num_shifts, "max_holidays"
        )
        min_holidays = model.new_int_var(
            0, len(self.horizon.holiday_indices) * self.num_shifts, "min_holidays"
        )
        model.add_max_equality(max_holidays, holiday_counts)
        model.add_min_equality(min_holidays, holiday_counts)
        model.add(max_holidays - min_holidays <= 3)

        # F. Monte turni mensile esatto (da config: exact_monthly_equivalent_shifts)
        for w in range(self.num_workers):
            total_monthly = sum(
                shifts[(w, d, s)] * self._shift_weights[s]
                for d in range(num_days)
                for s in range(self.num_shifts)
            )
            model.add(total_monthly == cfg.exact_monthly_equivalent_shifts)

        # G. Limite ore settimanali: Massimo tot (36) ore per settimana solare (Lunedi' - Domenica)
        calendar_weeks = self.horizon.get_calendar_weeks()
        for w in range(self.num_workers):
            for week_days in calendar_weeks:
                weekly_hours = sum(
                    shifts[(w, d, s)] * self._shift_hours[s]
                    for d in week_days
                    for s in range(self.num_shifts)
                )
                model.add(weekly_hours <= cfg.max_weekly_working_hours)

        # H. Almeno 1 giorno di riposo per ogni settimana di calendario completa (>= 6 giorni).
        # NOTA: I 2 giorni di riposo obbligatori post-notte (shifts[(w, d, s)] == 0)
        # sono considerati giorni di riposo a tutti gli effetti e soddisfano questo vincolo.
        # Le preferenze individuali di riposo (preferred_rest_days) sono premiate
        # come soft constraints (+15) nel Satisfaction Model.
        for w in range(self.num_workers):
            for week_days in calendar_weeks:
                if len(week_days) >= 6:
                    days_worked = sum(
                        sum(shifts[(w, d, s)] for s in range(self.num_shifts))
                        for d in week_days
                    )
                    model.add(days_worked <= len(week_days) - 1)

        # ---------------------------------------------------------
        # 3. HARD CONSTRAINTS INDIVIDUALI (indisponibilità)
        # ---------------------------------------------------------
        for profile in self.worker_profiles:
            profile.apply_hard_constraints_to_model(model, shifts)

        # ---------------------------------------------------------
        # 4. SATISFACTION MODELS & FAIRNESS OBJECTIVE
        # ---------------------------------------------------------
        worker_satisfactions = []
        for profile in self.worker_profiles:
            sat_var = profile.satisfaction_model.generate_satisfaction_expression(model, shifts)
            worker_satisfactions.append(sat_var)

        min_satisfaction = model.new_int_var(-10000, 10000, "min_satisfaction")
        for sat_var in worker_satisfactions:
            model.add(sat_var >= min_satisfaction)

        total_satisfaction = sum(worker_satisfactions)

        # Priorità primaria: massimizza la soddisfazione del lavoratore meno soddisfatto (Min-Max Fairness).
        # Priorità secondaria: massimizza la soddisfazione totale.
        model.maximize(min_satisfaction * 1000 + total_satisfaction)

        return model, shifts, worker_satisfactions, min_satisfaction

    # ------------------------------------------------------------------
    # Risoluzione
    # ------------------------------------------------------------------
    def solve_draft(self, time_limit_seconds: int = 30) -> Dict[str, Any]:
        """
        Risolve il modello CP-SAT iniziale e restituisce il piano di schedulazione.
        """
        model, shifts, worker_satisfactions, min_sat_var = self.build_ortools_model()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = 8

        status = solver.solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            schedule_matrix = []
            for w in range(self.num_workers):
                worker_schedule = []
                for d in range(self.horizon.total_days):
                    assigned_shift = None
                    for s in range(self.num_shifts):
                        if solver.value(shifts[(w, d, s)]) == 1:
                            assigned_shift = s
                            break
                    worker_schedule.append(assigned_shift)
                schedule_matrix.append(worker_schedule)

            satisfaction_scores = [solver.value(sat) for sat in worker_satisfactions]

            return {
                "status": "FEASIBLE" if status == cp_model.FEASIBLE else "OPTIMAL",
                "schedule_matrix": schedule_matrix,
                "satisfaction_scores": satisfaction_scores,
                "min_satisfaction": solver.value(min_sat_var),
                "total_satisfaction": sum(satisfaction_scores)
            }
        else:
            return {
                "status": "INFEASIBLE",
                "schedule_matrix": [],
                "satisfaction_scores": [],
                "min_satisfaction": None,
                "total_satisfaction": None
            }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_to_python_file(self, filepath: str = "generated_schedule_model.py"):
        """
        Esporta la specifica OR-Tools del modello in un file Python autonomo,
        con i parametri letti dal file di config.
        """
        cfg = self.config
        night_idx  = SHIFT_MAP["night"]
        rest_days  = cfg.mandatory_rest_days_after_night
        weights    = [self._shift_weights[s] for s in range(self.num_shifts)]
        hours      = [self._shift_hours[s]   for s in range(self.num_shifts)]

        # Snippet staffing adattato allo scenario
        if self.scenario_type == "A":
            staffing_snippet = (
                f"model.add(sum(shifts[(w, d, s)] for w in range(num_workers)) "
                f">= {cfg.min_workers_per_shift})"
            )
        else:
            staffing_snippet = (
                f"model.add(sum(shifts[(w, d, s)] for w in range({self.num_standard}, num_workers)) "
                f">= {cfg.min_specialized_per_shift}); "
                f"model.add(sum(shifts[(w, d, s)] for w in range(num_workers)) "
                f">= {cfg.min_workers_per_shift + cfg.min_specialized_per_shift})"
            )

        code = f'''# Auto-generated by SmartScheduler Drafting Agent
# Config: scenario={cfg.scenario_type}, workers={self.num_workers},
#         monthly_shifts={cfg.exact_monthly_equivalent_shifts},
#         max_weekly_hours={cfg.max_weekly_working_hours}
from ortools.sat.python import cp_model

def create_and_solve_schedule():
    model = cp_model.CpModel()
    num_workers = {self.num_workers}
    num_days    = {self.horizon.total_days}
    num_shifts  = {self.num_shifts}

    # Shift weights (turni equivalenti): {weights}
    shift_weights = {weights}
    # Shift hours:                       {hours}
    shift_hours   = {hours}

    shifts = {{}}
    for w in range(num_workers):
        for d in range(num_days):
            for s in range(num_shifts):
                shifts[(w, d, s)] = model.new_bool_var(f"w{{w}}_d{{d}}_s{{s}}")

    # Copertura turni (scenario {self.scenario_type})
    for d in range(num_days):
        for s in range(num_shifts):
            {staffing_snippet}

    # Max 1 turno al giorno per lavoratore
    for w in range(num_workers):
        for d in range(num_days):
            model.add_at_most_one([shifts[(w, d, s)] for s in range(num_shifts)])


    # Riposo {rest_days} giorni post-notte (turno index {night_idx})
    for w in range(num_workers):
        for d in range(num_days - {rest_days}):
            for offset in range(1, {rest_days + 1}):
                for ns in range(num_shifts):
                    model.add(shifts[(w, d + offset, ns)] == 0).only_enforce_if(shifts[(w, d, {night_idx})])

    # {cfg.exact_monthly_equivalent_shifts} turni equivalenti al mese
    for w in range(num_workers):
        model.add(
            sum(shifts[(w, d, s)] * shift_weights[s]
                for d in range(num_days)
                for s in range(num_shifts)) == {cfg.exact_monthly_equivalent_shifts}
        )

    # Max {cfg.max_weekly_working_hours} ore settimanali (settimane solari Lunedi-Domenica)
    # e almeno 1 giorno di riposo per settimana di calendario
    for w in range(num_workers):
        for start_day in range(0, num_days, 7):
            end_day = min(start_day + 7, num_days)
            week_len = end_day - start_day
            model.add(
                sum(shifts[(w, d, s)] * shift_hours[s]
                    for d in range(start_day, end_day)
                    for s in range(num_shifts)) <= {cfg.max_weekly_working_hours}
            )
            if week_len >= 6:
                model.add(
                    sum(sum(shifts[(w, d, s)] for s in range(num_shifts))
                        for d in range(start_day, end_day)) <= week_len - 1
                )

    solver = cp_model.CpSolver()
    return solver.solve(model)

if __name__ == "__main__":
    create_and_solve_schedule()
'''
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)