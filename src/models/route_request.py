from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RouteRequest:
    start_text: str
    end_text: str
    algorithm: str
    fruit_type: str
    transport_mode: str
    depart_at: datetime
    load_ton: float
