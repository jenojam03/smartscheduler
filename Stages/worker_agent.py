import json
import hashlib
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Optional, Callable, Tuple
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import SystemMessage
from models import WorkerPreference, ShiftType, DATE_FORMAT, SHIFT_MAP
from calendar_manager import SchedulingHorizon
from satisfaction_model import WorkerSatisfactionModel


# ==========================================
# GESTIONE CACHE PERSISTENTE PREFERENZE
# ==========================================
CACHE_FILE = Path(__file__).resolve().parent / ".worker_cache.json"
_cache_lock = threading.Lock()
_memory_cache: Optional[Dict[str, Any]] = None


def _get_cache_dict() -> Dict[str, Any]:
    global _memory_cache
    with _cache_lock:
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
    with _cache_lock:
        return cache.get(key)


def set_cached_preference(text: str, pref_dict: Dict[str, Any]):
    """Salva in modo thread-safe un profilo parsato nella cache su disco e in memoria."""
    key = hashlib.md5(text.strip().encode("utf-8")).hexdigest()
    cache = _get_cache_dict()
    with _cache_lock:
        cache[key] = pref_dict
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


class FormalizedWorkerProfile:
    """
    Rappresentazione machine-readable che separa:
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
        self.worker_id = wid
        self.worker_idx = worker_idx
        self.reasoning = pref.reasoning
        self.raw_preference = pref
        self.horizon = horizon
        self.satisfaction_model = WorkerSatisfactionModel(pref, worker_idx, horizon)
        
        
        self.hard_constraints: Dict[str, Any] = {
            "unavailable_day_indices": [
                horizon.date_to_index[d] for d in pref.availability.unavailable_days 
                if horizon.is_date_in_horizon(d) #solo date valide
            ],
            "max_emergency_coverage": pref.shift_tolerance.emergency_coverage_limit
        }
        
        self.soft_constraints: Dict[str, Any] = {
            "preferred_shift_indices": [SHIFT_MAP[s] for s in pref.preferred_shifts],
            "disliked_shift_indices": [SHIFT_MAP[s] for s in pref.shift_tolerance.disliked_shift_types],
            "preferred_rest_day_indices": [
                horizon.date_to_index[d] for d in pref.availability.preferred_rest_days 
                if horizon.is_date_in_horizon(d)
            ],
            "avoid_holiday_shifts": pref.shift_tolerance.max_tolerated_holidays == 0,
            "avoid_consecutive_demanding": pref.shift_tolerance.max_tolerated_consecutive_demanding_shifts == 0
        }

    def apply_hard_constraints_to_model(self, model, shifts):
        w = self.worker_idx
        for d in self.hard_constraints["unavailable_day_indices"]:
            for s in range(3):
                model.add(shifts[(w, d, s)] == 0)


# ==========================================
# AGENTE PER L'ESTRAZIONE PREFERENZE (STAGE 1)
# ==========================================

HOLIDAY_NAMES: Dict[str, str] = {
    "01-01": "Capodanno / New Year's Day",
    "06-01": "Epifania (Befana) / Epiphany",
    "25-04": "Festa della Liberazione / Liberation Day",
    "01-05": "Festa del Lavoro / Labor Day",
    "02-06": "Festa della Repubblica / Republic Day",
    "15-08": "Ferragosto / Assumption Day",
    "01-11": "Ognissanti (Tutti i Santi) / All Saints' Day",
    "08-12": "Immacolata Concezione / Immaculate Conception",
    "24-12": "Vigilia di Natale / Christmas Eve",
    "25-12": "Natale / Christmas Day",
    "26-12": "Santo Stefano / Boxing Day / St. Stephen's Day",
    "31-12": "San Silvestro (Vigilia di Capodanno) / New Year's Eve",
}


def format_holidays_for_prompt(horizon: SchedulingHorizon) -> str:
    """
    Costruisce una tabella testuale delle festività note che cadono nell'orizzonte corrente,
    permettendo all'LLM di mappare nomi (es. 'Natale', 'Santo Stefano') alla data esatta DD-MM-YYYY.
    """
    lines = []
    curr = horizon.start_date
    while curr <= horizon.end_date:
        d_str = curr.strftime(DATE_FORMAT)
        dm = curr.strftime("%d-%m")
        if dm in HOLIDAY_NAMES:
            lines.append(f"- {HOLIDAY_NAMES[dm]}: {d_str}")
        elif d_str in horizon.public_holidays:
            lines.append(f"- Public Holiday (Pasqua/Pasquetta): {d_str}")
        curr += timedelta(days=1)

    if not lines:
        return "- No specific public holidays in this horizon."
    return "\n".join(lines)


def build_prompt_template(horizon: SchedulingHorizon) -> ChatPromptTemplate:
    holidays_table = format_holidays_for_prompt(horizon)
    system_instructions = f"""You are an expert hospital scheduling assistant.
Your task is to extract worker shift preferences and constraints into the exact structured schema.

SCHEDULING HORIZON FOR THIS RUN:
- Start Date: {horizon.start_date_str}
- End Date: {horizon.end_date_str}
- Total Days: {horizon.total_days}
- All dates MUST strictly follow the 'DD-MM-YYYY' format.

CALENDAR & NAMED HOLIDAYS IN THIS HORIZON (LOOKUP TABLE):
{holidays_table}

═══════════════════════════════════════════
CRITICAL RULES (FOLLOW EXACTLY):
═══════════════════════════════════════════

RULE 1 - preferred_shifts:
ONLY include shift types explicitly marked with preference verbs (e.g. 'I prefer morning', 'I like afternoon').
If none stated, leave EMPTY ([]).

RULE 2 - DATE CLASSIFICATION (HARD vs SOFT) - READ CAREFULLY:
These two categories are MUTUALLY EXCLUSIVE for the same date:
  - 'unavailable_days' (HARD): ONLY for strict impossibility. Trigger words: 'cannot work', 'unavailable', 'impossible', 'completely unavailable'.
  - 'preferred_rest_days' (SOFT): For wishes and requests. Trigger words: 'I want a rest day', 'I request off', 'I would like off', 'I need as a rest day', 'I want ... off'.
  CRITICAL: 'I want a rest day on X', 'I request X off', 'I need X as a rest day' are ALL SOFT constraints -> preferred_rest_days. ONLY 'cannot work' / 'unavailable' / 'impossible' go to unavailable_days.

RULE 3 - NAMED HOLIDAYS RESOLUTION:
When a worker mentions a holiday by name (e.g. 'Natale', 'Christmas', 'Capodanno'), resolve it to the exact DD-MM-YYYY date using the lookup table above, then classify it per RULE 2.

RULE 4 - shift_tolerance FIELDS:
  - 'disliked_shift_types': ONLY from ['morning', 'afternoon', 'night']. NEVER put 'holiday' or 'weekend' here.
  - 'max_tolerated_nights': integer limit on night shifts. Use 0 ONLY if worker explicitly says they hate/avoid nights. null if no limit stated.
  - 'max_tolerated_holidays': integer limit on holiday/weekend shifts. Use 0 ONLY if worker explicitly says they hate/want to avoid holiday shifts. null if no limit stated.
  - 'max_tolerated_consecutive_demanding_shifts': integer limit on consecutive demanding shifts. null if not mentioned.

═══════════════════════════════════════════
ANTI-HALLUCINATION RULES (MANDATORY):
═══════════════════════════════════════════

AH-1: NEVER INVENT NUMERIC LIMITS.
  max_tolerated_nights and max_tolerated_holidays MUST be null unless the worker EXPLICITLY states a numeric limit using words like 'at most N', 'up to N', 'maximum N', 'no more than N', 'I can tolerate N'.
  Requesting a specific date off (e.g. 'I want a rest day on 24-12-2026') is ONLY a preferred_rest_day. It does NOT set any max_tolerated field.
  Having a date that falls on a holiday in unavailable_days does NOT set max_tolerated_holidays.

AH-2: NEVER CONTRADICT PREFERENCES.
  If a worker says 'I prefer night shifts', then night MUST NOT appear in disliked_shift_types, and max_tolerated_nights MUST NOT be 0.
  If a worker says 'I prefer morning shifts', then morning MUST NOT appear in disliked_shift_types.
  A preferred shift type can NEVER simultaneously be a disliked shift type.

AH-3: 'avoid holiday shifts' = max_tolerated_holidays = 0.
  When a worker says 'I want to avoid holiday shifts' or 'I avoid holiday shifts', set max_tolerated_holidays = 0.
  Do NOT put 'holiday' in disliked_shift_types (it is not a valid shift type).

AH-4: GENERAL AVAILABILITY IS NOT A CONSTRAINT.
  Statements like 'I can work weekends', 'I can manage holidays' are standard availability. Do NOT add them to any constraint field.

AH-5: ROOT JSON OBJECT MANDATE.
  You MUST always output a complete JSON object containing all root keys: 'reasoning', 'worker_id', 'preferred_shifts', 'availability', 'shift_tolerance'.

═══════════════════════════════════════════
CHAIN-OF-THOUGHT REASONING PROTOCOL:
═══════════════════════════════════════════
Before filling the structured fields, provide step-by-step reasoning in the 'reasoning' field:
- Step 1 (Identity & Preferences): Identify worker_id and any explicit preferred shifts.
- Step 2 (Dates): Classify each mentioned date as HARD (unavailable) or SOFT (preferred_rest). Use RULE 2 trigger words.
- Step 3 (Tolerances): For EACH tolerance field, ask: "Did the worker EXPLICITLY state a numeric limit or avoidance for this?" If NO -> null.
- Step 4 (Contradiction Check): Verify no preferred shift appears in disliked_shift_types. Verify no numeric limit was invented.

═══════════════════════════════════════════
FEW-SHOT EXAMPLES:
═══════════════════════════════════════════

Example 1 (Preferred shift + avoid another + rest day on a holiday date):
Input: "Worker A: I prefer morning shifts and absolutely avoid night shifts. I want a rest day on 24-12-2026."
Output:
{{
  "reasoning": "Step 1: Worker A prefers 'morning'. Step 2: 'I want a rest day on 24-12-2026' -> SOFT -> preferred_rest_days. Step 3: 'absolutely avoid night shifts' -> disliked ['night'], max_tolerated_nights 0. No mention of holiday limits -> max_tolerated_holidays null. No mention of consecutive limits -> null. Step 4: morning is preferred, not in disliked. OK.",
  "worker_id": "Worker A",
  "preferred_shifts": ["morning"],
  "availability": {{
    "preferred_rest_days": ["24-12-2026"],
    "unavailable_days": [],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["night"],
    "max_tolerated_nights": 0,
    "max_tolerated_holidays": null,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 2 (Prefers night + avoids another shift type):
Input: "Worker B: I prefer night shifts and I avoid morning shifts."
Output:
{{
  "reasoning": "Step 1: Worker B prefers 'night'. Step 2: No dates mentioned. Step 3: 'avoid morning shifts' -> disliked ['morning']. Worker PREFERS nights, so max_tolerated_nights must NOT be 0 -> null. No holiday mention -> null. Step 4: night is preferred and NOT in disliked. OK.",
  "worker_id": "Worker B",
  "preferred_shifts": ["night"],
  "availability": {{
    "preferred_rest_days": [],
    "unavailable_days": [],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["morning"],
    "max_tolerated_nights": null,
    "max_tolerated_holidays": null,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 3 (Explicit numeric tolerances):
Input: "Worker C: I prefer morning shifts. I can tolerate at most 3 night shifts and up to 2 holiday shifts."
Output:
{{
  "reasoning": "Step 1: Worker C prefers 'morning'. Step 2: No dates. Step 3: Explicit limits: 'at most 3 night shifts' -> max_tolerated_nights 3. 'up to 2 holiday shifts' -> max_tolerated_holidays 2. disliked_shift_types empty because tolerance is stated, not avoidance. Step 4: OK.",
  "worker_id": "Worker C",
  "preferred_shifts": ["morning"],
  "availability": {{
    "preferred_rest_days": [],
    "unavailable_days": [],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": [],
    "max_tolerated_nights": 3,
    "max_tolerated_holidays": 2,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 4 (Unavailable dates on holidays + avoid nights):
Input: "Worker D: I cannot work on 31-12-2026 and 01-01-2027. I avoid night shifts and I prefer morning shifts."
Output:
{{
  "reasoning": "Step 1: Worker D prefers 'morning'. Step 2: 'cannot work on 31-12-2026 and 01-01-2027' -> HARD -> unavailable_days. Step 3: 'avoid night shifts' -> disliked ['night'], max_tolerated_nights 0. Having holiday dates in unavailable_days does NOT set max_tolerated_holidays -> null. Step 4: morning is preferred, not in disliked. OK.",
  "worker_id": "Worker D",
  "preferred_shifts": ["morning"],
  "availability": {{
    "preferred_rest_days": [],
    "unavailable_days": ["31-12-2026", "01-01-2027"],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["night"],
    "max_tolerated_nights": 0,
    "max_tolerated_holidays": null,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 5 (Named holiday resolution + soft date):
Input: "Worker E: I prefer morning shifts. I want Natale off and I am completely unavailable on Capodanno."
Output:
{{
  "reasoning": "Step 1: Worker E prefers 'morning'. Step 2: 'I want Natale off' -> SOFT -> preferred_rest_days ['25-12-2026']. 'completely unavailable on Capodanno' -> HARD -> unavailable_days ['01-01-2027']. Step 3: No night/holiday/consecutive limits mentioned -> all null. Step 4: OK.",
  "worker_id": "Worker E",
  "preferred_shifts": ["morning"],
  "availability": {{
    "preferred_rest_days": ["25-12-2026"],
    "unavailable_days": ["01-01-2027"],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": [],
    "max_tolerated_nights": null,
    "max_tolerated_holidays": null,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 6 (Prefers night + avoid holidays):
Input: "Worker F: I prefer night shifts but want to avoid holiday shifts."
Output:
{{
  "reasoning": "Step 1: Worker F prefers 'night'. Step 2: No dates. Step 3: 'avoid holiday shifts' -> max_tolerated_holidays 0. Worker PREFERS nights -> max_tolerated_nights null, night NOT in disliked. Step 4: night is preferred, not disliked. OK.",
  "worker_id": "Worker F",
  "preferred_shifts": ["night"],
  "availability": {{
    "preferred_rest_days": [],
    "unavailable_days": [],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": [],
    "max_tolerated_nights": null,
    "max_tolerated_holidays": 0,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}

Example 7 (Avoid shift type + request date off as SOFT):
Input: "Worker G: I avoid morning shifts and I request 31-12-2026 off."
Output:
{{
  "reasoning": "Step 1: Worker G has no preferred shifts. Step 2: 'I request 31-12-2026 off' -> SOFT -> preferred_rest_days. Step 3: 'avoid morning shifts' -> disliked ['morning']. No night/holiday/consecutive limits -> all null. Step 4: OK.",
  "worker_id": "Worker G",
  "preferred_shifts": [],
  "availability": {{
    "preferred_rest_days": ["31-12-2026"],
    "unavailable_days": [],
    "other_availability_constraints": []
  }},
  "shift_tolerance": {{
    "disliked_shift_types": ["morning"],
    "max_tolerated_nights": null,
    "max_tolerated_holidays": null,
    "max_tolerated_consecutive_demanding_shifts": null,
    "emergency_coverage_limit": null,
    "other_undesirable_patterns": []
  }}
}}
---
"""
    return ChatPromptTemplate.from_messages([
        SystemMessage(content=system_instructions),
        HumanMessagePromptTemplate.from_template("Extract constraints for this worker:\n{input_text}")
    ])


# ==========================================
# SANITIZER POST-PARSING (RETE DI SICUREZZA)
# ==========================================
def sanitize_parsed_preference(pref: WorkerPreference, raw_text: str) -> WorkerPreference:
    """
    Rete di sicurezza programmatica che corregge le allucinazioni più comuni
    dell'LLM dopo il parsing. Applica le stesse regole anti-allucinazione
    definite nel prompt, ma in modo deterministico e infallibile.
    """
    text_lower = raw_text.lower()
    tol = pref.shift_tolerance

    # AH-1: Rimuove limiti numerici inventati per max_tolerated_nights
    # Se il testo non contiene parole chiave relative ai turni di notte
    # (escluso "prefer night" che è una preferenza positiva), il limite è inventato.
    night_keywords = ["night", "notte", "notti", "notturno", "notturni"]
    night_avoidance_keywords = ["avoid night", "hate night", "dislike night", "no night",
                                "avoid notte", "evitare notte", "odio notte"]
    night_limit_keywords = ["tolerate", "at most", "up to", "maximum", "no more than",
                            "massimo", "al massimo"]

    mentions_night = any(kw in text_lower for kw in night_keywords)
    avoids_night = any(kw in text_lower for kw in night_avoidance_keywords)
    has_night_limit = mentions_night and any(kw in text_lower for kw in night_limit_keywords)

    # Se il worker preferisce le notti, max_tolerated_nights non può essere 0
    prefers_night = ShiftType.NIGHT in pref.preferred_shifts
    if prefers_night:
        if tol.max_tolerated_nights is not None and tol.max_tolerated_nights == 0:
            tol.max_tolerated_nights = None
        # Notte non può essere nei turni sgraditi se è preferita
        if ShiftType.NIGHT in tol.disliked_shift_types:
            tol.disliked_shift_types = [s for s in tol.disliked_shift_types if s != ShiftType.NIGHT]

    # Se il testo non menziona un limite numerico esplicito per le notti, forza null
    if tol.max_tolerated_nights is not None and not avoids_night and not has_night_limit:
        tol.max_tolerated_nights = None

    # AH-1: Rimuove limiti numerici inventati per max_tolerated_holidays
    holiday_keywords = ["holiday", "festiv", "weekend", "vacanz"]
    holiday_avoidance_keywords = ["avoid holiday", "avoid festiv", "hate holiday",
                                  "no holiday", "evitare festiv", "avoid weekend"]
    holiday_limit_keywords = ["tolerate", "at most", "up to", "maximum", "no more than",
                              "massimo", "al massimo"]

    mentions_holiday = any(kw in text_lower for kw in holiday_keywords)
    avoids_holiday = any(kw in text_lower for kw in holiday_avoidance_keywords)
    has_holiday_limit = mentions_holiday and any(kw in text_lower for kw in holiday_limit_keywords)

    if tol.max_tolerated_holidays is not None and not avoids_holiday and not has_holiday_limit:
        tol.max_tolerated_holidays = None

    # AH-2: Rimuove contraddizioni preferiti/sgraditi per morning e afternoon
    prefers_morning = ShiftType.MORNING in pref.preferred_shifts
    prefers_afternoon = ShiftType.AFTERNOON in pref.preferred_shifts

    if prefers_morning and ShiftType.MORNING in tol.disliked_shift_types:
        tol.disliked_shift_types = [s for s in tol.disliked_shift_types if s != ShiftType.MORNING]
    if prefers_afternoon and ShiftType.AFTERNOON in tol.disliked_shift_types:
        tol.disliked_shift_types = [s for s in tol.disliked_shift_types if s != ShiftType.AFTERNOON]

    # AH-3: "avoid holiday shifts" -> max_tolerated_holidays = 0
    if avoids_holiday and tol.max_tolerated_holidays is None:
        tol.max_tolerated_holidays = 0

    # RULE 2: Corregge date "want/request/need" erroneamente messe in unavailable
    soft_keywords = ["want", "request", "would like", "prefer", "need as a rest",
                     "vorrei", "desidero", "richiedo"]
    hard_keywords = ["cannot", "unavailable", "impossible", "can't", "not available",
                     "non posso", "indisponibile", "impossibile"]

    # Se il testo contiene solo keyword "soft" per una data (e nessuna keyword "hard"),
    # sposta le date da unavailable a preferred_rest
    has_hard = any(kw in text_lower for kw in hard_keywords)
    has_soft = any(kw in text_lower for kw in soft_keywords)

    if has_soft and not has_hard and pref.availability.unavailable_days:
        # Tutte le date dovrebbero essere in preferred_rest_days
        for d in pref.availability.unavailable_days:
            if d not in pref.availability.preferred_rest_days:
                pref.availability.preferred_rest_days.append(d)
        pref.availability.unavailable_days = []

    # RULE 3: Deduplicazione HARD > SOFT
    # Se una data è in unavailable_days (HARD), non può stare anche in preferred_rest_days (SOFT).
    # Il vincolo HARD ha sempre la precedenza.
    if pref.availability.unavailable_days and pref.availability.preferred_rest_days:
        hard_set = set(pref.availability.unavailable_days)
        pref.availability.preferred_rest_days = [
            d for d in pref.availability.preferred_rest_days if d not in hard_set
        ]

    # RULE 4: Normalizzazione worker_id (es. "19" -> "Worker 19")
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
    - Riutilizzo della medesima istanza client Ollama per tutte le chiamate.
    - Elaborazione parallela concorrente tramite ThreadPoolExecutor (process_all).
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
            if not parsed_pref.worker_id:
                parsed_pref.worker_id = f"Worker {worker_idx + 1}"
            return FormalizedWorkerProfile(parsed_pref, worker_idx, self.horizon)

        prompt = self.prompt_template.invoke({"input_text": text})
        parsed_pref: WorkerPreference = self.llm.invoke(prompt)

        # Rilevamento risposta vuota/scheletro: se l'LLM ha restituito tutti i
        # campi ai default (nessun reasoning, nessun preferred_shift, nessun dato),
        # probabilmente il contesto è stato troncato. Riprova una volta con un
        # prompt semplificato (senza reasoning) come fallback.
        if self._is_empty_response(parsed_pref):
            import logging
            logging.warning(f"[WorkerAgent] Risposta vuota per Worker {worker_idx + 1}, retry con prompt semplificato...")
            parsed_pref = self._retry_with_simplified_prompt(text, worker_idx)

        # Garanzia che worker_id sia sempre popolato
        if not parsed_pref.worker_id:
            parsed_pref.worker_id = f"Worker {worker_idx + 1}"

        # Sanitizer post-parsing: corregge allucinazioni LLM
        parsed_pref = sanitize_parsed_preference(parsed_pref, text)

        # Salva in cache (dopo sanitizzazione)
        set_cached_preference(text, parsed_pref.model_dump(mode="json"))

        return FormalizedWorkerProfile(parsed_pref, worker_idx, self.horizon)

    @staticmethod
    def _is_empty_response(pref: WorkerPreference) -> bool:
        """Rileva se l'LLM ha restituito una risposta 'scheletro' con tutti i campi ai default."""
        has_no_reasoning = pref.reasoning is None or pref.reasoning.strip() == ""
        has_no_shifts = len(pref.preferred_shifts) == 0
        has_no_dates = (
            len(pref.availability.preferred_rest_days) == 0
            and len(pref.availability.unavailable_days) == 0
        )
        has_no_tolerance = (
            len(pref.shift_tolerance.disliked_shift_types) == 0
            and pref.shift_tolerance.max_tolerated_holidays is None
            and pref.shift_tolerance.max_tolerated_nights is None
        )
        return has_no_reasoning and has_no_shifts and has_no_dates and has_no_tolerance

    def _retry_with_simplified_prompt(self, text: str, worker_idx: int) -> WorkerPreference:
        """Fallback: prompt compatto senza few-shot examples per rientrare nel contesto."""
        compact_prompt = f"""Extract worker shift preferences into JSON with these exact keys:
- "reasoning": string with your step-by-step analysis
- "worker_id": string (e.g. "Worker 12")
- "preferred_shifts": list of "morning"/"afternoon"/"night" the worker PREFERS
- "availability": object with:
  - "preferred_rest_days": dates they WANT off ("I want", "I request", "I need as rest") in DD-MM-YYYY
  - "unavailable_days": dates they CANNOT work ("cannot", "unavailable", "impossible") in DD-MM-YYYY
  - "other_availability_constraints": list of strings
- "shift_tolerance": object with:
  - "disliked_shift_types": list of "morning"/"afternoon"/"night" they AVOID (NEVER put "holiday" here)
  - "max_tolerated_nights": integer or null (null if not explicitly limited)
  - "max_tolerated_holidays": integer or null (0 if they say "avoid holiday shifts", null otherwise)
  - "max_tolerated_consecutive_demanding_shifts": integer or null
  - "emergency_coverage_limit": integer or null
  - "other_undesirable_patterns": list of strings

RULES:
- "cannot work" / "unavailable" / "completely unavailable" -> unavailable_days (HARD)
- "I want a rest day" / "I request off" / "I need as a rest day" -> preferred_rest_days (SOFT)
- If worker prefers a shift type, it MUST NOT appear in disliked_shift_types
- Do NOT invent numeric limits. Only set max_tolerated_nights/holidays if explicitly stated.

Worker text: {text}"""
        retry_llm = ChatOllama(
            model=self.model_name,
            temperature=self.temperature,
            num_ctx=4096
        ).with_structured_output(WorkerPreference, method="json_mode")
        try:
            return retry_llm.invoke(compact_prompt)
        except Exception:
            # Ultimo fallback: restituisce un profilo con i campi estratti dal worker_id
            return WorkerPreference(worker_id=f"Worker {worker_idx + 1}")

    def process_all(
        self,
        lines: List[str],
        max_workers: int = 2,
        on_progress: Optional[Callable[[int, str, FormalizedWorkerProfile, bool], None]] = None
    ) -> List[FormalizedWorkerProfile]:
        """
        Elabora tutte le preferenze concorrentemente sfruttando ThreadPoolExecutor e la cache.
        - max_workers: numero di thread simultanei (default 2, ideale per CPU/Ollama locale).
        - on_progress: callback (worker_idx, text, profile, is_cached) invocata appena ciascun
                       lavoratore è pronto (utile per l'avanzamento GUI in tempo reale).
        Ritorna la lista dei FormalizedWorkerProfile rigorosamente nell'ordine originale degli indici.
        """
        results: List[Optional[FormalizedWorkerProfile]] = [None] * len(lines)

        def _task(idx: int, text: str) -> Tuple[int, str, FormalizedWorkerProfile, bool]:
            is_cached = bool(get_cached_preference(text))
            profile = self.process_preference(text, idx)
            return idx, text, profile, is_cached

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_task, idx, text) for idx, text in enumerate(lines)]
            for fut in as_completed(futures):
                idx, text, profile, is_cached = fut.result()
                results[idx] = profile
                if on_progress:
                    try:
                        on_progress(idx, text, profile, is_cached)
                    except Exception:
                        pass

        return [p for p in results if p is not None]


# Singleton / cache dell'agente di default per retro-compatibilità
_default_agent: Optional[WorkerAgent] = None
_default_agent_horizon: Optional[SchedulingHorizon] = None
_agent_init_lock = threading.Lock()


def get_default_agent(horizon: SchedulingHorizon) -> WorkerAgent:
    global _default_agent, _default_agent_horizon
    with _agent_init_lock:
        if _default_agent is None or _default_agent_horizon != horizon:
            _default_agent = WorkerAgent(horizon)
            _default_agent_horizon = horizon
        return _default_agent


def process_worker_preference(
    text: str,
    worker_idx: int,
    horizon: SchedulingHorizon,
    agent: Optional[WorkerAgent] = None
) -> FormalizedWorkerProfile:
    """
    Funzione helper per retro-compatibilità. Riusa automaticamente l'istanza singleton
    e sfrutta la cache per massimizzare la velocità.
    """
    if agent is None:
        agent = get_default_agent(horizon)
    return agent.process_preference(text, worker_idx)

