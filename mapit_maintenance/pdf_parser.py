from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


@dataclass(frozen=True)
class Trip:
    trip_number: int
    start_at: str
    end_at: str
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    distance_km: float
    duration_min: int
    source_pdf: str

    @property
    def trip_key(self) -> str:
        raw = "|".join([
            self.start_at,
            self.end_at,
            f"{self.start_lon:.6f}",
            f"{self.start_lat:.6f}",
            f"{self.end_lon:.6f}",
            f"{self.end_lat:.6f}",
            f"{self.distance_km:.3f}",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or " ").strip()


def parse_mapit_distance(km_str: str, m_str: str) -> float:
    km = float(km_str.replace(".", "").replace(",", "."))
    metres = float(m_str.replace(".", "").replace(",", "."))
    return km + metres / 1000.0


TRIP_RE = re.compile(
    r"Trayecto\s+(?P<num>\d+).*?"
    r"Inicio estimado\s+(?P<start>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?"
    r"Final\s+(?P<end>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?"
    r"Posici[oó]n inicial\s+(?P<slon>-?\d+(?:\.\d+)?)\s+(?P<slat>-?\d+(?:\.\d+)?).*?"
    r"Posici[oó]n final\s+(?P<elon>-?\d+(?:\.\d+)?)\s+(?P<elat>-?\d+(?:\.\d+)?).*?"
    r"Distancia recorrida\s+(?P<km>\d+(?:[\.,]\d+)?)\s+km\s+(?P<m>\d+(?:[\.,]\d+)?)\s+m.*?"
    r"Duraci[oó]n\s+(?:(?P<h>\d+)\s+h\s+)?(?P<min>\d+)\s+min",
    re.IGNORECASE | re.DOTALL,
)


def read_pdf_text(pdf_path: Path) -> str:
    try:
        result = subprocess.run(["pdftotext", str(pdf_path), "-"], check=True, capture_output=True, text=True, timeout=40)
        if result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if text.strip():
                return text
    except Exception:
        pass

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_pdf(pdf_path: Path) -> list[Trip]:
    norm = normalize_text(read_pdf_text(pdf_path))
    trips: list[Trip] = []
    for m in TRIP_RE.finditer(norm):
        start_at = datetime.strptime(m.group("start"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        end_at = datetime.strptime(m.group("end"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        trips.append(Trip(
            trip_number=int(m.group("num")),
            start_at=start_at,
            end_at=end_at,
            start_lon=float(m.group("slon")),
            start_lat=float(m.group("slat")),
            end_lon=float(m.group("elon")),
            end_lat=float(m.group("elat")),
            distance_km=parse_mapit_distance(m.group("km"), m.group("m")),
            duration_min=int(m.group("min")) + int(m.group("h") or 0) * 60,
            source_pdf=pdf_path.name,
        ))
    return trips
