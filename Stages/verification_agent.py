from typing import List, Dict, Any, Optional

from models import SHIFT_MAP
from calendar_manager import SchedulingHorizon
from worker_agent import FormalizedWorkerProfile
from config_loader import SchedulingConfig
SHIFT_HOURS = {0: 6, 1: 6, 2: 12, None: 0}
SHIFT_UNITS = {0: 1, 1: 1, 2: 2, None: 0}     

# NOTA: il vincolo sui turni consecutivi non è stato implementato perchè soddisfatto automaticamente dai vincoli:
# - al più un turno al giorno
# - riposo post-notte (almeno 2 giorni liberi dopo ogni turno di notte)
# Il suo inserimento renderebbe ridondante la logica di verifica.

class HardConstraintVerificationAgent:
    """
    Symbolic Verification Agent per la conformita' dei vincoli rigidi (Hard Constraints).

    Valuta:
    1. Staffing requirements (copertura minima Use Case A e B)
    2. Legal work limits (max ore/settimana e monte turni mensili)
    3. Mandatory rest constraints (N giorni di riposo post-notte)
    4. Shift compatibility rules (max 1 turno al giorno)
    5. Specifiche indisponibilita' individuali (unavailable_days)
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

        self.scenario_type   = config.scenario_type
        self.num_standard    = config.num_standard_workers
        self.num_specialized = config.num_specialized_workers
        self.num_workers     = len(worker_profiles)
        self.specialized_indices = set(range(self.num_standard, self.num_workers))

        # Mappa indice shift -> ore reali e pesi equivalenti dal config
        self._shift_hours: Dict[Optional[int], int] = {None: 0}
        self._shift_units: Dict[Optional[int], int] = {None: 0}
        for sh_name in config.shifts:
            idx = SHIFT_MAP.get(sh_name)
            if idx is not None:
                self._shift_hours[idx] = config.shift_hours.get(sh_name, config.shift_duration_hours)
                self._shift_units[idx] = config.shift_weights.get(sh_name, 1)


    def verify(self, schedule_matrix: List[List[Optional[int]]]) -> Dict[str, Any]:
        """
        Riceve schedule_matrix da Stage 2: matrice [num_workers][num_days],
        dove il valore e' 0 (Mattina), 1 (Pomeriggio), 2 (Notte) o None (Riposo).
        """
        violations = []
        num_days = self.horizon.total_days
        cfg      = self.config

        if not schedule_matrix or len(schedule_matrix) != self.num_workers:
            return {
                "is_valid": False,
                "violations": ["Formato schedule_matrix non valido o numero di lavoratori non corrispondente."]
            }

        num_shifts = cfg.num_shifts   # numero turni per giorno (es. 3)

        # -----------------------------------------------------------------
        # 1. Verifica Staffing Requirements
        # -----------------------------------------------------------------
        for d in range(num_days):
            for s in range(num_shifts):
                active_workers = [w for w in range(self.num_workers) if schedule_matrix[w][d] == s]
                count_tot = len(active_workers)

                # Minimo numero di lavoratori per turno
                if self.scenario_type == "A":
                    if count_tot < cfg.min_workers_per_shift:
                        violations.append(
                            f"[Staffing] Giorno {d + 1} ({self.horizon.index_to_date[d]}), Turno {s}: "
                            f"richiesti >= {cfg.min_workers_per_shift} lavoratori, presenti {count_tot}."
                        )
                elif self.scenario_type == "B":
                    count_spec = len([w for w in active_workers if w in self.specialized_indices])
                    tot_required = cfg.min_workers_per_shift + cfg.min_specialized_per_shift
                    if count_tot < tot_required or count_spec < cfg.min_specialized_per_shift:
                        violations.append(
                            f"[Staffing] Giorno {d + 1} ({self.horizon.index_to_date[d]}), Turno {s}: "
                            f"richiesti >= {tot_required} tot e >= {cfg.min_specialized_per_shift} spec, "
                            f"presenti {count_tot} tot e {count_spec} spec."
                        )

        # -----------------------------------------------------------------
        # 2. Verifica Riposo Post-Notte
        # -----------------------------------------------------------------
        night_idx = SHIFT_MAP.get("night", 2)
        rest_days = cfg.mandatory_rest_days_after_night
        for w in range(self.num_workers):
            for d in range(num_days):
                if schedule_matrix[w][d] == night_idx:
                    for offset in range(1, rest_days + 1):
                        if d + offset < num_days and schedule_matrix[w][d + offset] is not None:
                            violations.append(
                                f"[Rest] Worker {w + 1}: Turno di notte al giorno {d + 1} "
                                f"senza riposo al giorno {d + 1 + offset}."
                            )

        # -----------------------------------------------------------------
        # 3. Verifica Limite Ore Settimanali (<= 36h per settimana solare Lun-Dom)
        # -----------------------------------------------------------------
        calendar_weeks = self.horizon.get_calendar_weeks()
        for w in range(self.num_workers):
            for week_idx, week_days in enumerate(calendar_weeks, 1):
                weekly_hours = sum(
                    self._shift_hours.get(schedule_matrix[w][d], 0)
                    for d in week_days
                )
                if weekly_hours > cfg.max_weekly_working_hours:
                    violations.append(
                        f"[Legal Hours] Worker {w + 1}, Settimana {week_idx}: "
                        f"lavorate {weekly_hours}h (limite {cfg.max_weekly_working_hours}h)."
                    )

        # -----------------------------------------------------------------
        # 3b. Verifica Riposo Minimo Settimanale (almeno 1 giorno libero
        #     per ogni settimana di calendario completa)
        # NOTA: I 2 giorni post-notte (schedule_matrix[w][d] is None) sono
        # considerati giorni di riposo a tutti gli effetti.
        # -----------------------------------------------------------------
        for w in range(self.num_workers):
            for week_idx, week_days in enumerate(calendar_weeks, 1):
                if len(week_days) >= 6:
                    days_worked = sum(
                        1 for d in week_days
                        if schedule_matrix[w][d] is not None
                    )
                    if days_worked > len(week_days) - 1:
                        violations.append(
                            f"[Weekly Rest] Worker {w + 1}, Settimana {week_idx}: "
                            f"{days_worked} giorni lavorati su {len(week_days)} (richiesto almeno 1 giorno di riposo)."
                        )

        # -----------------------------------------------------------------
        # 4. Verifica Monte Turni Mensile
        # -----------------------------------------------------------------
        for w in range(self.num_workers):
            total_units = sum(
                self._shift_units.get(schedule_matrix[w][d], 0)
                for d in range(num_days)
            )
            if total_units != cfg.exact_monthly_equivalent_shifts:
                violations.append(
                    f"[Monthly Quota] Worker {w + 1}: completati {total_units} turni equivalenti "
                    f"(richiesti {cfg.exact_monthly_equivalent_shifts})."
                )

        # -----------------------------------------------------------------
        # 5. Max 1 turno al giorno (se one_shift_per_day nel config)
        # -----------------------------------------------------------------
        if cfg.one_shift_per_day:
            for w in range(self.num_workers):
                for d in range(num_days):
                    assigned = [s for s in range(num_shifts) if schedule_matrix[w][d] == s]
                    if len(assigned) > 1:
                        violations.append(
                            f"[One-Shift] Worker {w + 1}: piu' di un turno assegnato al giorno {d + 1}."
                        )

        # -----------------------------------------------------------------
        # 5b. Verifica Nessun Turno Consecutivo (No Consecutive Shifts)
        # Controlla che nessun lavoratore copra turni cronologicamente consecutivi:
        # - Notte (2) al giorno d e Mattina (0) al giorno d+1
        # -----------------------------------------------------------------
        if getattr(cfg, "no_consecutive_shifts", True):
            for w in range(self.num_workers):
                for d in range(num_days - 1):
                    if schedule_matrix[w][d] == 2 and schedule_matrix[w][d + 1] == 0:
                        violations.append(
                            f"[Consecutive Shifts] Worker {w + 1}: turno di Notte al giorno {d + 1} "
                            f"({self.horizon.index_to_date[d]}) seguito consecutivamente da turno di Mattina "
                            f"al giorno {d + 2} ({self.horizon.index_to_date[d + 1]})."
                        )

        # -----------------------------------------------------------------
        # 6. Verifica Indisponibilita' Assolute (Hard Individuali)
        # -----------------------------------------------------------------
        for profile in self.worker_profiles:
            w = profile.worker_idx
            for d in profile.hard_constraints["unavailable_day_indices"]:
                if schedule_matrix[w][d] is not None:
                    violations.append(
                        f"[Unavailability] Worker {w + 1} ({profile.worker_id}) assegnato al giorno {d + 1} "
                        f"({self.horizon.index_to_date[d]}) nonostante indisponibilita' dichiarata."
                    )

        # -----------------------------------------------------------------
        # 7. Verifica Limite Coperture di Emergenza (Hard Individuale)
        # -----------------------------------------------------------------
        for profile in self.worker_profiles:
            w = profile.worker_idx
            max_cov = profile.hard_constraints.get("max_emergency_coverage")
            if max_cov is not None:
                total_shifts_assigned = sum(
                    1 for d in range(num_days) if schedule_matrix[w][d] is not None
                )
                if total_shifts_assigned > max_cov:
                    violations.append(
                        f"[Emergency Coverage] Worker {w + 1} ({profile.worker_id}): "
                        f"assegnati {total_shifts_assigned} turni totali, "
                        f"limite dichiarato {max_cov}."
                    )

        is_valid = len(violations) == 0
        return {
            "is_valid": is_valid,
            "status": "APPROVED" if is_valid else "REJECTED",
            "violation_count": len(violations),
            "violations": violations
        }




class SymbolicFairnessVerificationAgent:
    """
    Symbolic Fairness Verification Agent per la quantificazione dell'equita' distributiva.
    Calcola:
    - Identificazione del lavoratore piu' svantaggiato (Most Disadvantaged Worker)
    - Statistiche di distribuzione (Min, Max, Media, Std Dev)
    - Indice di Gini (disuguaglianza tra i punteggi di soddisfazione)
    - Distribuzione dei turni gravosi (notti e festivi assegnati per lavoratore)
    """

    def __init__(self, horizon: SchedulingHorizon, worker_profiles: List[FormalizedWorkerProfile]):
        self.horizon = horizon
        self.worker_profiles = worker_profiles
        self.num_workers = len(worker_profiles)

    def evaluate(self, schedule_matrix: List[List[Optional[int]]], satisfaction_scores: List[int]) -> Dict[str, Any]:
        min_score = min(satisfaction_scores)
        max_score = max(satisfaction_scores)
        avg_score = sum(satisfaction_scores) / self.num_workers

        night_idx = SHIFT_MAP.get("night", 2)

        # Identifica i lavoratori piu' svantaggiati
        disadvantaged_indices = [i for i, s in enumerate(satisfaction_scores) if s == min_score]
        disadvantaged_workers = [
            {
                "worker_index": idx,
                "worker_id": self.worker_profiles[idx].worker_id,
                "satisfaction_score": satisfaction_scores[idx],
                "assigned_nights": sum(
                    1 for d in range(self.horizon.total_days) if schedule_matrix[idx][d] == night_idx
                ),
                "assigned_holidays": sum(
                    1 for d in self.horizon.holiday_indices if schedule_matrix[idx][d] is not None
                )
            }
            for idx in disadvantaged_indices
        ]

        # Varianza e deviazione standard
        variance = sum((s - avg_score) ** 2 for s in satisfaction_scores) / self.num_workers
        std_dev  = variance ** 0.5

        # Gini Index normalizzato
        shift_val = abs(min_score) + 1 if min_score <= 0 else 0
        positive_scores = sorted([s + shift_val for s in satisfaction_scores])
        n = len(positive_scores)
        if sum(positive_scores) == 0:
            gini_index = 0.0
        else:
            diff_sum   = sum(abs(xi - xj) for xi in positive_scores for xj in positive_scores)
            gini_index = diff_sum / (2 * n * sum(positive_scores))

        # Analisi carico turni sgraditi
        shift_distribution = []
        for w in range(self.num_workers):
            nights   = sum(1 for d in range(self.horizon.total_days) if schedule_matrix[w][d] == night_idx)
            holidays = sum(1 for d in self.horizon.holiday_indices if schedule_matrix[w][d] is not None)
            shift_distribution.append({
                "worker_id": self.worker_profiles[w].worker_id,
                "nights":    nights,
                "holidays":  holidays,
                "score":     satisfaction_scores[w]
            })

        return {
            "min_satisfaction":           min_score,
            "max_satisfaction":           max_score,
            "mean_satisfaction":          round(avg_score, 2),
            "std_deviation":              round(std_dev, 2),
            "gini_index":                 round(gini_index, 4),
            "most_disadvantaged_workers": disadvantaged_workers,
            "workers_shift_distribution": shift_distribution
        }