# SmartScheduler

Sistema multi-agente intelligente per la pianificazione, generazione e ottimizzazione automatica dei turni di lavoro, basato sull'integrazione sinergica tra **Modelli di Linguaggio (LLM)** e **Constraint Programming (Google OR-Tools CP-SAT)**.

---

## 📌 Panoramica

**SmartScheduler** risolve il problema complesso del *Nurse/Worker Scheduling Problem* combinando:
1. **Comprensione del linguaggio naturale**: interpretazione delle preferenze dei lavoratori espresse in linguaggio informale tramite LLM (LangChain + Ollama).
2. **Ottimizzazione matematica rigorosa**: formulazione di vincoli hard e soft tramite solver CP-SAT per garantire conformità normativa, sicurezza e turni equi.
3. **Ciclo di Refinement e Bilanciamento (Fairness)**: ridistribuzione automatica del carico di lavoro e dei turni gravosi (es. notturni e festivi) per massimizzare la soddisfazione globale e minimizzare la disparità (indice di Gini).
4. **Risoluzione dinamica delle infattibilità**: diagnosi simbolica delle cause di infeasibility e generazione guidata di proposte di rilassamento dei vincoli.
5. **Interfaccia Grafica Moderna**: UI dark-mode realizzata in CustomTkinter con visualizzazione tabellare interattiva, avanzamento pipeline in tempo reale e metriche di equità.

---

## 🏗️ Architettura Multi-Agente

Il sistema è strutturato come una pipeline cooperativa di agenti specializzati:

- **Worker Preference Formalization Agent** (`worker_agent.py`): Esegue il parsing delle preferenze non strutturate dei lavoratori, traducendole in oggetti fortemente tipizzati con validazione Pydantic e caching deterministico.
- **Schedule Drafting Agent** (`drafting_agent.py`): Genera la prima bozza fattibile del calendario mensile traducendo regole contrattuali e vincoli normativi in un modello CP-SAT.
- **Verification & Fairness Agent** (`verification_agent.py`): Verifica l'assenza di violazioni di vincoli hard e calcola metriche di equità (media, deviazione standard, distribuzione festivi/notti).
- **Schedule Refinement Agent** (`refinement_loop.py`): Ciclo iterativo che incrementa la soddisfazione minima dei lavoratori e livella il carico tra colleghi.
- **Infeasibility Diagnostician & Dynamic Replanning Agent** (`infeasibility_agent.py`): Identifica i colli di bottiglia matematici e sintetizza alternative di rilassamento comprensibili all'utente.
- **GUI Application** (`gui.py`): Dashboard interattiva per selezione dei file di configurazione, monitoraggio dell'esecuzione, ispezione della griglia turni e applicazione di trade-off.

---

## 📁 Struttura del Progetto

```
smartscheduler/
├── Stages/
│   ├── calendar_manager.py      # Gestione dell'orizzonte temporale e giorni festivi
│   ├── config_loader.py         # Caricamento e validazione delle configurazioni
│   ├── drafting_agent.py        # Generazione bozza con Google OR-Tools CP-SAT
│   ├── gui.py                   # Interfaccia grafica CustomTkinter
│   ├── infeasibility_agent.py   # Diagnostica simbolica e replanning
│   ├── models.py                # Modelli dati Pydantic e costanti turni
│   ├── refinement_loop.py       # Loop di ottimizzazione e massimizzazione fairness
│   ├── rendering.py             # Utility di rendering
│   ├── satisfaction_model.py    # Calcolo score di soddisfazione dei lavoratori
│   ├── verification_agent.py    # Verifica vincoli hard e metriche
│   ├── worker_agent.py          # Agente LangChain/Ollama per preferenze
│   ├── assets/                  # Icone applicative
│   └── configs/                 # Esempi di file di configurazione
├── assets/                      # Asset grafici
├── main.py                      # Entry point dell'applicazione
├── requirements.txt             # Dipendenze Python
└── .gitignore                   # Esclusioni per file temporanei e cache
```

---

## 🚀 Requisiti e Installazione

### 1. Prerequisiti
- **Python 3.10+**
- **Ollama** in esecuzione in locale (es. con modello `llama3` o `gemma2`) per la formalizzazione delle preferenze.

### 2. Installazione delle dipendenze
Clona il repository e installa i pacchetti richiesti:

```bash
pip install -r requirements.txt
```

---

## 💻 Avvio dell'Applicazione

Puoi avviare l'interfaccia grafica direttamente dalla radice del progetto:

```bash
python main.py
```

Oppure avviando direttamente il modulo della GUI:

```bash
python Stages/gui.py
```

Dall'interfaccia potrai:
1. Selezionare il file di configurazione del personale e dei turni (disponibili nella cartella `Stages/configs/`).
2. Avviare la pipeline e osservare in tempo reale lo stato degli agenti.
3. Ispezionare la matrice dei turni generata, con evidenziazione grafica dei turni (Mattina, Pomeriggio, Notte, Riposo).
4. Esaminare le metriche di equità e il punteggio di soddisfazione individuale di ciascun lavoratore.
