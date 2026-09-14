"""
config_loader.py  -  SmartScheduler
====================================
Parsa i file di specifica del modello (configs_case_a / configs_case_b) e
restituisce un oggetto SchedulingConfig con tutti i parametri pronti per
l'uso nei moduli di drafting, verification e refinement.

Formato atteso del file (INI-like con sezioni in []):
------------------------------------------------------
[HORIZON]
start_date = 07-12-2026
end_date   = 06-01-2027

[WORKERS]
scenario_type         = A | B
num_standard_workers  = 13
num_specialized_workers = 7

[AVAILABLE_SHIFTS]
shifts         = morning, afternoon, night
shift_weights  = morning:1, afternoon:1, night:2

[HARD_CONSTRAINTS_LEGAL]
min_workers_per_shift          = 2
min_specialized_per_shift      = 1
max_consecutive_working_days   = 6
exact_monthly_equivalent_shifts= 25
max_weekly_working_hours       = 36
shift_duration_hours           = 6
mandatory_rest_days_after_night= 2
one_shift_per_day              = true
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set



@dataclass
class SchedulingConfig:
    # ---- HORIZON ----
    start_date: str
    end_date: str

    # ---- WORKERS ----
    scenario_type: str          # "A" o "B"
    num_standard_workers: int
    num_specialized_workers: int

    # ---- SHIFTS ----
    shifts: List[str]                  # es. ["morning", "afternoon", "night"]
    shift_weights: Dict[str, int]      # es. {"morning": 1, "afternoon": 1, "night": 2}
    shift_hours: Dict[str, int]        # durata in ore, calcolata da shift_duration_hours

    # ---- HARD CONSTRAINTS ----
    min_workers_per_shift: int
    min_specialized_per_shift: int
    max_consecutive_working_days: int
    exact_monthly_equivalent_shifts: int
    max_weekly_working_hours: int
    shift_duration_hours: int          # ore per turno base (mattina / pomeriggio)
    mandatory_rest_days_after_night: int
    one_shift_per_day: bool
    no_consecutive_shifts: bool = True


    @property
    def num_workers(self) -> int:
        return self.num_standard_workers + self.num_specialized_workers

    @property
    def num_shifts(self) -> int:
        return len(self.shifts)



# Parser

def _parse_key_value_pairs(raw: str) -> Dict[str, str]:
    """Parsa stringhe del tipo 'chiave1:val1, chiave2:val2' in un dict."""
    result = {}
    for item in raw.split(","):
        item = item.strip()
        if ":" in item:
            k, v = item.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def load_config(config_path: str) -> "SchedulingConfig":
    """
    Legge il file di specifica del modello e restituisce un SchedulingConfig.

    Parameters
    ----------
    config_path : str
        Percorso assoluto o relativo del file di configurazione
        (es. 'configs/configs_case_a' oppure 'configs/configs_case_a.txt').

    Returns
    -------
    SchedulingConfig
        Oggetto con tutti i parametri di schedulazione.

    Raises
    ------
    FileNotFoundError
        Se il file non esiste.
    ValueError
        Se mancano sezioni o chiavi obbligatorie.
    """
    path = Path(config_path)
    if not path.is_file():
        # Tentativo aggiungendo estensione .txt se omessa
        if (path.parent / f"{path.name}.txt").is_file():
            path = path.parent / f"{path.name}.txt"
        elif path.with_suffix(".txt").is_file():
            path = path.with_suffix(".txt")
        else:
            raise FileNotFoundError(f"File di configurazione non trovato: {path.resolve()}")

    parser = configparser.ConfigParser(
        inline_comment_prefixes=("#", ";"),
        allow_no_value=True,
        strict=False
    )
    raw_text = path.read_text(encoding="utf-8")
    parser.read_string(raw_text)

    # ---- Verifica sezioni obbligatorie ----
    required_sections = {"HORIZON", "WORKERS", "AVAILABLE_SHIFTS", "HARD_CONSTRAINTS_LEGAL"}
    missing = required_sections - set(parser.sections())
    if missing:
        raise ValueError(f"Sezioni mancanti nel file di config: {missing}")

    # ---- HORIZON ----
    h = parser["HORIZON"]
    start_date = h.get("start_date", "07-12-2026").strip()
    end_date   = h.get("end_date",   "06-01-2027").strip()

    # ---- WORKERS ----
    w = parser["WORKERS"]
    scenario_type           = w.get("scenario_type", "A").strip().upper()
    num_standard_workers    = int(w.get("num_standard_workers", "13").strip())
    num_specialized_workers = int(w.get("num_specialized_workers", "0").strip())

    # ---- AVAILABLE SHIFTS ----
    s = parser["AVAILABLE_SHIFTS"]
    shifts_raw = s.get("shifts", "morning, afternoon, night")
    shifts: List[str] = [sh.strip() for sh in shifts_raw.split(",") if sh.strip()]

    weights_raw = s.get("shift_weights", "morning:1, afternoon:1, night:2")
    weights_dict = _parse_key_value_pairs(weights_raw)
    shift_weights: Dict[str, int] = {k: int(v) for k, v in weights_dict.items()}

    # HARD CONSTRAINTS (regole ospedaliere)
    hc = parser["HARD_CONSTRAINTS_LEGAL"]
    min_workers_per_shift           = int(hc.get("min_workers_per_shift",           "2").strip())
    min_specialized_per_shift       = int(hc.get("min_specialized_per_shift",       "0").strip())
    max_consecutive_working_days    = int(hc.get("max_consecutive_working_days",    "6").strip())
    exact_monthly_equivalent_shifts = int(hc.get("exact_monthly_equivalent_shifts", "25").strip())
    max_weekly_working_hours        = int(hc.get("max_weekly_working_hours",        "36").strip())
    shift_duration_hours            = int(hc.get("shift_duration_hours",            "6").strip())
    mandatory_rest_days_after_night = int(hc.get("mandatory_rest_days_after_night", "2").strip())
    one_shift_per_day               = hc.get("one_shift_per_day", "true").strip().lower() == "true"
    no_consecutive_shifts           = hc.get("no_consecutive_shifts", "true").strip().lower() == "true"

    # ---- Calcolo durate in ore per ogni shift ----
    # Convenzione: ogni shift ha durata shift_duration_hours * shift_weight
    # (mattina=6h, pomeriggio=6h, notte=12h per i default classici)
    shift_hours: Dict[str, int] = {
        sh: shift_duration_hours * shift_weights.get(sh, 1)
        for sh in shifts
    }

    return SchedulingConfig(
        start_date=start_date,
        end_date=end_date,
        scenario_type=scenario_type,
        num_standard_workers=num_standard_workers,
        num_specialized_workers=num_specialized_workers,
        shifts=shifts,
        shift_weights=shift_weights,
        shift_hours=shift_hours,
        min_workers_per_shift=min_workers_per_shift,
        min_specialized_per_shift=min_specialized_per_shift,
        max_consecutive_working_days=max_consecutive_working_days,
        exact_monthly_equivalent_shifts=exact_monthly_equivalent_shifts,
        max_weekly_working_hours=max_weekly_working_hours,
        shift_duration_hours=shift_duration_hours,
        mandatory_rest_days_after_night=mandatory_rest_days_after_night,
        one_shift_per_day=one_shift_per_day,
        no_consecutive_shifts=no_consecutive_shifts,
    )
