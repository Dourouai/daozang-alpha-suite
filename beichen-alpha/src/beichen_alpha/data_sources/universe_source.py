from __future__ import annotations

import contextlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from beichen_alpha.models import StockProfile
from beichen_alpha.profile_tags import profile_all_tags

from .akshare_source import normalize_symbol
from .profile_source import fetch_tencent_profiles, merge_profiles


CONSUMER_NAME_KEYWORDS = (
    "茅台",
    "五粮液",
    "老窖",
    "汾酒",
    "洋河",
    "啤酒",
    "伊利",
    "海天",
    "中免",
    "牧原",
    "温氏",
    "海大",
    "双汇",
    "美的",
    "格力",
    "海尔",
    "老板电器",
    "苏泊尔",
    "韵达",
    "顺丰",
    "圆通",
    "申通",
)


@dataclass(frozen=True)
class UniverseResult:
    symbols: list[str]
    profiles: dict[str, StockProfile]


class AkshareUniverseSource:
    def __init__(
        self,
        limit: int = 60,
        candidates: int = 0,
        min_turnover_billion: float = 0.0,
        min_market_cap_billion: float = 300.0,
        exclude_themes: tuple[str, ...] = ("消费", "品牌消费"),
        cache_path: str | Path = "",
        refresh_cache: bool = False,
    ) -> None:
        self.limit = limit
        self.candidates = candidates
        self.min_turnover_billion = min_turnover_billion
        self.min_market_cap_billion = min_market_cap_billion
        self.exclude_themes = exclude_themes
        self.cache_path = Path(cache_path) if cache_path else None
        self.refresh_cache = refresh_cache

    def load(self, profile_overrides: dict[str, StockProfile] | None = None) -> UniverseResult:
        if self.cache_path and self.cache_path.exists() and not self.refresh_cache:
            candidate_rows, cached_profiles = load_universe_cache(self.cache_path)
            profiles = merge_profiles(cached_profiles, profile_overrides or {})
        else:
            candidate_rows, profiles = fetch_universe_rows_and_profiles(
                candidates=self.candidates,
                min_turnover_billion=self.min_turnover_billion,
                profile_overrides=profile_overrides,
            )
            if self.cache_path:
                save_universe_cache(candidate_rows, profiles, self.cache_path)

        candidate_rows = sort_candidates_by_profile(candidate_rows, profiles)
        symbols = []
        for row in candidate_rows:
            code = row["code"]
            profile = profiles.get(code)
            if profile is None or not passes_profile_filter(
                profile,
                min_market_cap_billion=self.min_market_cap_billion,
                exclude_themes=self.exclude_themes,
            ):
                continue
            symbols.append(code)
            if len(symbols) >= self.limit:
                break
        return UniverseResult(symbols=symbols, profiles=profiles)


def fetch_universe_rows_and_profiles(
    candidates: int = 0,
    min_turnover_billion: float = 0.0,
    profile_overrides: dict[str, StockProfile] | None = None,
) -> tuple[list[dict], dict[str, StockProfile]]:
    ak = import_akshare()
    candidate_rows = load_candidate_rows(
        ak,
        candidates=candidates,
        min_turnover_billion=min_turnover_billion,
    )
    inferred_profiles = {
        row["code"]: infer_stock_profile(row["code"], row["name"])
        for row in candidate_rows
    }
    live_profiles = fetch_tencent_profiles([row["code"] for row in candidate_rows])
    profiles = merge_profiles(inferred_profiles, profile_overrides or {}, live_profiles)
    return candidate_rows, profiles


def load_candidate_rows(ak, candidates: int, min_turnover_billion: float) -> list[dict]:
    try:
        spot_rows = fetch_sina_spot_rows(ak)
        candidate_rows = select_spot_candidates(
            spot_rows,
            candidates=candidates,
            min_turnover_billion=min_turnover_billion,
        )
        if candidate_rows:
            return candidate_rows
    except Exception:
        pass

    code_rows = fetch_code_name_rows(ak)
    return select_code_candidates(code_rows)


def import_akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "AKShare is not installed. Install it with: python3 -m pip install akshare pandas"
        ) from exc
    return ak


def fetch_sina_spot_rows(ak) -> list[dict]:
    frame = quiet_call(ak.stock_zh_a_spot)
    rows = []
    for record in frame.to_dict(orient="records"):
        code = normalize_spot_code(record.get("代码"))
        name = str(record.get("名称") or "").strip()
        if not code or not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "latest": to_float(record.get("最新价")),
                "turnover": to_float(record.get("成交额")),
            }
        )
    return rows


def fetch_code_name_rows(ak) -> list[dict]:
    frame = quiet_call(ak.stock_info_a_code_name)
    rows = []
    for record in frame.to_dict(orient="records"):
        code = normalize_symbol(str(record.get("code") or ""))
        name = str(record.get("name") or "").strip()
        if not code or not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "latest": 0.0,
                "turnover": 0.0,
            }
        )
    return rows


def select_spot_candidates(
    rows: list[dict],
    candidates: int,
    min_turnover_billion: float,
) -> list[dict]:
    min_turnover = min_turnover_billion * 100_000_000
    filtered = [
        row
        for row in rows
        if is_mainland_stock(row["code"])
        and row["latest"] > 0
        and row["turnover"] >= min_turnover
        and not is_bad_name(row["name"])
    ]
    sorted_rows = sorted(filtered, key=lambda row: row["turnover"], reverse=True)
    if candidates <= 0:
        return sorted_rows
    return sorted_rows[:candidates]


def select_code_candidates(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if is_mainland_stock(row["code"])
        and not is_bad_name(row["name"])
    ]


def save_universe_cache(rows: list[dict], profiles: dict[str, StockProfile], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        for row in sort_candidates_by_profile(rows, profiles):
            profile = profiles.get(row["code"], infer_stock_profile(row["code"], row["name"]))
            payload = {
                "code": row["code"],
                "name": profile.name or row["name"],
                "latest": row.get("latest", 0.0),
                "turnover": row.get("turnover", 0.0),
                "industry": profile.industry,
                "themes": list(profile.themes),
                "market_cap_billion": profile.market_cap_billion,
                "primary_industry": profile.primary_industry,
                "secondary_industries": list(profile.secondary_industries),
                "style_tags": list(profile.style_tags),
                "concept_tags": list(profile.concept_tags),
            }
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return target


def load_universe_cache(path: str | Path) -> tuple[list[dict], dict[str, StockProfile]]:
    rows = []
    profiles: dict[str, StockProfile] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = normalize_symbol(str(record.get("code") or ""))
        name = str(record.get("name") or "").strip()
        if not code or not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "latest": to_float(record.get("latest")),
                "turnover": to_float(record.get("turnover")),
            }
        )
        profiles[code] = StockProfile(
            code=code,
            name=name,
            industry=str(record.get("industry") or ""),
            themes=tuple(record.get("themes") or ()),
            market_cap_billion=to_optional_float(record.get("market_cap_billion")),
            primary_industry=str(record.get("primary_industry") or ""),
            secondary_industries=tuple(record.get("secondary_industries") or ()),
            style_tags=tuple(record.get("style_tags") or ()),
            concept_tags=tuple(record.get("concept_tags") or ()),
        )
    return rows, profiles


def to_optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_candidates_by_profile(rows: list[dict], profiles: dict[str, StockProfile]) -> list[dict]:
    def sort_key(row: dict) -> tuple[float, float]:
        profile = profiles.get(row["code"])
        market_cap = profile.market_cap_billion if profile and profile.market_cap_billion else 0.0
        return (market_cap, row.get("turnover", 0.0))

    return sorted(rows, key=sort_key, reverse=True)


def infer_stock_profile(code: str, name: str) -> StockProfile:
    industry = ""
    themes: list[str] = []

    if any(keyword in name for keyword in CONSUMER_NAME_KEYWORDS):
        industry = "消费"
        themes.extend(["消费", "品牌消费"])
    elif name.endswith("银行") or "银行" in name:
        industry = "银行"
        themes.extend(["金融稳定", "高股息", "防御"])
    elif any(keyword in name for keyword in ("证券", "中金公司", "华泰", "国泰海通", "东方财富", "中信建投", "中国银河")):
        industry = "非银金融"
        themes.extend(["非银金融", "金融稳定", "顺周期"])
    elif any(keyword in name for keyword in ("保险", "中国平安", "中国人寿", "中国太保", "中国人保", "新华保险")):
        industry = "保险"
        themes.extend(["非银金融", "金融稳定", "防御"])
    elif any(keyword in name for keyword in ("电力", "水电", "核电", "三峡能源", "国投电力")):
        industry = "公用事业"
        themes.extend(["高股息", "防御", "能源安全", "现金流"])
    elif any(keyword in name for keyword in ("神华", "煤", "能源", "兖矿", "中煤")):
        industry = "煤炭"
        themes.extend(["高股息", "能源安全", "资源", "现金流"])
    elif "黄金" in name:
        industry = "黄金"
        themes.extend(["黄金", "避险", "资源"])
    elif any(keyword in name for keyword in ("石油", "石化", "海油")):
        industry = "石油石化"
        themes.extend(["高股息", "能源安全", "资源"])
    elif any(keyword in name for keyword in ("半导体", "芯", "中芯", "寒武纪", "海光", "北方华创", "中微公司", "韦尔")):
        industry = "半导体"
        themes.extend(["半导体", "先进制造", "高端制造", "数字经济"])
    elif any(keyword in name for keyword in ("华虹", "兆易", "澜起", "长电科技", "复旦微电", "沪硅", "盛合晶微")):
        industry = "半导体"
        themes.extend(["半导体", "先进制造", "高端制造", "数字经济"])
    elif any(keyword in name for keyword in ("中际旭创", "新易盛", "天孚通信", "光迅科技", "光模块", "CPO")):
        industry = "光模块"
        themes.extend(["AI硬件", "先进制造", "数字经济"])
    elif any(keyword in name for keyword in ("工业富联", "立讯精密", "东山精密", "生益科技", "沪电股份", "胜宏科技")):
        industry = "AI硬件"
        themes.extend(["AI硬件", "先进制造", "数字经济"])
    elif any(keyword in name for keyword in ("中国移动", "中国电信", "中国联通", "长飞光纤", "中兴通讯")):
        industry = "通信"
        themes.extend(["数字经济", "AI硬件", "高股息"])
    elif any(keyword in name for keyword in ("京东方", "TCL科技", "海康威视", "大华股份")):
        industry = "电子"
        themes.extend(["先进制造", "数字经济"])
    elif any(keyword in name for keyword in ("宁德", "比亚迪", "阳光电源", "亿纬", "新能源")):
        industry = "新能源"
        themes.extend(["新能源", "先进制造", "全球竞争"])
    elif any(keyword in name for keyword in ("钨", "锂", "稀土", "铜", "铝", "钼", "钴", "矿业", "洛阳钼业", "紫金")):
        industry = "工业金属"
        themes.extend(["工业金属", "资源", "新材料"])
    elif any(keyword in name for keyword in ("巨化", "万华", "化学", "化工", "氟")):
        industry = "化工"
        themes.extend(["先进制造", "新材料"])
    elif any(keyword in name for keyword in ("医药", "生物", "药明", "恒瑞", "迈瑞", "百济", "君实", "创新药")):
        industry = "医药"
        themes.extend(["创新药", "医药"])

    return StockProfile(code=code, name=name, industry=industry, themes=tuple(dict.fromkeys(themes)))


def passes_profile_filter(
    profile: StockProfile,
    min_market_cap_billion: float,
    exclude_themes: tuple[str, ...],
) -> bool:
    if profile.market_cap_billion is None or profile.market_cap_billion < min_market_cap_billion:
        return False
    if set(profile_all_tags(profile)).intersection(exclude_themes):
        return False
    return True


def quiet_call(func: Callable, **kwargs):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return func(**kwargs)


def normalize_spot_code(value) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(("sh", "sz")):
        return normalize_symbol(text)
    return ""


def is_mainland_stock(code: str) -> bool:
    return code.startswith(("0", "3", "6", "688", "689"))


def is_bad_name(name: str) -> bool:
    upper = name.upper()
    return "ST" in upper or "退" in name or upper.startswith(("N", "C", "U"))


def to_float(value) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
