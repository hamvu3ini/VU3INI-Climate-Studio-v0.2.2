"""Create synthetic WeatherLink exports for tests (desktop .txt and WeatherLink.com .csv)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent / "data"

R1 = ["", "", "Temp", "Hi", "Low", "Out", "Dew", "Wind", "Wind", "Wind", "Hi", "Hi", "Wind", "Heat", "THW", "THSW", "", "",
      "Rain", "Solar", "Solar", "Hi Solar", "UV", "UV", "Hi", "Heat", "Cool", "In", "In", "In", "In", "In", "In Air", "",
      "Soil", "Soil", "Wind", "Wind", "ISS", "Arc."]
R2 = ["Date", "Time", "Out", "Temp", "Temp", "Hum", "Pt.", "Speed", "Dir", "Run", "Speed", "Dir", "Chill", "Index", "Index",
      "Index", "Bar", "Rain", "Rate", "Rad.", "Energy", "Rad.", "Index", "Dose", "UV", "D-D", "D-D", "Temp", "Hum", "Dew",
      "Heat", "EMC", "Density", "ET", "Moist. 1", "Moist. 2", "Samp", "Tx", "Recept", "Int."]
DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def synth(days: int = 75, start: str = "2026-04-01", interval_min: int = 30, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=days * 24 * 60 // interval_min, freq=f"{interval_min}min")
    h = t.hour + t.minute / 60
    doy = t.day_of_year
    temp = 27 + 5 * np.sin((h - 9) / 24 * 2 * np.pi) + 0.03 * (doy - doy[0]) + rng.normal(0, 0.4, len(t))
    hum = np.clip(80 - 15 * np.sin((h - 9) / 24 * 2 * np.pi) + rng.normal(0, 3, len(t)), 30, 100)
    wind = np.clip(rng.gamma(2, 2.5, len(t)), 0, None)
    wdir = rng.choice(len(DIRS), len(t), p=np.array([3, 4, 8, 6, 4, 3, 2, 2, 2, 2, 3, 3, 3, 2, 2, 2]) / 51)
    rain = np.where(rng.random(len(t)) < 0.03, rng.gamma(1.2, 3, len(t)), 0.0)
    solar = np.clip(950 * np.sin((h - 6) / 12 * np.pi), 0, None) * rng.uniform(0.3, 1, len(t))
    uv = solar / 85
    bar = 1006 + 3 * np.sin(doy / 5) + rng.normal(0, 0.3, len(t))
    soil = np.clip(np.cumsum(np.where(rain > 0, -8, 0.15)) % 120, 0, 200)
    df = pd.DataFrame({"t": t, "temp": temp, "hum": hum, "wind": wind, "dir": [DIRS[i] for i in wdir], "rain": rain,
                       "solar": solar, "uv": uv, "bar": bar, "soil1": soil, "soil2": soil * 0.7})
    df.loc[100, "temp"] = 60.5  # a spike to be flagged
    return df


def write_desktop(df: pd.DataFrame, path: Path, interval_min: int = 30) -> Path:
    lines = ["\t".join(R1), "\t".join(R2)]
    for r in df.itertuples():
        tm = r.t.strftime("%I:%M").lstrip("0") + ("a" if r.t.hour < 12 else "p")
        if tm.startswith(":"):
            tm = "12" + tm
        dew = r.temp - (100 - r.hum) / 5
        vals = [r.t.strftime("%d/%m/%y"), tm, f"{r.temp:.1f}", f"{r.temp + 0.3:.1f}", f"{r.temp - 0.3:.1f}", f"{r.hum:.0f}",
                f"{dew:.1f}", f"{r.wind:.1f}", r.dir if r.wind >= 1 else "---", f"{r.wind * 0.5:.2f}", f"{r.wind * 1.8:.1f}",
                r.dir, f"{r.temp:.1f}", f"{r.temp + 1:.1f}", f"{r.temp + 1:.1f}", "---", f"{r.bar:.1f}", f"{r.rain:.1f}",
                f"{r.rain * 2:.1f}", f"{r.solar:.0f}", f"{r.solar * 0.0258:.2f}", f"{r.solar * 1.1:.0f}", f"{r.uv:.1f}", "0.00",
                f"{r.uv * 1.1:.1f}", "0.000", "0.091", "27.2", "70", "21.3", "28.9", "13.40", "1.1576", f"{r.solar / 1000 * 0.12:.2f}",
                f"{r.soil1:.0f}", f"{r.soil2:.0f}", "117", "1", "100.0", str(interval_min)]
        lines.append("\t".join(vals))
    path.write_text("\n".join(lines) + "\n", encoding="cp1252")
    return path


def write_cloud_csv(df: pd.DataFrame, path: Path) -> Path:
    out = pd.DataFrame({
        "Date & Time": df["t"].dt.strftime("%m/%d/%y %I:%M %p"),
        "Temp - °F": (df["temp"] * 9 / 5 + 32).round(1),
        "Hum - %": df["hum"].round(0),
        "Wind Speed - mph": (df["wind"] / 1.609344).round(1),
        "Wind Direction": df["dir"],
        "Barometer - in Hg": (df["bar"] / 33.8639).round(3),
        "Rain - in": (df["rain"] / 25.4).round(2),
        "Solar Rad - W/m^2": df["solar"].round(0),
        "UV Index": df["uv"].round(1),
        "ET - in": (df["solar"] / 1000 * 0.12 / 25.4).round(3),
        "Soil Moisture 1 - cb": df["soil1"].round(0),
    })
    text = "Premises Station\nData export\n" + out.to_csv(index=False)
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    HERE.mkdir(exist_ok=True)
    d = synth()
    write_desktop(d, HERE / "sample_weatherlink_desktop.txt")
    write_cloud_csv(d.iloc[:2000], HERE / "sample_weatherlink_cloud.csv")
    print("written")
