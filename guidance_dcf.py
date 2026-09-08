"""
Segments for the forward path (optional). Turns a management plan (segment Base → Target)
into a revenue-growth row that pre-fills the "Mein Pfad" table. Nothing else.

Excel sheet 'Guidance' (optional):
    Base Year | 2025
    Target Year | 2030
    ... any further key/value rows are ignored here ...
    Segment | Base | Target | Scalable | Ramp
    Gastro  | 54.3 | 57.6   | 0        |
    New Products | 0 | 216  | 1        | 0,5,21,53,100      (% of target per plan year)
"""
from dataclasses import dataclass
from typing import List, Optional
import numpy as np
import pandas as pd


@dataclass
class Segment:
    name: str
    base: float
    target: float
    scalable: bool = False              # does the "plan fulfilment" lever apply?
    ramp: Optional[List[float]] = None  # fraction of target per plan year; None = geometric (or linear from 0)


def segment_paths(segments: List[Segment], n_years: int, fulfilment: float = 1.0) -> pd.DataFrame:
    """Revenue per segment for plan years 1..n_years (+ 'Revenue' total). fulfilment scales scalable targets."""
    t = np.arange(1, n_years + 1); out = {}
    for s in segments:
        tgt = s.target * (fulfilment if s.scalable else 1.0)
        if s.ramp is not None and len(s.ramp) == n_years:
            path = tgt * np.asarray(s.ramp, dtype=float)
        elif s.base > 0 and tgt > 0:
            path = s.base * (tgt / s.base) ** (t / n_years)
        else:
            path = s.base + (tgt - s.base) * t / n_years
        out[s.name] = path
    df = pd.DataFrame(out, index=[f"Y{i}" for i in t]); df["Revenue"] = df.sum(axis=1)
    return df


def growth_row(segments: List[Segment], n_years: int, fulfilment: float = 1.0) -> List[float]:
    """Year-over-year growth of total segment revenue (what the path table needs)."""
    rev = segment_paths(segments, n_years, fulfilment)["Revenue"].to_numpy()
    base = sum(s.base for s in segments)
    prev = np.r_[base, rev[:-1]]
    return list(np.where(prev > 0, rev / np.where(prev > 0, prev, 1) - 1, 0.0))


def read_guidance_sheet(xl: pd.ExcelFile):
    """Returns {'base_year', 'target_year', 'segments': [Segment]} or None."""
    if "Guidance" not in xl.sheet_names: return None
    raw = pd.read_excel(xl, "Guidance", header=None); params = {}; hdr = None
    for i in range(len(raw)):
        k = str(raw.iloc[i, 0]).strip()
        if k.lower() == "segment": hdr = i; break
        v = raw.iloc[i, 1] if raw.shape[1] > 1 else None
        if k and k != "nan" and pd.notna(v):
            try: params[k] = float(v)
            except (TypeError, ValueError): pass
    segs = []
    if hdr is not None:
        tbl = pd.read_excel(xl, "Guidance", header=hdr).dropna(subset=["Segment"])
        for _, r in tbl.iterrows():
            if pd.isna(r.get("Base")) or pd.isna(r.get("Target")): continue
            rv = r.get("Ramp"); ramp = [float(x) / 100 for x in str(rv).replace(";", ",").split(",")] if pd.notna(rv) and str(rv).strip() else None
            sc = r.get("Scalable")
            sc = (float(sc) != 0) if isinstance(sc, (int, float)) and not pd.isna(sc) else str(sc).strip().lower() in ("1", "true", "yes", "ja", "x")
            segs.append(Segment(str(r["Segment"]), float(r["Base"]), float(r["Target"]), sc, ramp))
    if not segs: return None
    by = int(params.get("Base Year", 0)); ty = int(params.get("Target Year", 0))
    return {"base_year": by, "target_year": ty, "n_years": (ty - by) if ty > by else 5, "segments": segs,
            "fx": params.get("FX")}
