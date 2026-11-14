"""Attribute extraction from listing text, relevance filtering, cross-post dedupe.

Every extractor is a keyword/regex rule, kept transparent so you can audit and extend the
lists. Site-provided structured fields (brand, Etsy when_made) take precedence over text.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# Brands commonly seen on fur/shearling-collar leather jackets in resale. Extend freely;
# values are canonical names, keys are regexes matched against lowercased text.
BRANDS = {
    r"\bwilsons?\b|\bm\. ?julian\b": "Wilsons",
    r"\bg[- ]?iii\b": "G-III",
    r"\bschott\b": "Schott",
    r"\bavirex\b": "Avirex",
    r"\bpelle ?pelle\b": "Pelle Pelle",
    r"\bbernardo\b": "Bernardo",
    r"\bandrew marc\b": "Andrew Marc",
    r"\bmarc new york\b": "Marc New York",
    r"\boverland\b": "Overland",
    r"\bcripple creek\b": "Cripple Creek",
    r"\bgolden bear\b": "Golden Bear",
    r"\bexcelled\b": "Excelled",
    r"\bphase ?2\b": "Phase 2",
    r"\bvakko\b": "Vakko",
    r"\bremy\b": "Remy",
    r"\bsiena\b": "Siena",
    r"\bcoach\b": "Coach",
    r"\bpolo\b|\bralph lauren\b|\brrl\b": "Ralph Lauren",
    r"\bbanana republic\b": "Banana Republic",
    r"\blevi'?s\b": "Levi's",
    r"\beddie bauer\b": "Eddie Bauer",
    r"\bl\.? ?l\.? bean\b": "L.L.Bean",
    r"\bwoolrich\b": "Woolrich",
    r"\bpendleton\b": "Pendleton",
    r"\bcalvin klein\b": "Calvin Klein",
    r"\bguess\b": "Guess",
    r"\btommy hilfiger\b": "Tommy Hilfiger",
    r"\bmembers only\b": "Members Only",
    r"\bharley[- ]davidson\b": "Harley-Davidson",
    r"\bvanson\b": "Vanson",
    r"\blanglitz\b": "Langlitz",
    r"\bbelstaff\b": "Belstaff",
    r"\ballsaints\b|\ball saints\b": "AllSaints",
    r"\bacne\b": "Acne Studios",
    r"\bour legacy\b": "Our Legacy",
    r"\bsaint laurent\b|\bysl\b": "Saint Laurent",
    r"\bgucci\b": "Gucci",
    r"\bprada\b": "Prada",
    r"\bburberry\b": "Burberry",
    r"\bmax ?mara\b": "Max Mara",
    r"\bj\.? ?crew\b": "J.Crew",
    r"\bzara\b": "Zara",
    r"\bkenneth cole\b": "Kenneth Cole",
    r"\bcole haan\b": "Cole Haan",
    r"\blakeland\b": "Lakeland",
    r"\bcooper\b": "Cooper",
    r"\bbuzz rickson\b": "Buzz Rickson's",
    r"\breal mccoy\b": "The Real McCoy's",
    r"\beastman\b": "Eastman",
    r"\baero leather\b": "Aero",
    r"\bsheepskin co\b|\bshearling co\b": "Sheepskin Co",
    r"\bmirage\b": "Mirage",
    r"\bgallery\b": "Gallery",
    r"\bnine west\b": "Nine West",
    r"\bdanier\b": "Danier",
    r"\bjones new york\b": "Jones New York",
}
_BRAND_RX = [(re.compile(k), v) for k, v in BRANDS.items()]

LEATHER = {
    "lambskin": r"lamb ?skin|\blamb\b|nappa",
    "cowhide": r"cow ?hide|\bsteerhide|\bbuffalo|\bbison",
    "horsehide": r"horse ?hide",
    "goatskin": r"goat ?skin|\bgoat\b",
    "suede": r"\bsuede\b",
    "deerskin": r"deer ?skin|\belk\b",
    "faux": r"\bfaux leather\b|\bvegan leather\b|\bpleather\b|\bpu leather\b|\bimitation",
}
COLLAR = {
    "raccoon": r"raccoon",
    "fox": r"\bfox\b",
    "coyote": r"coyote",
    "mink": r"\bmink\b",
    "mouton": r"mouton",
    "shearling": r"shearling|sheepskin collar|sherpa",
    "faux_fur": r"faux fur|faux-fur",
    "fur_unspecified": r"\bfur\b",
}
STYLE = {
    "bomber": r"bomber|flight|a-?2\b|g-?1\b|b-?3\b",
    "car_coat": r"car coat|3/4|three quarter|mid[- ]length",
    "long_coat": r"long coat|trench|full length|maxi|duster",
    "trucker": r"trucker|rancher|western",
    "parka": r"parka|hooded|hood\b",
    "moto": r"\bmoto\b|motorcycle|biker|cafe racer",
    "varsity": r"varsity|letterman",
}
PATTERN = {
    "distressed": r"distress|worn[- ]in|patina",
    "two_tone": r"two[- ]tone|2[- ]tone|colou?r ?block",
    "quilted": r"quilt",
    "embossed": r"embossed|croc|snake|python",
    "patchwork": r"patchwork",
    "stripes": r"stripe",
    "embroidered": r"embroider",
}
COLORS = ["black", "brown", "tan", "cognac", "burgundy", "oxblood", "olive", "green",
          "navy", "blue", "red", "grey", "gray", "white", "cream", "camel", "chocolate"]
MADE_IN = {
    "usa": r"made in (the )?usa|made in america|usa made|union made",
    "italy": r"made in italy|italian leather",
    "turkey": r"made in turkey|turkish",
    "korea": r"made in korea",
    "canada": r"made in canada",
    "argentina": r"made in argentina",
    "uk": r"made in (england|uk|britain)",
    "china": r"made in china",
    "pakistan": r"made in pakistan|made in india",
}
EXCLUDE = re.compile(
    r"sewing pattern|\bdoll\b|costume|\bpatch(es)? only|\bpin\b|keychain|\bposter\b|\btoddler|"
    r"\bbaby\b|\bdog\b|\bpet\b|\bbag\b|\bboots?\b|\bgloves?\b|\bhat\b|\bvest\b"
)
LEATHER_ANY = re.compile(r"leather|lambskin|cowhide|horsehide|suede|sheepskin|shearling|goatskin")
STYLE_CORE = re.compile(r"grizzly|fur collar|fur trim|shearling|raccoon|mouton|fox collar|sherpa|fur[- ]lined|collar fur")


def _first(rx: dict[str, str], text: str, default: str = "none") -> str:
    for k, pat in rx.items():
        if re.search(pat, text):
            return k
    return default


def brand_of(text: str, given: str | None) -> str:
    if given and given.strip() and given.strip().lower() not in ("unknown", "unbranded", "other", "vintage"):
        g = given.strip().lower()
        for rx, name in _BRAND_RX:
            if rx.search(g):
                return name
        return given.strip().title()
    for rx, name in _BRAND_RX:
        if rx.search(text):
            return name
    return "unknown"


DECADE_RX = [
    (re.compile(r"\b(19[4-9]\d|20[0-2]\d)\b"), lambda m: int(m.group(1)) // 10 * 10),
    (re.compile(r"\b(19)?([4-9])0'?s\b"), lambda m: 1900 + int(m.group(2)) * 10),
    (re.compile(r"\b(forties|fifties|sixties|seventies|eighties|nineties)\b"),
     lambda m: {"forties": 1940, "fifties": 1950, "sixties": 1960, "seventies": 1970,
                "eighties": 1980, "nineties": 1990}[m.group(1)]),
    (re.compile(r"\by2k\b|\b2000s\b"), lambda m: 2000),
]


def decade_of(text: str, age_hint: str | None) -> float:
    for src in (age_hint or "", text):
        s = src.lower()
        for rx, fn in DECADE_RX:
            m = rx.search(s)
            if m:
                d = fn(m)
                if 1930 <= d <= 2020:
                    return float(d)
    return np.nan


def condition_of(text: str, given: str | None) -> str:
    s = f"{given or ''} {text}".lower()
    if re.search(r"\bnwt\b|new with tags|brand new|\bdeadstock\b|\bnew\b(?! listing)", s):
        return "new"
    if re.search(r"\bpoor\b|for parts|\brips?\b|\btears?\b|\bdamage|\bflaw|as is|\bcracking", s):
        return "flawed"
    if re.search(r"excellent|like new|mint|\bgently used|euc\b", s):
        return "excellent"
    if re.search(r"\bgood\b|\bused\b|pre-?owned|\bvgc\b|\bguc\b", s):
        return "good"
    return "unknown"


SIZE_RX = re.compile(r"\b(xxs|xs|s|m|l|xl|xxl|2xl|xxxl|3xl|4xl|small|medium|large|x-large)\b")
SIZE_NUM = re.compile(r"\b(3[4-9]|4[0-9]|5[0-6])\s?(r|l|s|t|reg|long|short)?\b")
SIZE_MAP = {"xxs": "XS", "xs": "XS", "s": "S", "small": "S", "m": "M", "medium": "M", "l": "L",
            "large": "L", "xl": "XL", "x-large": "XL", "xxl": "XXL", "2xl": "XXL",
            "xxxl": "3XL+", "3xl": "3XL+", "4xl": "3XL+"}


def size_of(text: str, given: str | None) -> str:
    s = f"{given or ''} size {text}".lower()
    m = re.search(r"size[:\s]+([a-z0-9-]+)", s)
    cand = m.group(1) if m else None
    if cand in SIZE_MAP:
        return SIZE_MAP[cand]
    m = SIZE_RX.search(s)
    if m:
        return SIZE_MAP[m.group(1)]
    m = SIZE_NUM.search(s)
    if m:  # chest sizes: 38 ~ M, 42 ~ L, 46 ~ XL, 50 ~ XXL
        n = int(m.group(1))
        return "S" if n <= 37 else "M" if n <= 40 else "L" if n <= 44 else "XL" if n <= 47 else "XXL" if n <= 50 else "3XL+"
    return "unknown"


def gender_of(text: str) -> str:
    if re.search(r"\bwomen'?s\b|\bwomens\b|\bladies\b|\bwmns\b|\bfemale\b", text):
        return "women"
    if re.search(r"\bmen'?s\b|\bmens\b|\bmale\b", text):
        return "men"
    return "unknown"


def extract(row: dict) -> dict:
    text = f"{row.get('title') or ''} {row.get('description') or ''}".lower()
    title = (row.get("title") or "").lower()
    return {
        "brand_norm": brand_of(text, row.get("brand")),
        "decade": decade_of(text, row.get("age_hint")),
        "vintage_kw": int(bool(re.search(r"\bvintage\b|\bvtg\b|\bretro\b", text))),
        "leather_type": _first(LEATHER, text, "leather_unspecified"),
        "collar": _first(COLLAR, text),
        "style": _first(STYLE, text, "jacket"),
        "pattern": _first(PATTERN, text, "solid"),
        "color": next((c if c != "gray" else "grey" for c in COLORS if re.search(rf"\b{c}\b", title)), "unknown"),
        "made_in": _first(MADE_IN, text, "unknown"),
        "zip_liner": int(bool(re.search(r"zip[- ]?out|removable lin", text))),
        "condition_norm": condition_of(text, row.get("condition")),
        "size_norm": size_of(title, row.get("size")),
        "gender": gender_of(text),
        "grizzly_kw": int("grizzly" in text),
        "relevant": int(bool(LEATHER_ANY.search(text)) and bool(STYLE_CORE.search(text))
                        and not EXCLUDE.search(title)),
    }


def _norm_title(t: str) -> str:
    return " ".join(sorted(set(re.findall(r"[a-z0-9]{3,}", (t or "").lower()))))


def build_frame(df: pd.DataFrame) -> pd.DataFrame:
    feats = pd.DataFrame([extract(r) for r in df.to_dict("records")], index=df.index)
    out = pd.concat([df, feats], axis=1)
    out["event_date"] = pd.to_datetime(out["sold_at"].fillna(out["listed_at"]), errors="coerce")
    out["month"] = out["event_date"].dt.to_period("M").astype(str).replace("NaT", np.nan)
    out["total_price"] = out["price"] + out["shipping"].fillna(0)
    # Cross-posted jackets (same seller lists on Poshmark + Mercari + eBay) count once:
    # identical title token set and price within 10%, keep sold over active.
    out["_tk"] = out["title"].map(_norm_title)
    # Cross-posted jackets (same seller lists on Poshmark + Mercari + eBay) count once: same
    # title token set, price within 10%, different site, within 45 days. Sold copy is kept.
    # Same-titled items on one site are treated as distinct items.
    out["dup"] = False
    cand = out[out["_tk"].str.len() > 15]
    for _, g in cand[cand.duplicated("_tk", keep=False)].groupby("_tk"):
        g = g.sort_values(["is_sold", "price_is_actual"], ascending=False)
        kept: list = []
        for idx, r in g.iterrows():
            if any(k.source != r.source
                   and pd.notna(k.price) and pd.notna(r.price) and max(k.price, r.price) <= 1.1 * min(k.price, r.price)
                   and pd.notna(k.event_date) and pd.notna(r.event_date)
                   and abs((k.event_date - r.event_date).days) <= 45 for k in kept):
                out.at[idx, "dup"] = True
            else:
                kept.append(r)
    return out.drop(columns=["_tk"])
