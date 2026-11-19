# ⚡ Sustainable Energy Digital Twin

An interactive Streamlit-based digital twin platform designed to model, monitor, and optimize energy consumption and sustainability performance.

## Project Structure

```text
├── app.py                      # Main Streamlit application
├── core/
│   ├── config.py              # Zone thresholds, tariff and carbon defaults
│   ├── energy.py              # Energy metrics and calculations
│   ├── ingest.py              # Automatic upload parsing and zone analysis
│   ├── input.py               # Manual zone input validation and snapshots
│   ├── twin.py                # Digital twin simulation models
│   ├── actions.py             # Action dispatch and control recommendations
│   └── recommender.py         # Sustainability recommendation engine
├── data/
│   └── sample_sensors.csv     # Sensor readings and telemetry data
├── docs                       # Feature specifications / task briefs
├── prompts/                   # Structured implementation prompts
├── requirements.txt           # Project dependencies
├── .env.example               # Template environment variables
├── .gitignore                 # Git ignore configuration
└── README.md                  # Project documentation
```

## Data Sources

Pick a data source in the sidebar:

1. **Sample dataset** — the bundled `data/sample_sensors.csv` demo.
2. **Manual zone input** — enter one current reading per fixed zone, with
   operator-supplied baselines, tariff-based cost KPIs and CSV export.
3. **Upload dataset** — upload a single **CSV, Excel or JSON** file. The backend
   detects the format, maps columns by name, classifies readings into the four
   fixed zones, applies per-zone thresholds and produces the full analysis
   automatically. No column mapping or configuration is required.

The upload pipeline flags records it cannot safely assign to a zone and excludes
them from analysis instead of guessing. Zone thresholds (baseline, normal range,
warning, critical and hourly energy limit) live in `core/config.py` and can be
tuned by developers without frontend changes. A short rolling median suppresses
isolated spikes so single bad readings do not raise alerts.

## Getting Started

### 1. Environment Setup

Create and activate a virtual environment:
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Unix/macOS:
source .venv/bin/activate
```

Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in any required API keys:
```bash
cp .env.example .env
```

### 3. Run the Application

Launch the Streamlit app:
```bash
streamlit run app.py
```
