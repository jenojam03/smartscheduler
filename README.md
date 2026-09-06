# SmartScheduler

An intelligent multi-agent system for automated worker and nurse scheduling optimization, combining the semantic reasoning of **Large Language Models (LLMs)** with the mathematical rigor of **Constraint Programming (Google OR-Tools CP-SAT)**.

---

## 📌 Overview

**SmartScheduler** solves complex instances of the *Nurse/Worker Scheduling Problem* by uniting:
1. **Natural Language Understanding**: Parses unstructured worker shift requests, preferences, and holiday constraints via LLMs (LangChain + Ollama).
2. **Rigorous Mathematical Optimization**: Formulates hard contractual constraints and soft preferences using Google OR-Tools CP-SAT to guarantee legality, safety, and operational feasibility.
3. **Iterative Refinement & Fairness Balancing**: Dynamically redistributes night shifts, holidays, and workload variance to maximize global satisfaction while minimizing disparity (Gini coefficient).
4. **Symbolic Infeasibility Diagnosis & Dynamic Replanning**: Pinpoints mathematical bottlenecks in conflicting constraints and generates actionable relaxation proposals.
5. **Modern Desktop Interface**: Dark-mode GUI built with CustomTkinter featuring an interactive monthly shift grid, real-time pipeline visualizer, fairness distribution charts, and tradeoff controls.

---

## 🏗️ Multi-Agent Architecture

The system operates as a cooperative pipeline of specialized agents:

- **Worker Preference Formalization Agent** (`worker_agent.py`): Translates informal shift requests into strongly-typed Pydantic profiles with deterministic hash caching.
- **Schedule Drafting Agent** (`drafting_agent.py`): Compiles coverage requirements, contractual limits, and worker preferences into a CP-SAT model to produce the initial feasible schedule.
- **Hard & Fairness Verification Agent** (`verification_agent.py`): Rigorously audits schedules against labor regulations (e.g., mandatory rest periods, max consecutive shifts) and computes fairness metrics.
- **Schedule Refinement Agent** (`refinement_loop.py`): Iteratively raises minimum satisfaction thresholds and re-balances burdensome shifts across staff.
- **Infeasibility Diagnostician & Dynamic Replanning Agent** (`infeasibility_agent.py`): Identifies unsatisfiable constraint combinations and synthesizes human-understandable relaxation alternatives.
- **GUI Dashboard** (`gui.py`): Real-time interactive UI for loading configurations, tracking execution, inspecting shift rosters, and fine-tuning schedules.

---

## 📁 Repository Structure

```
smartscheduler/
├── Stages/
│   ├── calendar_manager.py      # Scheduling horizon and holiday management
│   ├── config_loader.py         # Configuration file parsing and validation
│   ├── drafting_agent.py        # CP-SAT constraint programming solver
│   ├── gui.py                   # CustomTkinter modern dark-mode GUI
│   ├── infeasibility_agent.py   # Symbolic conflict diagnosis & replanning
│   ├── models.py                # Pydantic data schemas & shift constants
│   ├── refinement_loop.py       # Iterative fairness optimization loop
│   ├── rendering.py             # Schedule display utilities
│   ├── satisfaction_model.py    # Worker satisfaction scoring functions
│   ├── verification_agent.py    # Hard constraint and fairness validation
│   ├── worker_agent.py          # LLM preference extraction (LangChain / Ollama)
│   ├── assets/                  # UI icon assets
│   └── configs/                 # Sample operational configuration files
├── assets/                      # Shared graphical assets
├── main.py                      # Application root entry point
├── requirements.txt             # Python dependencies
└── .gitignore                   # Exclusions for temporary files and local caches
```

---

## 🚀 Requirements & Installation

### 1. Prerequisites
- **Python 3.10+**
- **Ollama** running locally (e.g., with `llama3` or `gemma2`) for natural language preference parsing.

### 2. Install Dependencies
Clone the repository and install the required packages:

```bash
pip install -r requirements.txt
```

---

## 💻 Running the Application

Launch the modern graphical interface directly from the repository root:

```bash
python main.py
```

Or run the GUI module directly:

```bash
python Stages/gui.py
```

### Key GUI Features:
1. **Config Selection**: Load operational parameters and worker files from `Stages/configs/`.
2. **Real-time Pipeline**: Monitor agent status, step-by-step logs, and execution time.
3. **Interactive Roster Grid**: View and inspect monthly assignments color-coded by shift type (Morning, Afternoon, Night, Off).
4. **Fairness Analytics**: Examine fairness indices, Gini coefficient improvements, and individual worker satisfaction breakdowns.
