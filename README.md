# grizzlyguy — grizzly_pricing

Collects resale listings of "grizzly"-style leather jackets (heavy leather, fur or shearling collar)
from every free source, extracts attributes, and fits a pricing model: % effect per attribute,
a quality-adjusted price index over time, and p10/p50/p90 fair-price bands for a given jacket.

**Status:** parsers and the model are tested only against synthetic data (`tests/`). The build
container had no network access to any marketplace, so none of the endpoints have been checked
live. Run the probe steps below first.

## Sources ($0)

| Source key | Access route | Sold history | What the price means | Needs |
|---|---|---|---|---|
| `ebay_sold` | HTML scrape of sold/completed search | ~90 days (eBay's limit) | sale price; list price when Best Offer accepted | none |
| Terapeak import | manual export from Seller Hub → Research | **up to 3 years** | sale price | free eBay account |
| `ebay_active` | official Browse API | active only | ask | free dev key |
| `grailed_sold` | Algolia index behind grailed.com search | **years** | actual sale price | none (key auto-found) |
| `grailed_active` | same | active only | ask | none |
| `poshmark_sold` | `/vm-rest/posts` JSON | **years** | last list price (accepted offers hidden, biased high) | none |
| `mercari_sold` | browser captures the site's GraphQL calls | months–years | sale price | Playwright |
| `depop` | webapi JSON, falls back to browser | thin sold coverage | ask / sale | none |
| `etsy_active` | official Open API v3 | active only (has `when_made` era field) | ask | free key |
| `vestiaire_sold` | search API | sold, mostly undated | sale price | none |
| `shopgoodwill_closed` | buyer API | ~60 days | auction hammer price | none |

Not available for free: The RealReal (hides sold prices), WorthPoint (paid; bans scraping),
eBay Marketplace Insights API (partner-only), eBay `findCompletedItems` (retired Feb 2025).

**Two years of eBay history at $0 comes only from Terapeak, by hand.** Everything else on eBay
reaches back 90 days. Running `collect` weekly builds the history forward from now.

Prior art (endpoint references, not dependencies): `incizzle/grailed-monitor` (Grailed Algolia),
`kcinnick/posh` (Poshmark), `adamgauthier/mercari-client`, `Gertje823/Vinted-Scraper` (Depop
API), `lancetm714/shopgoodwill-notifier`, `DannyRivasDev/Ebay-Sold-Listings-Scraper`. None of
them aggregates across sites or models prices.

## Setup

Run from a home connection, not a cloud server: bot walls are much stricter on datacenter IPs.

```bash
pip install -r grizzly_pricing/requirements.txt
playwright install chromium
export EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=...   # optional, developer.ebay.com
export ETSY_API_KEY=...                            # optional, etsy.com/developers
```

## Workflow

```bash
# 1. What's reachable, and do the parsers still match the live payloads?
python -m grizzly_pricing probe connectivity
python -m grizzly_pricing probe sample --sources all
#    0% coverage on a field means the field name changed. The raw JSON in
#    grizzly_pricing/data/probe/<source>.json shows the current name. Add it to that source's dig(...) list.

# 2. If a source is dead, find its current internal API:
python -m grizzly_pricing --headful probe discover --url "https://www.depop.com/search/?q=leather+fur+collar"

# 3. Collect everything (hours; rate-limited to 2-5s between requests).
python -m grizzly_pricing collect --sources all
python -m grizzly_pricing --headful collect --sources mercari_sold   # if bot-walled

# 4. eBay 2-year backfill: export Terapeak results to CSV (steps in sources/terapeak.py).
python -m grizzly_pricing import-terapeak ~/Downloads/terapeak_fur_collar.csv --query "leather jacket fur collar"

# 5. Fit and report.
python -m grizzly_pricing train        # -> data/report/report.md, effects.csv, price_index.csv, review_sample.csv
python -m grizzly_pricing estimate "Vintage 80s Schott leather bomber raccoon collar 42" --source ebay
```

Weekly accumulation (crontab): `0 3 * * 1 cd /path/to/repo && python -m grizzly_pricing collect`

## Attributes extracted (`features.py`)

brand (site field first, then a regex list of ~55 brands), decade (explicit year, "80s",
"eighties", Etsy `when_made`), a vintage keyword flag, leather type, collar material (raccoon / fox / coyote /
mink / mouton / shearling / faux / unspecified fur), style (bomber, car coat, long coat, trucker,
parka, moto, varsity), pattern (distressed, two-tone, quilted, embossed, patchwork, stripes,
embroidered), color, country of manufacture, zip-out liner, condition, size (letter or chest
number), gender, whether "grizzly" appears in the text, relevance, cross-post duplicate.

## Model (`model.py`)

- **Hedonic ridge** on log(price): each attribute's % effect vs the average level of that
  attribute, holding everything else fixed. Month dummies give a quality-adjusted price index.
  Source is a control, so Poshmark's list-price bias stays out of the attribute effects.
- **Quantile gradient boosting** (p10/p50/p90), trained on prices with the time trend removed
  and quoted at the latest month's level.
- **Validation:** time split (oldest 80% train, newest 20% test), compared with the median of the
  last 3 months. On synthetic data with known effects, the hedonic model's error is 10% vs the
  baseline's 17%, and it recovers the planted brand premium and monthly trend.
- Refuses to train below 60 usable sold rows. Rare categories (fewer than max(5, n/200) rows)
  are pooled.

## Premortem (most likely failures first)

1. **Endpoints or field names have drifted** since they were written. None were testable live.
   Mitigations: the `probe sample` coverage check, multi-name `dig()` lookups, browser
   capture that reads whatever the frontend calls, and `probe discover` to re-find APIs.
2. **Bot walls** (eBay Akamai, Cloudflare on Depop and Mercari, Poshmark rate limits) block plain
   HTTP clients. Mitigations: 2–5s pacing, `--headful` browser so you can solve captchas,
   home IP. A site may still block an IP for a while after a large run. Don't collect while
   logged into accounts you care about.
3. **Relevance noise.** Fur-collar searches return faux leather, women's coats and non-jackets,
   and the model learns from whatever gets through. Check `review_sample.csv` by hand once.
   If more than ~10% of the rows marked relevant are wrong, tighten `STYLE_CORE` / `EXCLUDE`.
4. **History is lopsided.** Grailed and Poshmark reach back years; eBay reaches back 90 days
   unless Terapeak is imported. The source control absorbs level differences, but early index
   months rest on fewer sources. Read `n_sales` next to each index value.
5. **Sold price ≠ transaction price** on Poshmark and on eBay Best Offer sales. Flagged as
   `price_is_actual`. The source control corrects the average bias, not the per-item error.
6. **Sparse data.** Few rows per brand gives noisy effects. `effects.csv` reports `n` for each
   level; ignore levels with n below ~15.
7. **Cross-post dedupe misses** listings whose title was edited between sites, which inflates
   counts somewhat.
