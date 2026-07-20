"""
Demand Forecasting & Dynamic Pricing prototype.

Pipeline:
  1. Load products.csv + weekly_data.csv
  2. Forecast: trailing moving-average units + momentum trend_pct
  3. Pricing rules (top-down, first match wins; suppressed later-matching
     rule is noted for transparency)
  4. Guardrails, applied in this order: ceiling -> stability -> floor
     (floor is absolute and applied last, so it can override the others)
  5. Output one row per product

All thresholds are named constants below -- change them here, nothing
else in the script needs to change.
"""

import pandas as pd

# ============================================================================
# CONFIG -- all thresholds live here
# ============================================================================
MOVING_AVERAGE_WINDOW = 3      # weeks used for forecast_unit
MIN_WEEKS_FOR_TREND = 6        # need two non-overlapping 3-week windows

TREND_DEAD_ZONE = 0.05         # |trend_pct| <= this -> no trend-based rule fires
TREND_ADJUST_PCT = 0.03        # +/-3% price move on trend rules

ALIGNMENT_THRESHOLD = 0.10     # rule 1 fires if current_price > competitor * (1+this)
ALIGNMENT_TARGET_MARGIN = 0.10 # rule 1 target = competitor * (1+this)

CEILING_MULT = 1.15            # never above competitor_price * this
STABILITY_BAND = 0.10          # +/-10% of last week's price_charged
FLOOR_MULT = 1.08              # never below unit_cost * this (absolute)

REVIEW_CHANGE_THRESHOLD = 0.08 # flag_for_review if |change_pct| exceeds this

EPS = 1e-9                     # float-compare tolerance for "did a guardrail change the price"


# ============================================================================
# STEP 1: Load data
# ============================================================================
products_df = pd.read_csv("products.csv")
weekly_df = pd.read_csv("weekly_data.csv")
weekly_df["week_start_date"] = pd.to_datetime(weekly_df["week_start_date"])


# ============================================================================
# STEP 2: Forecast -- moving average + momentum trend
# ============================================================================
def compute_forecast(units: list[float]) -> tuple[float, float | None, int]:
    """Returns (forecast_unit, trend_pct or None, weeks_of_data_used)."""
    n = len(units)

    # forecast_unit: trailing 3-week average, or expanding average if <3 weeks
    if n >= MOVING_AVERAGE_WINDOW:
        forecast_unit = sum(units[-MOVING_AVERAGE_WINDOW:]) / MOVING_AVERAGE_WINDOW
    else:
        forecast_unit = sum(units) / n if n > 0 else None

    # trend_pct: recent 3wk avg vs prior 3wk avg, needs >= 6 weeks; else None
    if n >= MIN_WEEKS_FOR_TREND:
        recent3 = sum(units[-3:]) / 3
        prior3 = sum(units[-6:-3]) / 3
        trend_pct = (recent3 - prior3) / prior3 if prior3 != 0 else None
    else:
        trend_pct = None

    return forecast_unit, trend_pct, n


# ============================================================================
# STEP 3 + 4: Pricing rules + guardrails, per product
# ============================================================================
def process_product(product_row, weekly_rows) -> dict:
    pid = product_row["product_id"]
    unit_cost = product_row["unit_cost"]
    current_price = product_row["current_price"]

    weekly_rows = weekly_rows.sort_values("week_start_date")
    units = weekly_rows["units_sold"].tolist()

    forecast_unit, trend_pct, weeks_used = compute_forecast(units)

    latest_row = weekly_rows.iloc[-1]
    competitor_price = latest_row["competitor_price"]
    last_week_price = latest_row["price_charged"]

    has_competitor_price = pd.notna(competitor_price)

    # ---- RULES (top-down, first match wins) --------------------------------
    matched_rule = None
    suggested = current_price
    rule_note = ""
    suppressed_note = ""

    trend_up_would_fire = trend_pct is not None and trend_pct > TREND_DEAD_ZONE
    trend_down_would_fire = trend_pct is not None and trend_pct < -TREND_DEAD_ZONE

    # Rule 1: market alignment
    rule1_fires = has_competitor_price and current_price > competitor_price * (1 + ALIGNMENT_THRESHOLD)
    if rule1_fires:
        target = competitor_price * (1 + ALIGNMENT_TARGET_MARGIN)
        gap_pct = (current_price - competitor_price) / competitor_price * 100
        suggested = target
        matched_rule = "market_alignment"
        rule_note = (
            f"current price {gap_pct:+.1f}% vs competitor (> +{ALIGNMENT_THRESHOLD*100:.0f}% threshold) "
            f"-> market_alignment applied, target {target:.2f} (competitor x {1+ALIGNMENT_TARGET_MARGIN:.2f})"
        )
    elif not has_competitor_price:
        rule_note = "market_alignment skipped: no competitor price available; "

    # Rule 2: trend up
    if matched_rule is None and trend_up_would_fire:
        suggested = current_price * (1 + TREND_ADJUST_PCT)
        matched_rule = "trend_up"
        rule_note += (
            f"trend_pct {trend_pct*100:+.1f}% (> +{TREND_DEAD_ZONE*100:.0f}% dead zone) "
            f"-> trend_up applied, +{TREND_ADJUST_PCT*100:.0f}%"
        )
    elif matched_rule is not None and trend_up_would_fire and matched_rule != "trend_up":
        suppressed_note = (
            f"trend_up suppressed (trend_pct {trend_pct*100:+.1f}% would have applied "
            f"+{TREND_ADJUST_PCT*100:.0f}%)"
        )

    # Rule 3: trend down
    if matched_rule is None and trend_down_would_fire:
        suggested = current_price * (1 - TREND_ADJUST_PCT)
        matched_rule = "trend_down"
        rule_note += (
            f"trend_pct {trend_pct*100:+.1f}% (< -{TREND_DEAD_ZONE*100:.0f}% dead zone) "
            f"-> trend_down applied, -{TREND_ADJUST_PCT*100:.0f}%"
        )
    elif matched_rule is not None and trend_down_would_fire and matched_rule != "trend_down":
        suppressed_note = (
            f"trend_down suppressed (trend_pct {trend_pct*100:+.1f}% would have applied "
            f"-{TREND_ADJUST_PCT*100:.0f}%)"
        )

    # Rule 4: no match -> hold price
    if matched_rule is None:
        suggested = current_price
        matched_rule = "no_change"
        dz_pct = TREND_DEAD_ZONE * 100
        trend_desc = f"{trend_pct*100:+.1f}%" if trend_pct is not None else "n/a"
        rule_note += f"no rule matched (price within market band, trend_pct {trend_desc} within +/-{dz_pct:.0f}% dead zone) -> price held"

    # ---- GUARDRAILS: ceiling -> stability -> floor --------------------------
    guardrail_notes = []
    price = suggested

    # 1. Ceiling
    if has_competitor_price:
        ceiling_price = competitor_price * CEILING_MULT
        if price > ceiling_price + EPS:
            guardrail_notes.append(f"capped at ceiling {ceiling_price:.2f} (competitor x {CEILING_MULT})")
            price = ceiling_price
    else:
        ceiling_price = None

    # 2. Stability
    lower_band = last_week_price * (1 - STABILITY_BAND)
    upper_band = last_week_price * (1 + STABILITY_BAND)
    pre_stability_price = price
    if price > upper_band + EPS:
        price = upper_band
    elif price < lower_band - EPS:
        price = lower_band
    if abs(price - pre_stability_price) > EPS:
        guardrail_notes.append(
            f"stability band [{lower_band:.2f}, {upper_band:.2f}] (+/-{STABILITY_BAND*100:.0f}% of last week's "
            f"{last_week_price:.2f}) -> clamped to {price:.2f}"
        )

    # 3. Floor (absolute, applied last -- can override ceiling/stability)
    floor_price = unit_cost * FLOOR_MULT
    pre_floor_price = price
    floor_overrides = False
    if price < floor_price - EPS:
        price = floor_price
        floor_overrides = True
        conflict_note = ""
        if ceiling_price is not None and floor_price > ceiling_price + EPS:
            conflict_note = (
                f" (floor {floor_price:.2f} exceeds ceiling {ceiling_price:.2f} -- no single price satisfies "
                f"both; minimum-margin floor takes precedence)"
            )
        guardrail_notes.append(
            f"floor {floor_price:.2f} (unit_cost x {FLOOR_MULT}) enforced, overriding prior guardrail result "
            f"of {pre_floor_price:.2f}{conflict_note}"
        )

    # ---- Final price, rounded once at the very end -------------------------
    suggested_price = round(price)  # whole baht
    change_pct = (suggested_price - current_price) / current_price * 100

    flag_for_review = (abs(change_pct) > REVIEW_CHANGE_THRESHOLD * 100) or floor_overrides

    reason_parts = [p for p in [rule_note, suppressed_note] + guardrail_notes if p]
    reason = "; ".join(reason_parts)

    return {
        "product_id": pid,
        "product_name": product_row["product_name"],
        "forecast_unit": round(forecast_unit, 1) if forecast_unit is not None else None,
        "trend_pct": round(trend_pct * 100, 1) if trend_pct is not None else None,
        "weeks_of_data_used": weeks_used,
        "current_price": current_price,
        "suggested_price": suggested_price,
        "change_pct": round(change_pct, 1),
        "flag_for_review": flag_for_review,
        "reason": reason,
    }


# ============================================================================
# STEP 5: Run for all products
# ============================================================================
results = []
for _, prod in products_df.iterrows():
    prod_weeks = weekly_df[weekly_df["product_id"] == prod["product_id"]]
    results.append(process_product(prod, prod_weeks))

output_df = pd.DataFrame(results)
output_df.to_csv("pricing_output.csv", index=False)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", None)
print(output_df.to_string(index=False))
