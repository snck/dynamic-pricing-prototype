# Demand Forecasting & Dynamic Pricing — Prototype

A rule-based pricing engine for a B2B food e-commerce platform (restaurant supply).
The script forecasts weekly demand per product from sales history, applies pricing
rules (market alignment, demand trend), and clamps every suggestion with guardrails
(margin floor, competitor ceiling, week-over-week stability). Each suggested price
comes with a plain-language reason a merchandiser can verify with a calculator.

Built with AI-assisted tooling as part of a product management assessment (Part 3,
Option A). The pricing logic, guardrails, and test scenarios are my design; see the
chat log for the full build process.

## Requirements

- Python 3.10+

## Run

From the project folder, create a virtual environment, install pandas, and run:

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install pandas
python pricing_engine.py
```

The script prints the results table and writes `pricing_output.csv`.

Returning in a new terminal? Just re-activate and run:

```bash
source venv/bin/activate        # Windows: venv\Scripts\activate
python pricing_engine.py
```

## Files

| File | Purpose |
|---|---|
| `pricing_engine.py` | Forecast + pricing rules + guardrails, all thresholds in the CONFIG block at the top |
| `products.csv` | Product master data: cost and current price per product |
| `weekly_data.csv` | 10 weeks of weekly sales and competitor prices per product |
| `pricing_output.csv` | Generated output: suggested price, change %, review flag, and reason per product |

## Mock data scenarios

The dataset contains 6 products, each engineered to exercise one code path:

| Product | Scenario |
|---|---|
| P001 | Priced >10% above competitor → market alignment rule (with a suppressed trend rule) |
| P002 | Demand trending up → +3% price increase |
| P003 | Demand trending down → −3% price decrease |
| P004 | Stable demand, competitive price → no change |
| P005 | Competitor priced below viable minimum → margin floor overrides, flagged for review |
| P006 | Far above market → alignment capped by the ±10% stability guardrail (two-stage reason) |

## Configuration

All thresholds live in the CONFIG block at the top of `pricing_engine.py` —
trend dead zone (±5%), adjustment step (±3%), margin floor (cost × 1.08),
competitor ceiling (× 1.15), stability band (±10%), review threshold (8%).
These are prototype placeholder values; in production they would be tuned
during an observation period against real data.

## Known limitations (deliberate prototype scope)

- No inventory or shelf-life data — the waste-reduction pricing channel is out of scope
- Single competitor price per product; no stockout flag (censored demand not handled)
- Simple moving-average forecast — no seasonality, holidays, or promotions
