import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Optional, Callable, Tuple
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import SystemMessage
from models import WorkerPreference, ShiftType, DATE_FORMAT, SHIFT_MAP
from calendar_manager import SchedulingHorizon
from satisfaction_model import WorkerSatisfactionModel



# ------------------------------------GESTIONE CACHE PERSISTENTE PREFERENZE  --------------------------------
# (per test ripetuti evita di dover eseguire nuovamente la fase 1)
CACHE_FILE = Path(__file__).resolve().parent / ".worker_cache.json"
_memory_cache: Optional[Dict[str, Any]] = None


def _get_cache_dict() -> Dict[str, Any]:
    global _memory_cache
    if _memory_cache is None:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _memory_cache = json.load(f)
            except Exception:
                _memory_cache = {}
        else:
            _memory_cache = {}
    return _memory_cache


def get_cached_preference(text: str) -> Optional[Dict[str, Any]]:
    """Recupera un profilo parsato dalla cache locale (se presente)."""
    key = hashlib.md5(text.strip().encode("utf-8")).hexdigest()
    cache = _get_cache_dict()
    return cache.get(key)


def set_cached_preference(text: str, pref_dict: Dict[str, Any]):
    """Salva un profilo parsato nella cache su disco e in memoria."""
    key = hashlib.md5(text.strip().encode("utf-8")).hexdigest()
    cache = _get_cache_dict()
    cache[key] = pref_dict
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

# ------------------------------------------- PROFILO DEL LAVORATORE ---------------------------------------------

class FormalizedWorkerProfile:
    """
    Rappresentazione machine-readable delle preferenze di un infermiere che separa:
    - Hard Constraints
    - Soft Constraints / Preferenze
    - Satisfaction Model
    """
    def __init__(self, pref: WorkerPreference, worker_idx: int, horizon: SchedulingHorizon):
        wid = pref.worker_id.strip() if pref.worker_id else ""
        if not wid:
            wid = f"Worker {worker_idx + 1}"
        elif wid.isdigit():
            wid = f"Worker {wid}"
        self.worker_id = wid           #etichetta testuale
        self.worker_idx = worker_idx   #indice di calcolo (serve a CP-SAT per indicizzare le variabili)
        self.reasoning = pref.reasoning
        self.raw_preference = pref
        self.horizon = horizon
        self.satisfaction_model = WorkerSatisfactionModel(pref, worker_idx, horizon)
        
        # HARD CONSTRAINTS: giorni di indisponibilità
        self.hard_constraints: Dict[str, Any] = {
            "unavailable_day_indices": [
                horizon.date_to_index[d] for d in pref.availability.unavailable_days 
                if horizon.is_date_in_horizon(d)
            ]
        }
        
        # SOFT CONTRAINTS: turni preferiti, turni non graditi, preferenza riposi
        self.soft_constraints: Dict[str, Any] = {
            "preferred_shift_indices": [SHIFT_MAP[s] for s in pref.preferred_shifts],
            "disliked_shift_indices": [SHIFT_MAP[s] for s in pref.shift_tolerance.disliked_shift_types],
            "preferred_rest_day_indices": [
                horizon.date_to_index[d] for d in pref.availability.preferred_rest_days 
                if horizon.is_date_in_horizon(d)
            ],
            "avoid_holiday_shifts": pref.shift_tolerance.max_tolerated_holidays == 0,
            "avoid_weekend_shifts": pref.shift_tolerance.max_tolerated_weekends == 0,
            "avoid_consecutive_demanding": pref.shift_tolerance.max_tolerated_consecutive_demanding_shifts == 0
        }

    def apply_hard_constraints_to_model(self, model, shifts):
        w = self.worker_idx
        for d in self.hard_constraints["unavailable_day_indices"]:
            for s in range(3):
                model.add(shifts[(w, d, s)] == 0)



#------------------------------------------------ COSTRUZIONE PROMPT -----------------------------------------

def build_prompt_template(horizon: SchedulingHorizon) -> ChatPromptTemplate:
    system_instructions = f"""You are an expert hospital scheduling assistant.
Extract worker preferences into structured JSON strictly following these rules:

SCHEDULING HORIZON:
- Dates: from {horizon.start_date_str} to {horizon.end_date_str} ({horizon.total_days} days). Format: 'DD-MM-YYYY'.

CORE RULES:
1. preferred_shifts: ONLY shifts explicitly preferred ('I prefer morning', 'I like afternoon'). If the worker only states dislikes/avoidances and NO preference, preferred_shifts MUST BE EMPTY ([]).
2. DATE CLASSIFICATION (MUTUALLY EXCLUSIVE):
   - unavailable_days (HARD): Strict impossibility ('cannot work', 'unavailable', 'impossible', 'not available').
   - preferred_rest_days (SOFT): Desired time off ('I want off', 'I request off', 'need as a rest day', 'would like off').
3. shift_tolerance:
   - disliked_shift_types: Shift types they avoid (ONLY from ['morning', 'afternoon', 'night']). Never put 'holiday' or 'weekend' here.
   - max_tolerated_nights: Integer limit on night shifts. 0 if they avoid/hate nights; null if no limit stated.
   - max_tolerated_holidays: Integer limit on public holidays. 0 if they avoid/hate holiday shifts; null if no limit stated.
   - max_tolerated_weekends: Integer limit on weekend shifts. 0 if they avoid/hate weekend shifts; null if no limit stated.
   - max_tolerated_consecutive_demanding_shifts: Integer limit on consecutive demanding shifts; null if not stated.
4. ANTI-HALLUCINATION:
   - NEVER invent preferred shifts: if not mentioned, preferred_shifts must be [].
   - NEVER invent numeric limits: max_tolerated_* MUST be null unless explicitly stated (e.g. 'at most N', 'up to N', or 'avoid' -> 0).
   - A date in unavailable_days or preferred_rest_days does NOT set max_tolerated_holidays or max_tolerated_weekends.
   - If a worker prefers a shift type, it can NEVER be in disliked_shift_types.

REASONING PROTOCOL:
In 'reasoning', concisely write:
- Preferred shifts (or 'none' -> [])
- Date classification (HARD vs SOFT)
- Tolerances (numeric limits or avoidance; null otherwise)
- Contradiction check

EXAMPLES:

Example 1 (Preferences + Disliked + Soft vs Hard Dates):
Input: "Worker A: I prefer morning shifts and absolutely avoid night shifts. I want a rest day on 24-12-2026 and cannot work on 31-12-2026."
Output:
{{
  "reasoning": "Worker A prefers morning. 'want rest day 24-12-2026' -> SOFT -> preferred_rest_days. 'cannot work 31-12-2026' -> HARD -> unavailable_days. 'avoid night shifts' -> disliked ['night'], max_tolerated_nights 0. No holiday/weekend numeric limits -> null. No contradictions.",
  "worker_id": "Worker A",
  "preferred_shifts": ["morning"],
  "availability": {{
    "preferred_rest_days": ["24-12-2026"],
    "unavailable_days": ["31-12-2026"]
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["night"],
    "max_tolerated_nights": 0,
    "max_tolerated_holidays": null,
    "max_tolerated_weekends": null,
    "max_tolerated_consecutive_demanding_shifts": null
  }}
}}

Example 2 (Avoidances Only, NO Preferred Shifts):
Input: "Worker B: I would like to avoid night shifts, holidays, and weekends. I want 25-12-2026 off."
Output:
{{
  "reasoning": "Worker B states no preferred shifts -> preferred_shifts []. 'want 25-12-2026 off' -> SOFT -> preferred_rest_days ['25-12-2026']. 'avoid night shifts' -> disliked ['night'], max_tolerated_nights 0. 'avoid holidays' -> max_tolerated_holidays 0. 'avoid weekends' -> max_tolerated_weekends 0.",
  "worker_id": "Worker B",
  "preferred_shifts": [],
  "availability": {{
    "preferred_rest_days": ["25-12-2026"],
    "unavailable_days": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["night"],
    "max_tolerated_nights": 0,
    "max_tolerated_holidays": 0,
    "max_tolerated_weekends": 0,
    "max_tolerated_consecutive_demanding_shifts": null
  }}
}}

Example 3 (Explicit Numeric Limits + Night Preference):
Input: "Worker C: I prefer night shifts. I can tolerate at most 2 holiday shifts and up to 1 weekend shift."
Output:
{{
  "reasoning": "Worker C prefers night -> preferred_shifts ['night'], max_tolerated_nights null. Stated limits: 'at most 2 holiday shifts' -> max_tolerated_holidays 2; 'up to 1 weekend shift' -> max_tolerated_weekends 1. No disliked shifts. No specific dates.",
  "worker_id": "Worker C",
  "preferred_shifts": ["night"],
  "availability": {{
    "preferred_rest_days": [],
    "unavailable_days": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": [],
    "max_tolerated_nights": null,
    "max_tolerated_holidays": 2,
    "max_tolerated_weekends": 1,
    "max_tolerated_consecutive_demanding_shifts": null
  }}
}}
---
"""
    return ChatPromptTemplate.from_messages([
        SystemMessage(content=system_instructions),
        HumanMessagePromptTemplate.from_template("Extract constraints for this worker:\n{input_text}")
    ])


# -------------------------------------- SANITIZER POST-PARSING (RETE DI SICUREZZA) -----------------------------------------

def sanitize_parsed_preference(pref: WorkerPreference, raw_text: str) -> WorkerPreference:
    """
    Rete di sicurezza programmatica per la consistenza logica:
    - Risolve conflitti tra turni preferiti e sgraditi
    - Assicura che la preferenza notturna sia coerente con max_tolerated_nights
    - Assicura la mutua esclusione tra vincoli HARD (unavailable) e SOFT (preferred rest)
    - Normalizza il worker_id
    """
    tol = pref.shift_tolerance

    # 1. Risoluzione conflitti preferiti vs sgraditi:
    # Un turno preferito non può mai comparire tra i turni sgraditi
    for pref_st in pref.preferred_shifts:
        if pref_st in tol.disliked_shift_types:
            tol.disliked_shift_types = [s for s in tol.disliked_shift_types if s != pref_st]

    # 2. Coerenza turno notturno:
    prefers_night = ShiftType.NIGHT in pref.preferred_shifts
    if prefers_night:
        if tol.max_tolerated_nights == 0:
            tol.max_tolerated_nights = None
        if ShiftType.NIGHT in tol.disliked_shift_types:
            tol.disliked_shift_types = [s for s in tol.disliked_shift_types if s != ShiftType.NIGHT]
    elif ShiftType.NIGHT in tol.disliked_shift_types and tol.max_tolerated_nights is None:
        tol.max_tolerated_nights = 0

    # 3. Deduplicazione HARD > SOFT per le date:
    # Se una data è in unavailable_days (HARD), non può stare anche in preferred_rest_days (SOFT)
    if pref.availability.unavailable_days and pref.availability.preferred_rest_days:
        hard_set = set(pref.availability.unavailable_days)
        pref.availability.preferred_rest_days = [
            d for d in pref.availability.preferred_rest_days if d not in hard_set
        ]

    # 4. Normalizzazione worker_id (es. "19" -> "Worker 19")
    if pref.worker_id:
        wid_clean = pref.worker_id.strip()
        if wid_clean.isdigit():
            pref.worker_id = f"Worker {wid_clean}"
    else:
        pref.worker_id = ""

    pref.shift_tolerance = tol
    return pref


class WorkerAgent:
    """
    Agente di Estrazione Preferenze del Lavoratore (Stage 1).
    Utilizza un LLM con structured output per convertire il testo libero
    delle preferenze di un lavoratore in un FormalizedWorkerProfile.
    Supporta:
    - Cache persistente su disco per abbattere i tempi da 80s a <0.1s sui testi già analizzati.
    - Elaborazione sequenziale dei lavoratori (process_all).
    - Sanitizer post-parsing per correggere le allucinazioni più comuni dell'LLM.
    """
    def __init__(self, horizon: SchedulingHorizon, model_name: str = "llama3.2", temperature: float = 0.0):
        self.horizon = horizon
        self.model_name = model_name
        self.temperature = temperature
        self.llm = ChatOllama(
            model=model_name,
            temperature=temperature,
            num_ctx=4096
        ).with_structured_output(WorkerPreference, method="json_mode")
        self.prompt_template = build_prompt_template(horizon)

    def process_preference(self, text: str, worker_idx: int) -> FormalizedWorkerProfile:
        """
        Elabora il testo delle preferenze di un singolo lavoratore.
        Controlla prima la cache su disco: se presente restituisce subito il profilo,
        altrimenti invoca l'LLM, applica il sanitizer e salva il risultato in cache.
        """
        cached_data = get_cached_preference(text)
        if cached_data:
            parsed_pref = WorkerPreference.model_validate(cached_data)
            parsed_pref = sanitize_parsed_preference(parsed_pref, text)
            if not parsed_pref.worker_id:
                parsed_pref.worker_id = f"Worker {worker_idx + 1}"
            return FormalizedWorkerProfile(parsed_pref, worker_idx, self.horizon)

        prompt = self.prompt_template.invoke({"input_text": text})
        parsed_pref: WorkerPreference = self.llm.invoke(prompt)

        # Garanzia che worker_id sia sempre popolato
        if not parsed_pref.worker_id:
            parsed_pref.worker_id = f"Worker {worker_idx + 1}"

        # Sanitizer post-parsing: corregge allucinazioni LLM
        parsed_pref = sanitize_parsed_preference(parsed_pref, text)

        # Salva in cache (dopo sanitizzazione)
        set_cached_preference(text, parsed_pref.model_dump(mode="json"))

        return FormalizedWorkerProfile(parsed_pref, worker_idx, self.horizon)

    def process_all(
        self,
        lines: List[str],
        on_progress: Optional[Callable[[int, str, FormalizedWorkerProfile, bool], None]] = None
    ) -> List[FormalizedWorkerProfile]:
        """
        Elabora tutte le preferenze sequenzialmente sfruttando la cache.
        - on_progress: callback (worker_idx, text, profile, is_cached) invocata appena ciascun
                       lavoratore è pronto (utile per l'avanzamento GUI in tempo reale).
        Ritorna la lista dei FormalizedWorkerProfile nell'ordine originale degli indici.
        """
        results: List[FormalizedWorkerProfile] = []

        for idx, text in enumerate(lines):
            is_cached = bool(get_cached_preference(text))
            profile = self.process_preference(text, idx)
            results.append(profile)
            if on_progress:
                try:
                    on_progress(idx, text, profile, is_cached)
                except Exception:
                    pass

        return results


