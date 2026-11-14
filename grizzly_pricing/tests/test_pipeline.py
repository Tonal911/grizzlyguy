"""Fixtures are synthetic, built from the documented/observed shapes of each site. They pin
parser behavior; they don't prove the live payloads still match. `probe sample` checks that."""
import numpy as np
import pandas as pd
import pytest

from grizzly_pricing.features import build_frame, extract
from grizzly_pricing.model import fit
from grizzly_pricing.schema import Listing, to_iso_date
from grizzly_pricing.sources import ebay, grailed, mercari, poshmark, shopgoodwill
from grizzly_pricing.sources.terapeak import import_file
from grizzly_pricing.store import Store

EBAY_HTML = """
<ul class="srp-results">
 <li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/123456789012?hash=x">
   <div class="s-item__title"><span>New Listing Vintage 80s Wilsons Leather Bomber Raccoon Fur Collar L</span></div></a>
   <span class="s-item__caption--signal POSITIVE">Sold  Oct 3, 2026</span>
   <span class="SECONDARY_INFO">Pre-Owned</span><span class="s-item__price">$145.00</span>
   <span class="s-item__shipping">+$18.50 shipping</span></li>
 <li class="s-card"><a class="su-card-container__header" href="https://www.ebay.com/itm/Grizzly-Jacket/223344556677">
   <div class="s-card__title">Grizzly Leather Jacket Shearling Collar Men's XL</div></a>
   <div class="s-card__caption">Sold Sep 28, 2026</div><span class="s-card__price">$210.00</span>
   <span>Best offer accepted</span><span>Free delivery</span></li>
 <li class="s-item"><a href="https://www.ebay.com/itm/999999999999"><div class="s-item__title">Shop on eBay</div></a></li>
</ul>"""


def test_ebay_sold_both_layouts():
    items = ebay.parse_sold_page(EBAY_HTML)
    assert [i.source_id for i in items] == ["123456789012", "223344556677"]
    a, b = items
    assert (a.price, a.shipping, a.sold_at, a.is_sold) == (145.0, 18.5, "2026-10-03", True)
    assert not a.title.startswith("New Listing")
    assert b.sold_at == "2026-09-28" and b.price_is_actual is False and b.shipping == 0.0


def test_grailed_hit():
    h = {"id": 55, "title": "Schott shearling collar leather jacket", "designer_names": "Schott NYC",
         "sold_price": 320, "price": 380, "sold_at": "2025-03-01T10:00:00Z", "size": "40", "condition": "is_gently_used"}
    l = grailed.hit_to_listing(h, True, "q")
    assert (l.price, l.sold_at, l.brand, l.price_is_actual) == (320.0, "2025-03-01", "Schott NYC", True)


def test_poshmark_item():
    p = {"id": "abc", "title": "Leather coat fur collar", "price_amount": {"val": "89", "currency_code": "USD"},
         "inventory": {"status": "sold_out", "status_changed_at": "2025-06-02T00:00:00-07:00"}, "brand": "Wilsons Leather"}
    l = poshmark.item_to_listing(p, None)
    assert l.is_sold and l.price == 89.0 and l.sold_at == "2025-06-02" and l.price_is_actual is False


def test_mercari_walk_and_cents():
    body = {"data": {"search": {"itemsList": [{"id": "m1", "name": "leather jacket fur collar", "price": 7500,
                                               "status": "sold_out", "updated": 1735689600}]}}}
    it = next(mercari._walk_items(body))
    l = mercari.item_to_listing(it, None)
    assert l.price == 75.0 and l.is_sold and l.sold_at == "2025-01-01"


def test_shopgoodwill_closed_requires_bids():
    sold = shopgoodwill.item_to_listing({"itemId": 1, "title": "x", "currentPrice": 40.0, "numBids": 3,
                                         "endTime": "2026-10-01T18:00:00"}, True, None)
    unsold = shopgoodwill.item_to_listing({"itemId": 2, "title": "x", "currentPrice": 9.99, "numBids": 0}, True, None)
    assert sold.is_sold and sold.sold_at == "2026-10-01" and not unsold.is_sold


def test_terapeak_csv(tmp_path):
    f = tmp_path / "tp.csv"
    f.write_text("Listing,Avg sold price,Avg shipping,Total sold,Date last sold\n"
                 "\"Vintage Leather Jacket Fur Collar 125566778899\",$120.50,$15.00,1,\"Mar 4, 2025\"\n")
    (l,) = import_file(f)
    assert (l.source_id, l.price, l.shipping, l.sold_at) == ("125566778899", 120.5, 15.0, "2025-03-04")


def test_dates():
    assert to_iso_date(1735689600000) == "2025-01-01"
    assert to_iso_date("Oct 3, 2026") == "2026-10-03"


@pytest.mark.parametrize("title,expect", [
    ("Vintage 80s Wilsons Leather Bomber Raccoon Fur Collar Made in USA L",
     {"brand_norm": "Wilsons", "decade": 1980.0, "collar": "raccoon", "style": "bomber", "made_in": "usa", "relevant": 1}),
    ("Grizzly lambskin leather car coat shearling collar distressed brown 42",
     {"leather_type": "lambskin", "collar": "shearling", "style": "car_coat", "pattern": "distressed",
      "color": "brown", "size_norm": "L", "grizzly_kw": 1, "relevant": 1}),
    ("Leather jacket sewing pattern fur collar", {"relevant": 0}),
    ("Faux leather jacket faux fur collar women's",
     {"leather_type": "faux", "collar": "faux_fur", "gender": "women", "relevant": 0}),
    # Real titles seen on Poshmark: "grizzly" as a brand, not the style.
    ("Dakota Grizzly Men's Faux Leather Sherpa Coat Large Brown EUC",
     {"brand_norm": "Dakota Grizzly", "grizzly_kw": 0, "relevant": 0}),
    ("Ariat Grizzly 2.0 Parka Womens Medium Olive Green Insulated Jacket",
     {"brand_norm": "Ariat", "grizzly_kw": 0, "relevant": 0}),
    ("Jaclyn Smith Women's Black Faux Fur Collar Leather Look Jacket", {"leather_type": "faux", "relevant": 0}),
    # Live false positives: leather/shearling only in the description, or not a jacket.
    ("Polo shirt", {"relevant": 0}),
    ("Faux fur coat", {"relevant": 0}),
])
def test_extract(title, expect):
    f = extract({"title": title, "description": "leather trim, shearling collar" if len(title) < 15 else None})
    for k, v in expect.items():
        assert f[k] == v, (k, f[k])


def _synthetic(n=900, seed=0):
    """Known ground truth: Schott +60%, raccoon collar +25%, 2%/month inflation, poshmark +15% bias."""
    rng = np.random.default_rng(seed)
    brands = rng.choice(["Schott", "Wilsons", "Bernardo", "G-III"], n, p=[.2, .4, .2, .2])
    collars = rng.choice(["raccoon", "shearling", "fur"], n)
    src = rng.choice(["ebay", "grailed", "poshmark"], n)
    months = rng.integers(0, 24, n)
    logp = (np.log(120) + np.where(brands == "Schott", np.log(1.6), 0) + np.where(collars == "raccoon", np.log(1.25), 0)
            + months * np.log(1.02) + np.where(src == "poshmark", np.log(1.15), 0) + rng.normal(0, 0.15, n))
    dates = pd.Timestamp("2024-11-01") + pd.to_timedelta(months * 30 + rng.integers(0, 28, n), unit="D")
    rows = [Listing(source=s, source_id=str(i), title=f"{b} leather jacket {c} collar lot{i}", price=float(np.exp(lp)),
                    is_sold=True, price_is_actual=True, sold_at=d.date().isoformat(), brand=b).to_row()
            for i, (b, c, s, lp, d) in enumerate(zip(brands, collars, src, logp, dates))]
    return pd.DataFrame(rows)


def test_model_recovers_known_effects():
    df = build_frame(_synthetic())
    m = fit(df)
    eff = m.effects.set_index(["feature", "level"]).pct_effect
    # Schott vs the other three brands: the centered effect gap should be near +60%.
    others = eff.loc["brand_norm"].drop("Schott").mean()
    gap = (1 + eff["brand_norm", "Schott"] / 100) / (1 + others / 100) - 1
    assert 0.45 < gap < 0.75, gap
    last = m.index["index"].iloc[-1]
    assert 140 < last < 175, last  # 1.02^23 = 1.58
    assert m.n > 850
    assert m.metrics["gbm_median_abs_pct_err"] < m.metrics["baseline_median_abs_pct_err"]
    assert m.metrics["hedonic_median_abs_pct_err"] < m.metrics["baseline_median_abs_pct_err"]
    assert 65 < m.metrics["p10_p90_coverage_pct"] < 95


def test_dedupe_cross_post_only():
    rows = [Listing("poshmark", "a", "Vintage Wilsons leather bomber raccoon collar", 100.0, is_sold=True, sold_at="2026-01-05"),
            Listing("mercari", "b", "Vintage Wilsons leather bomber raccoon collar", 104.0, listed_at="2025-12-20"),
            Listing("poshmark", "c", "Vintage Wilsons leather bomber raccoon collar", 101.0, is_sold=True, sold_at="2026-01-09")]
    d = build_frame(pd.DataFrame([r.to_row() for r in rows])).set_index("source_id").dup
    assert list(d) == [False, True, False]


def test_store_never_downgrades_sale(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert([Listing("ebay", "1", "x", 100.0, is_sold=True, sold_at="2026-01-01")])
    s.upsert([Listing("ebay", "1", "x", 120.0, is_sold=False)])
    row = s.frame().iloc[0]
    assert row.is_sold == 1 and row.price == 100.0


def test_grailed_key_from_next_data():
    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"initialProps":{"globalData":'
            '{"public_config":{"algolia":{"appId":"MNRWEFSS2Q","publicSearchKey":"' + "a" * 32 + '"}}}}}}</script>')
    assert grailed.key_from_html(html) == "a" * 32
    assert grailed.key_from_html("<html></html>") is None


def test_grailed_shipping_included_not_double_counted():
    h = {"id": 1, "title": "x", "sold_price": 200, "sold_shipping_price": 15, "sold_price_includes_shipping": True,
         "sold_at": "2026-01-01T00:00:00Z"}
    assert grailed.hit_to_listing(h, True, None).shipping == 0.0


DEPOP_HTML = """<ul><li class="x_listItem"><div><a aria-label="Schott men's brown jacket"
 href="/products/vintseller_-schott-leather-bomber-shearling-collar-ab12/"><img src="p.jpg" alt="Schott"></a>
 <p class="q_sizeAttributeText">L</p><p aria-description="Price">$185.00</p></div></li>
 <li><a href="/products/other-thing-zz99/">no price here</a></li></ul>"""


def test_depop_cards():
    from grizzly_pricing.sources.depop import parse_search_html

    (l,) = parse_search_html(DEPOP_HTML)
    assert (l.price, l.size, l.seller, l.is_sold) == (185.0, "L", "vintseller", False)
    assert l.description == "schott leather bomber shearling collar"


def test_zenrows_budget_guard(tmp_path, monkeypatch):
    from grizzly_pricing import zenrows
    from grizzly_pricing.net import SourceError

    monkeypatch.setattr(zenrows, "SPEND_FILE", tmp_path / "spend.json")
    (tmp_path / "spend.json").write_text('{"credits": 4490}')
    monkeypatch.setenv("GRIZZLY_ZENROWS_BUDGET", "4500")
    with pytest.raises(SourceError, match="budget"):
        zenrows.fetch(["https://example.com/"])
