import copy
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

from models import WorkerPreference, SHIFT_MAP, DATE_FORMAT
from calendar_manager import SchedulingHorizon
from worker_agent import FormalizedWorkerProfile
from config_loader import SchedulingConfig
from drafting_agent import ScheduleDraftingAgent


# =====================================================================
# 1. DIAGNOSI SIMBOLICA DELL'INFATTIBILITÀ (Infeasibility Diagnostician)
# =====================================================================

class InfeasibilityDiagnostician:
    """
    Agente di Diagnosi Simbolica dell'Infattibilità.
    Analizza matematicamente i profili dei lavoratori e i vincoli di configurazione
    per individuare la causa radice che rende il modello INFEASIBLE.
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
        self.num_standard = config.num_standard_workers
        self.num_specialized = config.num_specialized_workers
        self.specialized_indices = set(range(self.num_standard, self.num_workers))

    def diagnose(self) -> Dict[str, Any]:
        """
        Esegue un'analisi completa dei colli di bottiglia e restituisce un report strutturato.
        """
        bottlenecks = []
        conflicting_worker_ids = set()

        # -------------------------------------------------------------
        # A. Controllo Deficit di Personale Giornaliero (Staffing Deficit)
        # -------------------------------------------------------------
        num_shifts = self.config.num_shifts
        required_daily_tot = (
            (self.config.min_workers_per_shift + self.config.min_specialized_per_shift) * num_shifts
            if self.config.scenario_type == "B"
            else self.config.min_workers_per_shift * num_shifts
        )
        required_daily_spec = (
            self.config.min_specialized_per_shift * num_shifts
            if self.config.scenario_type == "B"
            else 0
        )

        for d in range(self.horizon.total_days):
            d_str = self.horizon.index_to_date[d]

            # Lavoratori indisponibili in questa data
            unavail_workers = [
                p for p in self.worker_profiles
                if d in p.hard_constraints["unavailable_day_indices"]
            ]
            unavail_count = len(unavail_workers)
            available_count = self.num_workers - unavail_count

            # Lavoratori specializzati (Scenario B)
            if self.config.scenario_type == "B":
                unavail_spec = [w for w in unavail_workers if w.worker_idx in self.specialized_indices]
                avail_spec_count = self.num_specialized - len(unavail_spec)

                if avail_spec_count < required_daily_spec:
                    spec_deficit = required_daily_spec - avail_spec_count
                    conflicting_ids = [w.worker_id for w in unavail_spec]
                    conflicting_worker_ids.update(conflicting_ids)
                    bottlenecks.append({
                        "type": "SPECIALIZED_STAFFING_DEFICIT",
                        "date": d_str,
                        "day_index": d,
                        "required": required_daily_spec,
                        "available": avail_spec_count,
                        "deficit": spec_deficit,
                        "conflicting_workers": conflicting_ids,
                        "detail": (
                            f"Nel giorno {d_str}, sono richiesti almeno {required_daily_spec} infermieri specializzati "
                            f"({self.config.min_specialized_per_shift} per ciascuno dei {num_shifts} turni), "
                            f"ma ne risultano disponibili solo {avail_spec_count} a causa di {len(unavail_spec)} indisponibilità."
                        )
                    })

            if available_count < required_daily_tot:
                tot_deficit = required_daily_tot - available_count
                conflicting_ids = [w.worker_id for w in unavail_workers]
                conflicting_worker_ids.update(conflicting_ids)
                bottlenecks.append({
                    "type": "TOTAL_STAFFING_DEFICIT",
                    "date": d_str,
                    "day_index": d,
                    "required": required_daily_tot,
                    "available": available_count,
                    "deficit": tot_deficit,
                    "conflicting_workers": conflicting_ids,
                    "detail": (
                        f"Nel giorno {d_str}, sono richiesti complessivamente almeno {required_daily_tot} operatori "
                        f"per coprire tutti i turni, ma ne risultano disponibili solo {available_count} "
                        f"(deficit di {tot_deficit} operatori). Lavoratori indisponibili: {', '.join(conflicting_ids)}."
                    )
                })

        # -------------------------------------------------------------
        # B. Controllo Sovraccarico Singolo Lavoratore (Over-constrained Worker)
        # -------------------------------------------------------------
        worker_capacity_issues = []
        exact_shifts = self.config.exact_monthly_equivalent_shifts
        for p in self.worker_profiles:
            unavail_count = len(p.hard_constraints["unavailable_day_indices"])
            avail_days = self.horizon.total_days - unavail_count
            if avail_days < exact_shifts:
                conflicting_worker_ids.add(p.worker_id)
                worker_capacity_issues.append({
                    "worker_id": p.worker_id,
                    "unavailable_days_count": unavail_count,
                    "available_days": avail_days,
                    "required_shifts": exact_shifts,
                    "detail": (
                        f"{p.worker_id} ha {unavail_count} giorni di indisponibilità su {self.horizon.total_days} totali. "
                        f"Con soli {avail_days} giorni disponibili è matematicamente impossibile completare "
                        f"il monte turni mensile obbligatorio di {exact_shifts} turni."
                    )
                })

        return {
            "is_infeasible": len(bottlenecks) > 0 or len(worker_capacity_issues) > 0,
            "bottlenecks": bottlenecks,
            "worker_capacity_issues": worker_capacity_issues,
            "conflicting_workers": sorted(list(conflicting_worker_ids)),
            "total_conflicts_count": len(bottlenecks) + len(worker_capacity_issues),
        }


# =====================================================================
# 2. SPIEGAZIONE IN LINGUAGGIO NATURALE CON CoT (Infeasibility Explainer)
# =====================================================================

class InfeasibilityExplainer:
    """
    Agente di Spiegazione in Linguaggio Naturale (Stage 2 Fallback).
    Utilizza un LLM con Chain of Thought per formulare una spiegazione empatica,
    chiara e dettagliata delle ragioni del fallimento per il coordinatore ospedaliero.
    """

    def __init__(self, model_name: str = "llama3.2", temperature: float = 0.0):
        self.model_name = model_name
        self.temperature = temperature
        try:
            self.llm = ChatOllama(model=model_name, temperature=temperature, num_ctx=2048)
        except Exception:
            self.llm = None

    def explain(self, diagnosis: Dict[str, Any]) -> str:
        """
        Genera una spiegazione discorsiva dei conflitti diagnosticati.
        """
        if not diagnosis["is_infeasible"]:
            return "Nessun conflitto strutturale rilevato. Il problema potrebbe essere dovuto a combinazioni complesse di vincoli di riposo."

        # Se Ollama non è disponibile, usa fallback rule-based deterministico
        if not self.llm:
            return self._rule_based_explanation(diagnosis)

        try:
            prompt = ChatPromptTemplate.from_messages([
                SystemMessage(content=(
                    "Sei un coordinatore sanitario esperto e mediatore di pianificazione turni ospedalieri.\n"
                    "L'algoritmo di ottimizzazione non è riuscito a generare il calendario perché le richieste "
                    "hanno reso il problema MATEMATICAMENTE INFATTIBILE (INFEASIBLE).\n"
                    "Il tuo compito è spiegare in modo chiaro, costruttivo e professionale il motivo esatto del blocco.\n\n"
                    "FORMATO DELLA RISPOSTA (in italiano):\n"
                    "1. Sintesi del problema principale (giorni critici e deficit di personale).\n"
                    "2. Dettaglio dei colli di bottiglia (norme di sicurezza violate vs presenze).\n"
                    "3. Infermieri interessati dal conflitto (senza colpevolizzare nessuno).\n"
                    "4. Proposta di mediazione consigliata."
                )),
                HumanMessage(content=f"Report di Diagnosi Simbolica:\n{diagnosis}")
            ])
            response = self.llm.invoke(prompt.format_messages())
            return response.content
        except Exception:
            return self._rule_based_explanation(diagnosis)

    def _rule_based_explanation(self, diagnosis: Dict[str, Any]) -> str:
        lines = [
            "==================================================================",
            "        ANALISI DI INFATTIBILITÀ (ROOT CAUSE EXPLANATION)",
            "==================================================================",
            "Lo scheduling non può essere generato nel rispetto di tutti i vincoli normativi.",
            ""
        ]

        if diagnosis["bottlenecks"]:
            lines.append("COLI DI BOTTIGLIA RILEVATI (Deficit di Copertura):")
            for b in diagnosis["bottlenecks"]:
                lines.append(f"  • Data critica: {b['date']}")
                lines.append(f"    - {b['detail']}")
                lines.append(f"    - Personale coinvolto: {', '.join(b['conflicting_workers'])}")
            lines.append("")

        if diagnosis["worker_capacity_issues"]:
            lines.append("PROBLEMI DI MONTE ORE INDIVIDUALE:")
            for w in diagnosis["worker_capacity_issues"]:
                lines.append(f"  • {w['worker_id']}: {w['detail']}")
            lines.append("")

        lines.append(f"Infermieri Interessati dalla Mediazione: {', '.join(diagnosis['conflicting_workers'])}")
        lines.append("==================================================================")
        return "\n".join(lines)


# =====================================================================
# 3. REPLANNING DINAMICO E NEGOZIAZIONE (Dynamic Replanning Agent)
# =====================================================================

class DynamicReplanningAgent:
    """
    Agente di Replanning Dinamico e Negoziazione con gli Infermieri.
    Se il piano iniziale è INFEASIBLE:
    1. Esegue la diagnosi simbolica.
    2. Spiega in linguaggio naturale il problema.
    3. Formula proposte eque di compromesso per gli infermieri coinvolti.
    4. Rilassa in modo mirato e minimale i vincoli critici (es. Hard Unavailable -> Soft Rest Day con bonus)
       per ottenere una soluzione FEASIBLE/OPTIMAL alternativa.
    """

    def __init__(
        self,
        horizon: SchedulingHorizon,
        worker_profiles: List[FormalizedWorkerProfile],
        config: SchedulingConfig,
        model_name: str = "llama3.2"
    ):
        self.horizon = horizon
        self.original_profiles = worker_profiles
        self.config = config
        self.diagnostician = InfeasibilityDiagnostician(horizon, worker_profiles, config)
        self.explainer = InfeasibilityExplainer(model_name=model_name)

    def formulate_negotiation_proposals(self, diagnosis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Genera proposte personalizzate per ciascun infermiere interessato dal conflitto.
        """
        proposals = []
        conflicting_workers = diagnosis.get("conflicting_workers", [])
        bottlenecks = diagnosis.get("bottlenecks", [])

        # Raccoglie per ogni lavoratore i giorni critici in cui ha dichiarato indisponibilità
        worker_critical_dates: Dict[str, List[str]] = {w: [] for w in conflicting_workers}
        for b in bottlenecks:
            for w_id in b.get("conflicting_workers", []):
                if b["date"] not in worker_critical_dates[w_id]:
                    worker_critical_dates[w_id].append(b["date"])

        for w_id, dates in worker_critical_dates.items():
            if not dates:
                continue
            dates_str = ", ".join(dates)
            proposal = {
                "worker_id": w_id,
                "critical_dates": dates,
                "proposal_title": f"Proposta di Compromesso per {w_id}",
                "proposal_message": (
                    f"Gentile {w_id}, per consentire la corretta copertura del reparto nella data {dates_str}, "
                    f"ti proponiamo di rendere flessibile la tua disponibilità. In cambio, il sistema ti assegnerà "
                    f"priorità assoluta per il riposo nelle festività successive e sui tuoi turni preferiti."
                ),
                "suggested_tradeoff": "Conversione dell'indisponibilità tassativa in preferenza soft ad alta priorità."
            }
            proposals.append(proposal)

        return proposals

    def create_relaxed_profiles(
        self,
        workers_to_relax: Optional[List[str]] = None
    ) -> List[FormalizedWorkerProfile]:
        """
        Crea una copia dei profili lavoratori dove le indisponibilità tassative (Hard)
        dei lavoratori specificati (o di tutti i lavoratori in conflitto) vengono convertite
        in preferenze di riposo (Soft Constraint), permettendo al solver di trovare una soluzione
        rispettando al massimo la preferenza.
        """
        relaxed_profiles = []
        target_workers = set(workers_to_relax) if workers_to_relax else set()

        for idx, original_profile in enumerate(self.original_profiles):
            raw_pref = original_profile.raw_preference
            w_id = original_profile.worker_id

            if not target_workers or w_id in target_workers:
                # Clona l'oggetto WorkerPreference
                pref_dict = raw_pref.model_dump()
                unavail = list(pref_dict.get("availability", {}).get("unavailable_days", []))
                rest = list(pref_dict.get("availability", {}).get("preferred_rest_days", []))

                # Sposta le indisponibilità tassative nei giorni di riposo preferiti (Soft)
                for d in unavail:
                    if d not in rest:
                        rest.append(d)

                pref_dict["availability"]["unavailable_days"] = []
                pref_dict["availability"]["preferred_rest_days"] = rest

                relaxed_pref = WorkerPreference(**pref_dict)
                relaxed_profile = FormalizedWorkerProfile(relaxed_pref, idx, self.horizon)
                relaxed_profiles.append(relaxed_profile)
            else:
                relaxed_profiles.append(original_profile)

        return relaxed_profiles

    def run_dynamic_replanning(self, time_limit_seconds: int = 30) -> Dict[str, Any]:
        """
        Esegue l'intero flusso di diagnosi, spiegazione, negoziazione e risoluzione alternativa.
        """
        print("\n" + "=" * 80)
        print("          AVVIO PROTOCOLLO DI REPLANNING DINAMICO E DIAGNOSTICA")
        print("=" * 80)

        # 1. Diagnosi Simbolica
        print("\n[DIAGNOSTICA] Analisi dei vincoli e colli di bottiglia...")
        diagnosis = self.diagnostician.diagnose()

        print(f"  • Conflitti rilevati   : {diagnosis['total_conflicts_count']}")
        print(f"  • Infermieri coinvolti : {', '.join(diagnosis['conflicting_workers']) if diagnosis['conflicting_workers'] else 'Nessuno'}")

        # 2. Spiegazione in Linguaggio Naturale
        print("\n[EXPLAINER] Generazione spiegazione in linguaggio naturale...")
        explanation = self.explainer.explain(diagnosis)
        print("\n" + explanation + "\n")

        # 3. Formulazione Proposte di Negoziazione
        print("[NEGOZIAZIONE] Formulazione proposte per gli infermieri interessati...")
        proposals = self.formulate_negotiation_proposals(diagnosis)
        for p in proposals:
            print(f"  -> [{p['worker_id']}]: {p['proposal_message']}")

        # 4. Replanning Simbolico: Risoluzione Modello Rilassato
        print("\n[REPLANNING] Applicazione del modello di compromesso (Hard Unavailability -> Prioritized Soft)...")
        relaxed_profiles = self.create_relaxed_profiles(workers_to_relax=diagnosis["conflicting_workers"])

        draft_agent_relaxed = ScheduleDraftingAgent(
            horizon=self.horizon,
            worker_profiles=relaxed_profiles,
            config=self.config
        )

        relaxed_draft = draft_agent_relaxed.solve_draft(time_limit_seconds=time_limit_seconds)
        print(f"[REPLANNING] Esito nuovo tentativo con proposta di compromesso: {relaxed_draft['status']}")

        return {
            "diagnosis": diagnosis,
            "explanation": explanation,
            "negotiation_proposals": proposals,
            "relaxed_profiles": relaxed_profiles,
            "relaxed_draft": relaxed_draft,
            "replanning_success": relaxed_draft["status"] in ("OPTIMAL", "FEASIBLE")
        }
