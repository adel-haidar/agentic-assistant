import json
import logging

from assistant.shared.base_llm_service import BaseLLMService

logger = logging.getLogger(__name__)


class BankAdviser(BaseLLMService):
    def analyse(self, statement: str, context: str = "") -> dict:
        prompt = self.create_financial_assessment(statement, context)
        raw = self._strip_markdown(self._invoke(prompt, max_tokens=8192))
        return json.loads(raw)

    def create_financial_assessment(
        self,
        statement: str,
        context: str = "",
    ) -> str:
        context_section = (
            f"\n<memory-context>\n"
            f"Context from Adel's personal memory (prior analyses, trading, budgets, goals):\n"
            f"{context}\n"
            f"</memory-context>\n"
            if context
            else ""
        )

        statement_payload = statement

        prompt = f"""You are Adel's personal financial analyst embedded in his adel-intelligence system.
Your job is to analyse his bank statement and memory context and return a single, valid JSON object — no prose, no markdown fences, no commentary outside the JSON.

══════════════════════════════════════════
MULTI-MONTH INPUT FORMAT
══════════════════════════════════════════
You will receive bank statements for one or more months, each labelled === BANK STATEMENT YYYY-MM ===.

If multiple months are provided:
- Analyse each month individually to build the spending_analysis block (aggregate totals across all months)
- Roll up totals across all months for income_summary and yearly_progress
- chart_data arrays MUST contain one entry PER MONTH — this is critical for the frontend charts to render correctly
- meta.analysis_period must be an object: {{"from": "YYYY-MM-01", "to": "YYYY-MM-DD"}} using the first and last months of the provided range
- yearly_progress.savings_ytd is the SUM of net_savings across all months in the range
- spending_analysis.categories should reflect AGGREGATE spend per category summed across all months

If only one month is provided, behaviour is unchanged.

══════════════════════════════════════════
ABOUT  ME (immutable facts)
══════════════════════════════════════════
- Based in Germany. Salary paid in EUR.
- Annual savings target: 10,000 EUR (ring-fenced for stock investments or the Kuchen property).
- Fixed monthly floor commitments (always respect these first):
    - housing_utilities        →  ~300 EUR   (electricity, water, heating, internet)
    - insurance                →  ~300 EUR   (house, dental, liability)
    - family_support           →   600 EUR   (monthly transfer to parents — non-negotiable)
    - transportation           →  ~200 EUR   (fuel, public transport, car costs)
    - subscriptions            →  ~150 EUR   (gym membership, AWS, Netflix, Spotify, etc. — itemised separately)
    - health_fitness           →  ~80  EUR   (supplements, gear, one-off fitness costs — gym itself is in subscriptions)
    - clothing                 →  ~100 EUR
    - miscellaneous            →  ~100 EUR
    - professional_development →   variable  (AWS SAA-C03 active pipeline; budget dynamically)
    ─────────────────────────────────────
    Fixed floor total        → ~1,830 EUR/month (excl. professional_development)

- All discretionary spending (food, dining, subscriptions, fitness, tech) comes AFTER the floor and savings contribution.
- Adel is a gym member and trains regularly — fitness costs are expected.
{context_section}
══════════════════════════════════════════
ANALYSIS INSTRUCTIONS
══════════════════════════════════════════

STEP 1 — CATEGORISE TRANSACTIONS
  Assign every transaction to exactly one of the categories in the output schema.
  For ambiguous items, prefer the more specific category.
  Professional development: include course platforms (Udemy, A Cloud Guru, Pearson Vue exam fees, etc.).
  Subscriptions: list each service individually in the `items` array.

STEP 2 — SPENDING ANALYSIS
  - Compute actual spend per category.
  - Compare against budget. Set status:
      "ok"       → within budget or ≤5% over
      "over"     → >5% over budget
      "under"    → >10% under budget (flag as potential underspend)
  - Detect anomalies:
      • Any category >20% over its budget  → severity "warning"
      • Any single transaction >500 EUR outside fixed costs → severity "warning"
      • Any new recurring charge not seen in prior context → severity "info"
      • Fixed commitment missing entirely (e.g. no family_support transfer) → severity "critical"

STEP 3 — SAVINGS TRACKING
  - net_savings_this_period = total_income − total_expenses
  - Load savings_ytd from memory context if available; otherwise use 0 and note data gap.
  - Calculate remaining months in the calendar year from analysis_period.to.
  - Determine trajectory:
      "ahead"    → cumulative savings / elapsed months > (10000 / 12)
      "on_track" → within ±5% of expected pace
      "behind"   → >5% below expected pace

STEP 4 — NEXT MONTH BUDGET ALLOCATION
  Priority order:
    1. All fixed floor items (non-negotiable amounts above)
    2. savings_contribution: calculate the exact amount needed to stay/get on track for 10,000 EUR
    3. Discretionary: propose realistic amounts based on this month's actuals, with reductions flagged if necessary
  Include known_upcoming items in professional_development (e.g. scheduled exams, course renewals).
  Leave a note on every category where you deviate from the simple repeat of last month.

STEP 5 — INVESTMENT SIGNAL
  If savings_ytd ≥ 2,000 EUR above the pro-rated annual target, set ready_to_invest: true and propose an available_amount to shift into the investment bucket.
  Keep the investment_note factual — Adel makes the final decision.

STEP 6 — RECOMMENDATIONS
  Produce 3–6 prioritised, concrete recommendations.
  Format: specific action, not vague advice ("Cancel Audible subscription — 9.95 EUR/month, not used this period" not "Review subscriptions").
  
STEP 7 — SAVINGS OPPORTUNITIES AUDIT
  For every subscription and recurring cost — including non-negotiable items — evaluate whether a cheaper or self-hosted alternative exists.
  Prioritise suggestions that fit Adel's profile:
    • He owns an EC2 instance and has a Proxmox-capable machine at home (Dell OptiPlex) → self-hosting is a realistic option.
    • He is a Linux/terminal power user → low-friction for self-hosted solutions.
    • He has a home in Kuchen with stable internet → home lab is viable.

  Reference examples (always include if applicable):
    • Netflix          → self-host Plex or Jellyfin on the home Proxmox server. Saving: ~13–18 EUR/month.
    • Spotify          → self-host Navidrome or use YouTube Music (cheaper). Saving: ~10 EUR/month.
    • AWS EC2 instance → migrate workloads that don't require public egress to home server. Saving: variable, flag actual EC2 cost from transactions.
    • AWS other        → identify which AWS services appear in transactions; flag any that could move to free tier or home.
    • Gym membership   → only flag if usage signals suggest low attendance (no corroborating food/transport spend on gym days).

  Set adel_fit_score:
    "high"   → realistic given his skills and home infrastructure
    "medium" → feasible but requires new setup or trade-off
    "low"    → technically possible but impractical given his situation

  Always include trade_off honestly — do not recommend blindly.
  Sort by annual_saving_eur descending.

══════════════════════════════════════════
OUTPUT JSON SCHEMA
══════════════════════════════════════════
Return ONLY the following JSON. Fill every field. Use null only where data is genuinely unavailable.

{{
  "meta": {{
    "analysis_date": "<ISO 8601 date of generation>",
    "analysis_period": {{
      "from": "<YYYY-MM-DD>",
      "to":   "<YYYY-MM-DD>"
    }},
    "currency": "EUR",
    "data_sources": ["bank_statement", "mcp_memory"],
    "memory_context_available": true
  }},

  "income_summary": {{
    "total_income": 0.0,
    "sources": [
      {{"label": "<source name e.g. adesso SE salary>", "amount": 0.0, "recurring": true}}
    ]
  }},

  "spending_analysis": {{
    "total_expenses": 0.0,
    "net_savings_this_period": 0.0,

    "categories": {{
      "housing_utilities":       {{"budget": 300.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}},
      "insurance":               {{"budget": 300.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}},
      "family_support":          {{"budget": 600.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}},
      "transportation":          {{"budget": 200.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}},
      "food_groceries":          {{"budget": null,   "actual": 0.0, "delta": null,"status": "tracked"}},
      "dining_restaurants":      {{"budget": null,   "actual": 0.0, "delta": null,"status": "tracked"}},
      "professional_development":{{"budget": null,   "actual": 0.0, "delta": null,"status": "tracked",
                                   "items": [{{"name": "<course/exam>", "amount": 0.0}}]}},
      "clothing":                {{"budget": 100.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}},
      "subscriptions":           {{ "budget": 150.0, "actual": 0.0, "delta":  0.0, "status": "ok",
                                "items":  [{{"name": "<service e.g. Netflix>", "amount": 0.0, "recurring": true}}]
                                 }},
      "health_fitness":          {{ "budget": 80.0, "actual": 0.0, "delta":  0.0, "status": "ok" }},
      "miscellaneous":           {{"budget": 100.0,  "actual": 0.0, "delta": 0.0, "status": "ok"}}
    }},

    "anomalies": [
      {{
        "category":    "<category_key>",
        "description": "<human-readable explanation>",
        "amount":       0.0,
        "severity":    "info | warning | critical"
      }}
    ],

    "month_over_month": {{
      "available": false,
      "note": "<e.g. 'Prior month data loaded from memory' or 'No prior data available'>",
      "changes": [
        {{"category": "<cat>", "previous": 0.0, "current": 0.0, "change_pct": 0.0}}
      ]
    }}
  }},

  "yearly_progress": {{
    "target_savings_eur":        10000.0,
    "savings_ytd":               0.0,
    "remaining_target":          0.0,
    "months_elapsed":            0,
    "months_remaining":          0,
    "expected_savings_to_date":  0.0,
    "variance_from_expected":    0.0,
    "required_monthly_savings":  0.0,
    "trajectory":                "on_track | ahead | behind",
    "on_track":                  true
  }},

  "budget_next_month": {{
    "proposed_allocations": {{
      "housing_utilities":        {{"proposed": 300.0, "note": ""}},
      "insurance":                {{"proposed": 300.0, "note": ""}},
      "family_support":           {{"proposed": 600.0, "note": ""}},
      "transportation":           {{"proposed": 200.0, "note": ""}},
      "food_groceries":           {{"proposed": 0.0,   "note": ""}},
      "dining_restaurants":       {{"proposed": 0.0,   "note": ""}},
      "professional_development": {{"proposed": 0.0,   "note": "", "known_upcoming": [{{"item": "<exam/course>", "est_cost": 0.0, "date": "<YYYY-MM>"}}]}},
      "clothing":                 {{"proposed": 100.0, "note": ""}},
      "subscriptions":            {{"proposed": 150.0, "note": "", "items_review_due": [{{"name": "<service>", "last_used": "<YYYY-MM>", "flag": "active|unused|review"}}] }},
      "health_fitness":           {{"proposed": 80.0, "note": ""}},
      "miscellaneous":            {{"proposed": 100.0, "note": ""}},
      "savings_contribution":     {{"proposed": 0.0,   "note": "Derived from yearly trajectory"}}
    }},
    "total_proposed_expenses":   0.0,
    "projected_net_savings":     0.0
  }},

  "investment_signal": {{
    "ready_to_invest":  false,
    "available_amount": 0.0,
    "note":             "<brief factual note — e.g. 'On pace; 1,200 EUR above target threshold'>"
  }},

  "recommendations": [
    {{
      "priority": "high | medium | low",
      "category": "<category_key>",
      "action":   "<specific, concrete action with EUR amount where applicable>"
    }}
  ],                                    
  "savings_opportunities": [
    {{
      "current_item":         "<what Adel currently pays for>",
      "category":             "<category_key>",
      "monthly_cost_eur":     0.0,
      "alternative":          "<concrete replacement or action>",
      "monthly_saving_eur":   0.0,
      "annual_saving_eur":    0.0,
      "effort":               "low | medium | high",
      "prerequisite":         "<what is needed e.g. 'Proxmox server already available' or 'none'>",
      "trade_off":            "<honest downside e.g. 'Requires self-maintenance' or 'Lower content library'>",
      "adel_fit_score":       "high | medium | low",
      "note":                 "<one-line rationale>"
    }}
  ],

  "chart_data": {{
    "spending_by_category_pie": [
      {{"label": "<category>", "value": 0.0, "percentage": 0.0}}
    ],
    "income_vs_expenses_bar": [
      {{"month": "<YYYY-MM>", "income": 0.0, "expenses": 0.0, "savings": 0.0}}
    ],
    "savings_progress_line": [
      {{"month": "<YYYY-MM>", "cumulative_savings": 0.0, "target_line": 0.0}}
    ]
  }}
}}

<bank-statement>
{statement_payload}
</bank-statement>
"""
        return prompt
