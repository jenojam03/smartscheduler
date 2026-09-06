"""
SmartScheduler — Main Entry Point
Avvia l'interfaccia grafica moderna (CustomTkinter) per la pianificazione intelligente dei turni.
"""
import sys
from pathlib import Path

# Assicura che la directory Stages sia nel sys.path
STAGES_DIR = Path(__file__).resolve().parent
if str(STAGES_DIR) not in sys.path:
    sys.path.insert(0, str(STAGES_DIR))

from gui import launch_gui


def main():
    launch_gui()


if __name__ == "__main__":
    main()
