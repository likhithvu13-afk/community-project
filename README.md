# ⚡ Sustainable Energy Digital Twin

An interactive Streamlit-based digital twin platform designed to model, monitor, and optimize energy consumption and sustainability performance.

## Project Structure

```text
├── app.py                      # Main Streamlit application
├── core/
│   ├── config.py              # Configuration and environment settings
│   ├── energy.py              # Energy metrics and calculations
│   ├── twin.py                # Digital twin simulation models
│   ├── actions.py             # Action dispatch and control recommendations
│   └── recommender.py         # Sustainability recommendation engine
├── data/
│   └── sample_sensors.csv     # Sensor readings and telemetry data
├── requirements.txt           # Project dependencies
├── .env.example               # Template environment variables
├── .gitignore                 # Git ignore configuration
└── README.md                  # Project documentation
```

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
