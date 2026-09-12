from models import WorkerPreference, SHIFT_MAP
from calendar_manager import SchedulingHorizon


# -------------------------- MODELLO DI SODDISFAZIONE DEL LAVORATORE ---------------------------------

class WorkerSatisfactionModel:
    """
    Satisfaction Model associato al lavoratore.
    Quantifica matematicamente quanto una schedulazione soddisfa le preferenze.
    """
    def __init__(self, pref: WorkerPreference, worker_idx: int, horizon: SchedulingHorizon):
        self.pref = pref
        self.worker_idx = worker_idx
        self.horizon = horizon

    def generate_satisfaction_expression(self, model, shifts):
        satisfaction_terms = []
        w = self.worker_idx

        # SODDISFAZIONE POSITIVA
        # 1. Bonus tipi di turni preferiti (+10)
        for s_type in self.pref.preferred_shifts:
            s_idx = SHIFT_MAP[s_type]
            for d in range(self.horizon.total_days):
                satisfaction_terms.append(shifts[(w, d, s_idx)] * 10)

        # 2. Penalità tipi di turni sgraditi (-10)
        for s_type in self.pref.shift_tolerance.disliked_shift_types:
            s_idx = SHIFT_MAP[s_type]
            for d in range(self.horizon.total_days):
                satisfaction_terms.append(shifts[(w, d, s_idx)] * -10)

        # 3. Bonus giorni di riposo preferiti (+15)
        for date_str in self.pref.availability.preferred_rest_days:
            if self.horizon.is_date_in_horizon(date_str):
                d = self.horizon.date_to_index[date_str]
                is_free = model.new_bool_var(f"free_w{w}_d{d}")
                model.add(sum(shifts[(w, d, s)] for s in range(3)) == 0).only_enforce_if(is_free)
                model.add(sum(shifts[(w, d, s)] for s in range(3)) >= 1).only_enforce_if(is_free.negated())
                satisfaction_terms.append(is_free * 15)

        # 4. Bonus neutro (+2) per turni non esplicitamente sgraditi o preferiti
        for s in range(3):
            if s not in [SHIFT_MAP[p] for p in self.pref.preferred_shifts] and \
               s not in [SHIFT_MAP[p] for p in self.pref.shift_tolerance.disliked_shift_types]:
                for d in range(self.horizon.total_days):
                    satisfaction_terms.append(shifts[(w, d, s)] * 2)


        # SODDISFAZIONE NEGATIVA
        tol = self.pref.shift_tolerance

        # TOLLERANZA NOTTI: se supera la soglia, penalità progressiva (-15 per ogni notte extra)
        if tol.max_tolerated_nights is not None:
            limit_n = tol.max_tolerated_nights
            actual_nights = sum(shifts[(w, d, 2)] for d in range(self.horizon.total_days))
            excess_nights = model.new_int_var(0, self.horizon.total_days, f"excess_nights_w{w}")
            model.add(excess_nights >= actual_nights - limit_n)
            satisfaction_terms.append(excess_nights * -15)

        # TOLLERANZA FESTIVI: penalità progressiva (-15 per ogni turno festivo extra)
        if tol.max_tolerated_holidays is not None:
            limit_h = tol.max_tolerated_holidays
            actual_holidays = sum(
                shifts[(w, d, s)] 
                for d in self.horizon.holiday_indices 
                for s in range(3)
            )
            excess_holidays = model.new_int_var(0, len(self.horizon.holiday_indices) * 3, f"excess_holidays_w{w}")
            model.add(excess_holidays >= actual_holidays - limit_h)
            satisfaction_terms.append(excess_holidays * -15)

        # TOLLERANZA WEEKEND: penalità progressiva (-15 per ogni turno weekend extra)
        if tol.max_tolerated_weekends is not None:
            limit_w = tol.max_tolerated_weekends
            actual_weekends = sum(
                shifts[(w, d, s)]
                for d in self.horizon.weekend_indices
                for s in range(3)
            )
            excess_weekends = model.new_int_var(0, len(self.horizon.weekend_indices) * 3, f"excess_weekends_w{w}")
            model.add(excess_weekends >= actual_weekends - limit_w)
            satisfaction_terms.append(excess_weekends * -15)

        # TOLLERANZA TURNI FATICOSI CONSECUTIVI:
        # Un turno è 'stancante' se cade in una notte OPPURE in un giorno festivo OPPURE in un weekend.
        # Ogni unità oltre la soglia genera -20 di penalità.
        # Nota: max_tolerated_consecutive_demanding_shifts=0 = "nessuna coppia consecutiva tollerata"
        #
        # Tre regole di consecutività (unite senza double-counting):
        #   REGOLA 1 - Notte+Notte:          adiacenti nel calendario (d e d+1, entrambi turno notte)
        #   REGOLA 2 - Weekend+Weekend:      Sabato e Domenica dello stesso weekend (d e d+1 in weekend_indices)
        #   REGOLA 3 - Festività+Festività:  adiacenti nella sequenza delle sole festività nazionali
        #                                   (es. 26-12 e 01-01: nessuna altra festività tra loro)
        if tol.max_tolerated_consecutive_demanding_shifts is not None:
            limit_c = tol.max_tolerated_consecutive_demanding_shifts
            night_idx = 2  # ShiftType.NIGHT

            # Unione di festività e weekend per determinare i giorni "stancanti"
            demanding_day_indices = self.horizon.holiday_indices | self.horizon.weekend_indices

            # dem[d] = 1 se il giorno d è 'stancante' per il worker w:
            #   - weekend o festività nazionale: stancante se il worker lavora qualsiasi turno
            #   - giorno feriale: stancante solo se il worker ha turno notturno
            demanding = []
            for d in range(self.horizon.total_days):
                dem = model.new_bool_var(f"dem_w{w}_d{d}")
                if d in demanding_day_indices:
                    works_any = model.new_bool_var(f"works_w{w}_d{d}")
                    model.add_bool_or([shifts[(w, d, s)] for s in range(3)]).only_enforce_if(works_any)
                    model.add(sum(shifts[(w, d, s)] for s in range(3)) == 0).only_enforce_if(works_any.negated())
                    model.add(dem == works_any)
                else:
                    model.add(dem == shifts[(w, d, night_idx)])
                demanding.append(dem)

            # Insieme delle coppie candidate (senza duplicati)
            candidate_pairs: set = set()

            # REGOLA 1 & 2: ogni coppia di giorni adiacenti nel calendario
            # (copre notti consecutive e coppie Sabato-Domenica)
            for d in range(self.horizon.total_days - 1):
                candidate_pairs.add((d, d + 1))

            # REGOLA 3: coppie di festività nazionali adiacenti nella loro sequenza,
            # anche se separate da giorni feriali (es. 26-12 e 01-01)
            sorted_pub_hol = sorted(self.horizon.holiday_indices)
            for i in range(len(sorted_pub_hol) - 1):
                h1, h2 = sorted_pub_hol[i], sorted_pub_hol[i + 1]
                if h2 > h1 + 1:
                    candidate_pairs.add((h1, h2))

            # Bool var per ciascuna coppia candidata
            consec_count_terms = []
            for (d1, d2) in sorted(candidate_pairs):
                pair = model.new_bool_var(f"consec_pair_w{w}_d{d1}_d{d2}")
                model.add_bool_and([demanding[d1], demanding[d2]]).only_enforce_if(pair)
                model.add_bool_or([demanding[d1].negated(), demanding[d2].negated()]).only_enforce_if(pair.negated())
                consec_count_terms.append(pair)

            if consec_count_terms:
                actual_consec = model.new_int_var(0, len(consec_count_terms), f"consec_dem_w{w}")
                model.add(actual_consec == sum(consec_count_terms))
                excess_consec = model.new_int_var(0, len(consec_count_terms), f"excess_consec_w{w}")
                model.add(excess_consec >= actual_consec - limit_c)
                satisfaction_terms.append(excess_consec * -20)

        satisfaction_var = model.new_int_var(-3000, 3000, f"sat_worker_{w}")
        model.add(satisfaction_var == sum(satisfaction_terms))
        return satisfaction_var
