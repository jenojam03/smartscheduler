from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Any
from enum import Enum
from datetime import datetime

DATE_FORMAT = "%d-%m-%Y"

# TURNI: definisce le tipologie di turni previste
class ShiftType(str, Enum):
    MORNING = "morning"       # Turno mattina (8:00 - 14:00)
    AFTERNOON = "afternoon"   # Turno pomeriggio (14:00 - 20:00)
    NIGHT = "night"           # Turno notte (20:00 - 8:00)


# MAPPA TURNI: converte ciascun tipo di turno nel rispettivo indice numerico (0, 1, 2)
SHIFT_MAP = {
    ShiftType.MORNING: 0,
    ShiftType.AFTERNOON: 1,
    ShiftType.NIGHT: 2,
}


class ShiftTolerance(BaseModel):
    """
    Serve per codificare la tolleranza dei lavoratori a turni faticosi
    """
    # 1. Tipi specifici di turno sgraditi (es. night, afternoon)
    disliked_shift_types: List[ShiftType] = Field(
        default_factory=list,
        description="Tipologie di turno sgradite verso cui si ha bassa tolleranza (es. night)"
    )
    # 2. Festività / Weekend
    max_tolerated_holidays: Optional[int] = Field(
        default=None,
        description="Massimo numero di turni festivi/weekend che il lavoratore tollera di svolgere nel mese."
        "Se questa soglia viene superata, ogni violazione genera una penalità progressiva."
        "None = nessun limite; 0 = non tollera nessun turno festivo/weekend."
    )
    # 3. Turni di notte
    max_tolerated_nights: Optional[int] = Field(
        default=None,
        description="Massimo numero di turni di notte che il lavoratore tollera di svolgere nel mese."
        "Se questa soglia viene superata, ogni violazione genera una penalità progressiva."
        "None = nessun limite; 0 = non tollera nessun turno di notte."
    )
    # 4. Pattern consecutivi impegnativi
    max_tolerated_consecutive_demanding_shifts: Optional[int] = Field(
        default=None,
        description=(
            "Massimo numero di turni stancanti (notti, festivi, weekend) consecutivi che il lavoratore tollera. "
            "Due turni stancanti sono 'consecutivi' se sono:"
            "- Due notti che cadono in giorni adiacenti nel calendario "
            "- Due weekend consecutivi"
            "- Due festivi non intermezzati da nessun altro giorno festivo (es. 26-12 e 01-01)"
            "Se la soglia viene superata, ogni coppia consecutiva extra genera una penalità progressiva."
            "None = nessun limite; 0 = non tollera nessuna sequenza stancante consecutiva."
        )
    )
    # 5. Coperture di emergenza
    emergency_coverage_limit: Optional[int] = Field(
        default=None,
        description="Numero massimo di coperture di emergenza che il lavoratore può fare nell'arco di tempo"
    )
    # 6. Campo aperto per ulteriori casistiche descritte testualmente
    other_undesirable_patterns: List[str] = Field(
        default_factory=list,
        description="Eventuali ulteriori vincoli o pattern sgraditi non coperti dai campi precedenti"
    )

    @model_validator(mode="before")
    @classmethod
    def handle_raw_disliked_shifts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            disliked = data.get("disliked_shift_types", [])
            if isinstance(disliked, list):
                # Se l'LLM ha inserito 'holiday' o 'weekend' tra i turni sgraditi,
                # lo converte in max_tolerated_holidays = 0 se non già impostato
                if any(str(x).lower().strip() in ("holiday", "holidays", "weekend", "weekends") for x in disliked):
                    if data.get("max_tolerated_holidays") is None:
                        data["max_tolerated_holidays"] = 0
                # Mantiene solo i turni di lavoro validi (morning, afternoon, night)
                data["disliked_shift_types"] = [
                    x for x in disliked if str(x).lower().strip() in ("morning", "afternoon", "night")
                ]
        return data


class AvailabilityConstraints(BaseModel):
    """
    Esprime le preferenze dei lavoratori riguardo disponibilità e riposo. Riguarda QUANDO il lavoratore
    può o non può lavorare su date specifiche
    """
    preferred_rest_days: List[str] = Field(
        default_factory=list,
        description="Lista di date (formato 'DD-MM-YYYY') in cui il lavoratore preferisce riposare"
    )
    unavailable_days: List[str] = Field(
        default_factory=list,
        description="Lista di date (formato 'DD-MM-YYYY') in cui il lavoratore non è affatto disponibile"
        "Questo vincolo e' trattato come un hard constraint."
    )
    # ESTENSIBILITÀ: per vincoli temporali/presenza atipici
    other_availability_constraints: List[str] = Field(
        default_factory=list,
        description="Eventuali vincoli di disponibilità non coperti dai campi precedenti"
    )

    # Classe utile alla conversione del formato data. Necessaria per garantire l'uniformità del formato utilizzato
    @field_validator("preferred_rest_days", "unavailable_days", mode="before")
    @classmethod
    def normalize_dates(cls, v):
        if not isinstance(v, list):
            return v
        normalized = []
        for item in v:
            # Prova a parsare formati comuni se l'LLM sbaglia leggermente
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(item).strip(), fmt)
                    normalized.append(dt.strftime(DATE_FORMAT))
                    break
                except ValueError:
                    continue
        return normalized


class WorkerPreference(BaseModel):
    """
    Raccoglie le informazioni, preferenze e tolleranze dei lavoratori riguardo disponibilità e riposo
    """
    # CHAIN OF THOUGHT / REASONING
    reasoning: Optional[str] = Field(
        default=None,
        description="Step-by-step reasoning and analysis of the worker input text before extracting the structured fields (Chain of Thought)."
    )

    worker_id: str = Field(
        default="", 
        description="Identificativo del lavoratore (es. 'Worker 1')"
    )

    # PREFERRED SHIFTS
    preferred_shifts: List[ShiftType] = Field(
        default_factory=list, 
        description="Lista dei turni esplicitamente preferiti (morning, afternoon, night)"
    )

    # AVAILABILITY CONSTRAINTS
    availability: AvailabilityConstraints = Field(
        default_factory=AvailabilityConstraints,
        description="Tutti i vincoli e preferenze di disponibilità e riposo"
    )

    # TOLERANCE TOWARDS UNDESIRABLE SHIFTS
    shift_tolerance: ShiftTolerance = Field(
        default_factory=ShiftTolerance,
        description="Tolleranza e preferenze verso turni o combinazioni sgradite"
    )