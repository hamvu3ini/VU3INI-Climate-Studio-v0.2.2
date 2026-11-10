# VU3INI Climate Studio — Phase 1

Climate analysis for any place on Earth from Google Earth Engine, your Davis Vantage Pro2 weather station and NASA–IBM Prithvi AI models, with Word/PDF reports, preview and print.

**Personal, noncommercial use only.** The app runs on your own Google Earth Engine noncommercial project; you are responsible for your registration, for following Google's terms, and for how you use the results. Results are estimates, not official data.

## What Phase 1 includes

| Area | Status |
| --- | --- |
| Personal-use notice (first run, connect page, About, every report footer) | Done |
| Connect Earth Engine page: how-to steps, project ID check, Google sign-in, connection test | Done |
| Area: pick from list (FAO GAUL), search a place, draw, upload (Shapefile zip, KML/KMZ, GeoJSON, GeoPackage), coordinates | Done |
| Period: presets, From/To, season filter, baseline, dataset date checks | Done |
| Drought: VCI mapping (1), temporal classification with VCI or SPI (2), MODIS VCI/TCI/VHI (6) | Done, needs first live run |
| Rainfall: GFS forecast (8), SDII (10), anomaly (13), clustering by place or year (15) | Done, needs first live run |
| Davis Vantage Pro2: import (desktop .txt, WeatherLink.com .csv, wlkReader CSV), mapping wizard, units, QC, 11 reports, station vs satellite | Done; tested on synthetic files |
| Reports: preview, Word (.docx), PDF, print, logo, sections on/off, A4/Letter | Done, tested |
| AI: Explain panel (Ollama / Gemini / Claude); Prithvi flood pipeline | Done, needs first live run |
| Automatic CPU/GPU choice with speed test and out-of-memory fallback | Done, tested with simulated GPU |
| Interface translation framework (English now; 6 languages in Phase 4) | Done |

Numbers in brackets are the tutorial numbers from the original list.

## Install (Windows 10/11, no admin rights)

1. Unzip the folder anywhere, for example `D:\VU3INI Climate Studio`.
2. Double-click **`setup.bat`**. It downloads a private Python 3.12 into the folder, installs the packages, and installs PyTorch for CUDA 12.8 if it finds an NVIDIA GPU (needed by the RTX 5060), otherwise the CPU build. It ends by printing whether PyTorch can see the GPU.
3. Double-click **`VU3INI Climate Studio.bat`**. The app opens in your browser; a tray icon lets you reopen or quit it.

Your settings, database, cache, AI models, logs and reports are kept in `%APPDATA%\VU3INI Climate Studio\`, not in the program folder.

## First run

1. Accept the personal-use notice.
2. **Connect Earth Engine**: follow the six steps on the page, paste your project ID, click *Sign in with Google*, then *Test connection*.
3. **Settings → Earth Engine datasets → Check datasets**. This verifies every dataset ID and band against the live catalog. Fix anything marked NO in `core/datasets.py`.
4. **Settings → Station**: enter the station's latitude, longitude and elevation (needed for evapotranspiration and station vs satellite).
5. **Settings → AI** (optional): choose Ollama (free, local; install from ollama.com) or add a Gemini/Claude API key.

## Things to check on the first live run (could not be tested in the build environment)

The build environment had no access to Earth Engine, Hugging Face or a GPU. The local parts have unit tests; these need a first check on your PC:

- [ ] Each drought and rainfall analysis on its tutorial's own example area; compare with the tutorial video (Gate 1).
- [ ] GFS forecast: the code assumes `total_precipitation_surface` is accumulated from the run start and checks this automatically; confirm the 72-hour totals look sensible.
- [ ] SMAP and CHIRPS: use the newest versions shown by *Check datasets*.
- [ ] Prithvi: confirm the model name `ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11`, its licence, and that `terratorch.cli_tools.LightningInferenceModel` loads it with your TerraTorch version. The land-cover model entry is a placeholder until a suitable fine-tune is chosen.
- [ ] Davis import with **your real WeatherLink export** (send a sample file and the parser will be checked against it).
- [ ] Tutorial links 4, 7, 11 and 16 were read from a phone screenshot where capital I and lowercase l look the same; fix any that fail in `analyses/base.py`.

## How CPU/GPU selection works

Earth Engine computes in Google's cloud. Local work (Prithvi inference, XGBoost, local LLMs) asks `compute/device.py` for a device:

- GPU when CUDA works and free memory covers the task; small jobs stay on CPU because starting the GPU costs more than it saves.
- *Settings → Performance → Run speed test* times each device once; if the CPU turns out faster for a task, Auto uses the CPU.
- On a GPU out-of-memory error, the batch is halved twice, then the task moves to CPU. It never crashes.
- Settings lets you force CPU or GPU. The device used is written in every report's method note.
- An RTX 50-series card needs a PyTorch build for CUDA 12.8 or newer; the app detects an older build and explains how to fix it.

## Developer notes

```
app/        Solara pages and components (main.py = entry)
core/       settings, paths, Earth Engine client, datasets, area, period, result object
analyses/   one module per analysis group; each analysis has run(area, period, params) -> AnalysisResult
station/    Davis importer, QC, derived values, reports, satellite validation
ai/         Prithvi pipeline, Explain panel
compute/    CPU/GPU device manager
reports/    charts (Plotly in app, Matplotlib for files), map images, Word/PDF/HTML builder
i18n/       translation catalogues (en.json)
storage/    SQLite
tests/      pytest suite + synthetic WeatherLink sample generator
```

Run the tests: `.venv\Scripts\python -m pytest -q`

Every analysis can run without the interface, for example:

```python
from core import ee_client, area, period
import analyses
ee_client.initialize("your-project-id")
a = area.from_point(27.47, 94.91, 25)
p = period.check(period.preset("last3y"), ["modis_ndvi"])
res = analyses.REGISTRY["drought_vci"].run(a, p)
print(res.summary)
```

Owner: Sasanka Boruah (VU3INI) · hamvu3ini@gmail.com

## Building the Windows installer

The installer bundles its own Python 3.12 and every library, so the target PC needs nothing else. It installs for the current user without admin rights, adds Start-menu and desktop shortcuts, shows the personal-use notice as the licence page, and has a normal uninstaller (which asks whether to keep your data).

On a Windows 10/11 PC with internet, from this folder:

1. Double-click `packaging\build_installer.bat` (standard installer, about 250–350 MB). During installation it offers to download the AI components (PyTorch for the PC's NVIDIA GPU or CPU, and TerraTorch).
2. Or run `packaging\build_installer.bat full` in a Command Prompt for a fully offline installer that already contains the AI components (several GB, split into `setup.exe` plus `.bin` parts that must stay together).

The script downloads a standalone Python, installs the libraries, copies the app, runs the tests, installs Inno Setup with winget if needed, and writes the installer to `dist\VU3INI_Climate_Studio_Setup_v<version>.exe`. The first build takes 10–20 minutes.

After installing, the Start menu has: **VU3INI Climate Studio**, **VU3INI Climate Studio (debug)**, **Install or repair AI components**, and **Uninstall**. Settings > AI mapping components also has an install button.

## Publishing to GitHub

Double-click `PUBLISH_TO_GITHUB.BAT` in this folder. On the first run it:

1. installs Git and the GitHub CLI with winget if they are missing;
2. opens the browser so you can sign in to GitHub;
3. sets up the local repository (commit e-mail uses your private GitHub no-reply address);
4. asks for a short description of the update and commits it;
5. creates the repository (default name `vu3ini-climate-studio`, **private** unless you answer *y*) and pushes;
6. if `dist\VU3INI_Climate_Studio_Setup_v<version>.exe` exists, offers to create a GitHub Release with the installer attached.

Later runs commit and push your changes to the same repository. `.venv`, `python`, `tools`, `build`, `dist` and caches are never uploaded, and your settings, API keys and station data are kept in `%APPDATA%`, outside this folder.

## Version history

| Version | Changes |
| --- | --- |
| 0.2.2 | `PUBLISH_TO_GITHUB.BAT`: one-click publishing to GitHub with optional installer release |
| 0.2.1 | Installer build fixes: version read safely when the folder path has spaces; absolute paths for Inno Setup; Python not added to the user's PATH during the build; no deprecation warnings |
| 0.2.0 | Windows installer: bundled Python and libraries, Start-menu and desktop shortcuts, licence page with the personal-use notice, optional AI download during install, full offline build option, app icon, uninstaller that can keep or remove user data, "Install or repair AI components" shortcut and Settings button |
| 0.1.1 | Logo and app name in the top-left of every page; footer with version, owner and personal-use line; normal start uses a hidden console (fixes the start that closed immediately); error message box and server log; debug start file; setup.bat download fix |
| 0.1.0 | Phase 1 first build |
