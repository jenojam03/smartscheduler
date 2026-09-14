import sys
from pathlib import Path

STAGES_DIR = Path(__file__).resolve().parent
if str(STAGES_DIR) not in sys.path:
    sys.path.insert(0, str(STAGES_DIR))

if sys.platform.startswith("win"):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("unical.smartscheduler.hospital.scheduler.v1")
    except Exception:
        pass

from gui import launch_gui


def main():
    launch_gui()


if __name__ == "__main__":
    main()
