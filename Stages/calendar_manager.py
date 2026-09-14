from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional
from models import DATE_FORMAT


#-------------------------------------- GESTIONE CALENDARIO E DATE --------------------------------------------------------

def generate_public_holidays(start_date: datetime, end_date: datetime) -> Set[str]:
    """
    Calcola automaticamente le festività nazionali (formato DD-MM-YYYY)
    per tutti gli anni compresi nell'orizzonte temporale [start_date, end_date].
    Include sia le festività fisse (Capodanno, Epifania, Liberazione, Festa del Lavoro,
    Festa della Repubblica, Ferragosto, Ognissanti, Immacolata, Natale, S. Stefano)
    sia quelle mobili (Pasqua e Pasquetta).
    """
    holidays = set()
    for year in range(start_date.year, end_date.year + 1):
        # Festività fisse
        fixed = [
            f"01-01-{year}",
            f"06-01-{year}",
            f"25-04-{year}",
            f"01-05-{year}",
            f"02-06-{year}",
            f"15-08-{year}",
            f"01-11-{year}",
            f"08-12-{year}",
            f"25-12-{year}",
            f"26-12-{year}",
        ]
        holidays.update(fixed)

        # Festività mobili: Pasqua e Pasquetta
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1

        easter = datetime(year, month, day)
        easter_monday = easter + timedelta(days=1)

        holidays.add(easter.strftime(DATE_FORMAT))
        holidays.add(easter_monday.strftime(DATE_FORMAT))

    return holidays


class SchedulingHorizon:
    """
    Gestisce dinamicamente il calendario e l'orizzonte temporale fornito in input.
    """
    def __init__(self, start_date_str: str, end_date_str: str):
        self.start_date = datetime.strptime(start_date_str, DATE_FORMAT)
        self.end_date = datetime.strptime(end_date_str, DATE_FORMAT)
        
        if self.end_date < self.start_date:
            raise ValueError("La data di fine non puo' essere antecedente alla data di inizio.")
            
        self.start_date_str = start_date_str
        self.end_date_str = end_date_str
        
        # Festività nazionali calcolate automaticamente per la finestra temporale
        self.public_holidays = generate_public_holidays(self.start_date, self.end_date)
        
        self.date_to_index: Dict[str, int] = {}
        self.index_to_date: Dict[int, str] = {}
        self.holiday_indices: Set[int] = set()         # festività nazionali
        self.weekend_indices: Set[int] = set()         # weekend
        
        self._build_calendar()

    def _build_calendar(self):
        curr = self.start_date
        idx = 0
        while curr <= self.end_date:
            d_str = curr.strftime(DATE_FORMAT)
            self.date_to_index[d_str] = idx
            self.index_to_date[idx] = d_str

            is_weekend = curr.weekday() in (5, 6)
            is_public_holiday = d_str in self.public_holidays

            # weekend_indices
            if is_weekend:
                self.weekend_indices.add(idx)
            # holiday_indices
            if is_public_holiday:
                self.holiday_indices.add(idx)

            curr += timedelta(days=1)
            idx += 1
            
        self.total_days = idx

    def is_date_in_horizon(self, date_str: str) -> bool:
        return date_str in self.date_to_index

    def get_calendar_weeks(self) -> List[List[int]]:
        """
        Ritorna la lista delle settimane di calendario (Lun-Dom).
        Ogni elemento e' una lista di indici dei giorni appartenenti a quella settimana.
        """
        weeks = []
        current_week = []
        
        curr = self.start_date
        idx = 0
        while curr <= self.end_date:
            current_week.append(idx)
            # Se è domenica (weekday == 6) o l'ultimo giorno dell'orizzonte, chiudi la settimana
            if curr.weekday() == 6 or curr == self.end_date:
                weeks.append(current_week)
                current_week = []
            curr += timedelta(days=1)
            idx += 1
            
        return weeks
