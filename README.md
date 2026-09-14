# SmartScheduler

An intelligent multi-agent system for automated nurse and hospital shift scheduling optimization, combining the semantic reasoning of **Large Language Models (LLMs)** with the mathematical rigor of **Constraint Programming (Google OR-Tools CP-SAT)**.

---

## 📌 Overview

**SmartScheduler** addresses the NP-hard *Nurse/Worker Scheduling Problem* by uniting:
1. **Natural Language Understanding**: Parses unstructured shift preferences, vacation requests, and date constraints directly from free text using a local LLM (`llama3.2` with LangChain + Ollama) backed by structured Pydantic schemas.
2. **Rigorous Combinatorial Optimization**: Formulates legal contractual constraints (*Hard Constraints*) and subjective worker preferences (*Soft Constraints*) into Google OR-Tools CP-SAT, ensuring 100% legal compliance, safety, and mathematical feasibility.
3. **Iterative Refinement & Fairness Balancing**: Dynamically redistributes burdensome shifts (night shifts, public holidays, weekends) and raises the minimum satisfaction floor to minimize disparity (measured via the Gini coefficient and guided by Leximin principles).
4. **Symbolic Infeasibility Diagnosis & Dynamic Replanning**: Automatically identifies mathematical bottlenecks when constraints conflict (*INFEASIBLE*), provides clear natural language explanations to the head nurse, and generates negotiated compromise proposals for affected staff.
5. **Modern Desktop Dashboard**: A dark-mode desktop GUI built with CustomTkinter featuring real-time node pipeline tracking, an interactive monthly schedule grid, fairness metric cards, and an interactive replanning suite.

---

## 🏗️ Multi-Agent Staged Architecture

The system operates as a cooperative sequential pipeline composed of specialized stages:

- **Stage 0 — Horizon & Configuration Management** (`calendar_manager.py`, `config_loader.py`):
  - Dynamically calculates the scheduling horizon, calendar weeks, and national public holidays (including mobile holidays like Easter and Easter Monday).
  - Loads and validates operational hospital department settings (`configs_case_a.txt`, `configs_case_b.txt`).
- **Stage 1 — Worker Preference Formalization Agent** (`worker_agent.py`, `models.py`):
  - Converts free-text preferences into strongly typed Pydantic profiles.
  - Features persistent disk-based caching (`.worker_cache.json`) for instant evaluations and a programmatic *Post-Parsing Sanitizer* to eliminate hallucinations and logical contradictions.
- **Stage 2 — Schedule Drafting Agent** (`drafting_agent.py`, `satisfaction_model.py`):
  - Compiles coverage rules, labor regulations, and worker satisfaction models into an initial CP-SAT problem.
  - Supports automatic export of the standalone mathematical model specification in pure Python.
- **Stage 3 — Symbolic Verification & Fairness Evaluation** (`verification_agent.py`):
  - Audits generated schedules against labor laws (36h weekly maximum, mandatory 2-day rest post-night, monthly shift totals, max 1 shift/day).
  - Computes distribution equity metrics: Gini Index, standard deviation, minimum and mean satisfaction.
- **Stage 4 — Iterative Refinement Agent** (`refinement_loop.py`):
  - Executes a targeted fairness optimization loop that raises the satisfaction of the least-favored workers without regressing existing guarantees.
- **Stage 5 — Infeasibility Diagnosis & Dynamic Replanning** (`infeasibility_agent.py`):
  - Performs root-cause conflict analysis on over-constrained models, generates empathetic explanations for the coordinator, and crafts compromise proposals.
- **GUI Application** (`gui.py`, `main.py`, `rendering.py`):
  - Provides a desktop interface with animated node statuses, real-time logging, interactive preference browsing, and visual schedule matrices.

---

## 📁 Repository Structure

```text
smartscheduler/
├── Stages/
│   ├── main.py                  # Primary entry point of the application
│   ├── gui.py                   # Modern dark-mode GUI (CustomTkinter)
│   ├── calendar_manager.py      # Horizon calendar, solar weeks, and holiday management
│   ├── config_loader.py         # Department configuration parser and validator
│   ├── worker_agent.py          # LLM preference extraction (LangChain / Ollama) + Cache
│   ├── drafting_agent.py        # CP-SAT constraint programming model and solver
│   ├── satisfaction_model.py    # Mathematical satisfaction objective functions
│   ├── verification_agent.py    # Symbolic hard constraint verification and fairness metrics
│   ├── refinement_loop.py       # Iterative fairness and leximin optimization loop
│   ├── infeasibility_agent.py   # Infeasibility diagnosis, LLM explanation, and replanning
│   ├── models.py                # Pydantic data schemas, enums, and shift constants
│   ├── rendering.py             # Rendering utilities and schedule popup window
│   ├── assets/                  # UI icons and visual assets
│   └── configs/                 # Operational department configuration files
│       ├── configs_case_a.txt   # Configuration for Use Case A (13 standard workers)
│       └── configs_case_b.txt   # Configuration for Use Case B (13 standard + 7 specialized)
├── assets/                      # Shared media and screenshots
├── deliverables/                # Formal project deliverables
│   └── example_output/          # Partial CP-SAT models and resulting schedules (CSV, TXT, JSON)
├── requirements.txt             # Python dependencies
└── .gitignore                   # Exclusions for cache files, virtual environments, and scratchpads
```

---

## 🚀 Installation & Requirements

### 1. Prerequisites
- **Python 3.10+** (Python 3.11 or 3.12 recommended)
- **Ollama** installed and running locally with the `llama3.2` model:
  ```bash
  ollama pull llama3.2
  ```

### 2. Install Dependencies
It is recommended to set up and activate a virtual environment:

```bash
# Create virtual environment
python -m venv .venv

# Activate on Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Activate on macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 💻 Running the Application

Launch the modern desktop application:

```bash
python Stages/main.py
```

or:

```bash
python Stages/gui.py
```

### Key GUI Features:
1. **Scenario & Input Selection**: Choose the Use Case (A or B) and browse for worker natural language preference files.
2. **Real-Time Agent Visualizer**: Track execution across all stages (Config, Workers LLM, Drafting CP-SAT, Verifier, Refinement Loop).
3. **Interactive Infeasibility Resolution**: Review symbolic diagnoses and trade-off proposals when conflicting constraints arise.
4. **Interactive Roster Matrix**: Monthly color-coded shift grid (`M` = Morning, `P` = Afternoon, `N` = Night, `-` = Rest; highlights holidays in orange and weekends in blue).
5. **Fairness Analytics Panel**: Real-time cards showing minimum satisfaction progression, Gini coefficient reduction, standard deviation, and individual staff workload distributions.
