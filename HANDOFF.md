# Acme procurement hand-off

I prioritized Acme's three complaints and the optional approval limit: reliable quote intake, visibility of negotiated prices, and company-specific accounting categories.

## Delivered

- **Quote extraction:** structured, nullable output prevents missing tax IDs from failing extraction. Layout-preserving text and separate classification improve accuracy and speed. Arithmetic checks trigger one retry; remaining discrepancies are visible. Editing lines keeps shipping, tax and discounts; vendor alternatives remain available separately.
- **Negotiated prices:** lines show matching catalog articles, suppliers and prices. Results use the organization's own catalog and are saved for the buyer. Relevant edits invalidate suggestions; outdated responses cannot restore them. Suppliers are not switched automatically.
- **Booking rules:** Acme's cable ties, toner and small-subscription rules are seeded and admin-editable. Company-only groups provide Office Supplies without changing other tenants. Requesters can override the suggested group; repeated corrections help admins identify missing rules.
- **Approval bonus:** totals strictly below $2,000, including tax and shipping, are automatically approved. Edits crossing the limit update automatic decisions; buyer decisions remain intact.

## Why these choices

I kept the provided model and improved the pipeline: AI reads varied wording; code handles arithmetic, catalog boundaries and thresholds. Uncertainty stays visible. One retry balances reliability and latency. Tenant settings hold accounting policy; requests keep one vendor and one commodity group.

## Evidence and limits

The committed backend suite passes **74 tests**; the local suite passes **83** with quote fixtures. TypeScript checking and the production build pass. Ten temporary form regression scenarios passed. Live matching passed **14/14 cases** in the latest run. Earlier extraction measurements improved aggregate checks from **43% to 92–93%**. These small evaluations do not establish general accuracy.

Scans and image-only tax IDs need manual entry. Greenmantle's alternative can still require correction after a warning. Catalog matches do not prove exact specification, packaging or quantity eligibility; bundle replacement is unsupported. Booking rules remain editable and do not automatically classify manual requests. Existing requests are not retrospectively auto-approved.

## Next steps and running it

Confirm the $500 definition of a small subscription and Acme's override policy. Next I would tighten product identity checks, broaden matching evaluation, add image fallback and re-approval after material edits. Supplier enrichment is outside the brief and was left for later.

Copy `.env.example` to `.env`, add an OpenAI key for AI features, and run `docker compose up --build`. Demo accounts are in README. The detailed PR description covers implementation and trade-offs; quote fixtures and live scorecards remain local.
