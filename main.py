import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
SIMPLE_POKEDEX_JSON = BASE_DIR / "simple_pokedex.json"
ABILITY_LIST_JSON = BASE_DIR / "ability_list.json"
ITEM_LIST_JSON = BASE_DIR / "item_list.json"
MOVE_LIST_JSON = BASE_DIR / "move_list.json"

STAT_ORDER = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
LETTER_TO_STAT = {
    "H": "HP",
    "A": "Atk",
    "B": "Def",
    "C": "SpA",
    "D": "SpD",
    "S": "Spe",
}

NATURE_BY_BOOST_DROP = {
    ("Atk", "Def"): "Lonely",
    ("Atk", "SpA"): "Adamant",
    ("Atk", "SpD"): "Naughty",
    ("Atk", "Spe"): "Brave",
    ("Def", "Atk"): "Bold",
    ("Def", "SpA"): "Impish",
    ("Def", "SpD"): "Lax",
    ("Def", "Spe"): "Relaxed",
    ("SpA", "Atk"): "Modest",
    ("SpA", "Def"): "Mild",
    ("SpA", "SpD"): "Rash",
    ("SpA", "Spe"): "Quiet",
    ("SpD", "Atk"): "Calm",
    ("SpD", "Def"): "Gentle",
    ("SpD", "SpA"): "Careful",
    ("SpD", "Spe"): "Sassy",
    ("Spe", "Atk"): "Timid",
    ("Spe", "Def"): "Hasty",
    ("Spe", "SpA"): "Jolly",
    ("Spe", "SpD"): "Naive",
}


# Manual overrides (applied after JSON). Use for forms / names missing from simple_pokedex.
POKEMON_NAME_OVERRIDES: Dict[str, str] = {
    "烈咬陆鲨": "Garchomp",
    "喷火龙": "Charizard",
    "妙蛙花": "Venusaur",
    "大狃拉": "Sneasler",
    "大狂拉": "Sneasler",
    "索罗亚克-洗翠的样子": "Zoroark-Hisui",
    "炽焰咆哮虎": "Incineroar",
}

ABILITY_OVERRIDES: Dict[str, str] = {
    "粗糙皮肤": "Rough Skin",
    "猛火": "Blaze",
    "叶绿素": "Chlorophyll",
    "轻装": "Unburden",
    "幻觉": "Illusion",
    "威吓": "Intimidate",
}

ITEM_OVERRIDES: Dict[str, str] = {
    "喷火龙进化石Y": "Charizardite Y",
    "计时球": "Timer Ball",
    "讲究": "Choice Scarf",
}

MOVE_OVERRIDES: Dict[str, str] = {
    "龙爪": "Dragon Claw",
    "跺脚": "Stomping Tantrum",
    # OCR / synonym fixes (JSON may use different wording, e.g. 空气之刃)
    "空气斩": "Air Slash",
    "踩脚": "Stomping Tantrum",
}

MOVE_OVERRIDES.update(
    {
        "灭亡之歌": "Perish Song",
        "地狱突刺": "Throat Chop",
    }
)

_TRANSLATION_MAPS_CACHE: Optional[Tuple[Dict[str, str], Dict[str, str], Dict[str, str], Dict[str, str]]] = None
_MEGA_NAME_MAP_CACHE: Optional[Dict[str, str]] = None

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def normalize_token(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    t = t.strip()
    t = re.sub(r"[：:]+$", "", t).strip()
    return t


def _flatten_item_nodes(nodes: Any) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not isinstance(nodes, list):
        return out
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        if ntype == "item":
            zh = node.get("name_zh")
            en = node.get("name_en")
            if zh and en:
                out.append((str(zh).strip(), str(en).strip()))
        elif ntype == "category":
            out.extend(_flatten_item_nodes(node.get("children")))
    return out


def _load_zh_en_rows(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return {}
    m: Dict[str, str] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        zh = row.get("name_zh")
        en = row.get("name_en")
        if zh and en:
            m[str(zh).strip()] = str(en).strip()
    return m


def _load_items_from_tree(path: Path) -> Dict[str, str]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return {}
    m: Dict[str, str] = {}
    for zh, en in _flatten_item_nodes(raw):
        m[zh] = en
    return m


def _merge_maps(base: Dict[str, str], overrides: Dict[str, str]) -> Dict[str, str]:
    merged = dict(base)
    merged.update(overrides)
    return merged


def _expand_mapping_normalized(m: Dict[str, str]) -> Dict[str, str]:
    out = dict(m)
    for k, v in m.items():
        nk = normalize_token(k)
        if nk and nk not in out:
            out[nk] = v
    return out


def lookup_zh_en(raw: str, mapping: Dict[str, str]) -> str:
    """Match OCR / Chinese text to English using exact, substring, then fuzzy keys."""
    v = normalize_token(raw)
    if not v:
        return raw
    if not has_cjk(v):
        return v
    if v in mapping:
        return mapping[v]
    zh_keys = [k for k in mapping if has_cjk(k)]
    cand = [k for k in zh_keys if len(v) >= 2 and v in k]
    if cand:
        return mapping[max(cand, key=lambda k: (len(k), k))]
    cand = [k for k in zh_keys if len(k) >= 2 and k in v]
    if cand:
        return mapping[max(cand, key=lambda k: (len(k), k))]
    matches = get_close_matches(v, zh_keys, n=1, cutoff=0.55)
    if matches:
        return mapping[matches[0]]
    return v


def lookup_en_zh(raw: str, mapping: Dict[str, str]) -> str:
    """Best-effort English -> Chinese reverse lookup for display."""
    v = normalize_token(raw)
    if not v:
        return raw
    # exact reverse
    for zh, en in mapping.items():
        if normalize_token(en).casefold() == v.casefold():
            return zh
    # fuzzy reverse
    en_keys = [normalize_token(en) for en in mapping.values() if normalize_token(en)]
    matches = get_close_matches(v, en_keys, n=1, cutoff=0.72)
    if matches:
        m = matches[0].casefold()
        for zh, en in mapping.items():
            if normalize_token(en).casefold() == m:
                return zh
    return raw


def lookup_move_zh_en(raw: str, mapping: Dict[str, str]) -> str:
    """Stricter Chinese move -> English lookup to reduce false positives."""
    v = normalize_token(raw)
    if not v:
        return raw
    if not has_cjk(v):
        return v
    if v in mapping:
        return mapping[v]
    zh_keys = [k for k in mapping if has_cjk(k)]
    cand = [k for k in zh_keys if len(v) >= 2 and v in k]
    if cand:
        return mapping[max(cand, key=lambda k: (len(k), k))]
    cand = [k for k in zh_keys if len(k) >= 2 and k in v]
    if cand:
        return mapping[max(cand, key=lambda k: (len(k), k))]
    matches = get_close_matches(v, zh_keys, n=1, cutoff=0.80)
    if matches:
        return mapping[matches[0]]
    return v


def mega_name_map() -> Dict[str, str]:
    """Combined zh->en for fallback when a token fits another category."""
    global _MEGA_NAME_MAP_CACHE
    if _MEGA_NAME_MAP_CACHE is not None:
        return _MEGA_NAME_MAP_CACHE
    p, a, i, m = translation_maps()
    mega: Dict[str, str] = {}
    mega.update(m)
    mega.update(a)
    mega.update(i)
    mega.update(p)
    _MEGA_NAME_MAP_CACHE = mega
    return mega


def ensure_latin_field(value: Optional[str], primary: Dict[str, str]) -> str:
    """Resolve to English; strip remaining CJK if lookup fails."""
    if value is None:
        return ""
    s = normalize_token(str(value))
    if not s:
        return ""
    if not has_cjk(s):
        return s
    t = lookup_zh_en(s, primary)
    if not has_cjk(t):
        return t
    t2 = lookup_zh_en(s, mega_name_map())
    if not has_cjk(t2):
        return t2
    stripped = _CJK_RE.sub("", t2).strip()
    return stripped or "Unknown"


def dedupe_moves(moves: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for mv in moves:
        key = mv.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(mv.strip())
    return out


def ensure_move_latin(value: Optional[str], move_map: Dict[str, str]) -> str:
    """Move-specific resolver using stricter matching."""
    if value is None:
        return ""
    s = normalize_token(str(value))
    if not s:
        return ""
    if not has_cjk(s):
        return s
    t = lookup_move_zh_en(s, move_map)
    if not has_cjk(t):
        return t
    return s


def translation_maps() -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str], Dict[str, str]]:
    """(pokemon, ability, item, move) name_zh -> name_en. JSON first, then overrides."""
    global _TRANSLATION_MAPS_CACHE, _MEGA_NAME_MAP_CACHE
    if _TRANSLATION_MAPS_CACHE is not None:
        return _TRANSLATION_MAPS_CACHE

    pokemon_base = _load_zh_en_rows(SIMPLE_POKEDEX_JSON)
    ability_base = _load_zh_en_rows(ABILITY_LIST_JSON)
    move_base = _load_zh_en_rows(MOVE_LIST_JSON)
    item_base = _load_items_from_tree(ITEM_LIST_JSON)

    _TRANSLATION_MAPS_CACHE = (
        _expand_mapping_normalized(_merge_maps(pokemon_base, POKEMON_NAME_OVERRIDES)),
        _expand_mapping_normalized(_merge_maps(ability_base, ABILITY_OVERRIDES)),
        _expand_mapping_normalized(_merge_maps(item_base, ITEM_OVERRIDES)),
        _expand_mapping_normalized(_merge_maps(move_base, MOVE_OVERRIDES)),
    )
    _MEGA_NAME_MAP_CACHE = None
    return _TRANSLATION_MAPS_CACHE

NATURE_CN_MAP = {
    "内敛": "Modest",
    "固执": "Adamant",
    "淘气": "Naughty",
    "胆小": "Timid",
    "开朗": "Jolly",
    "慎重": "Careful",
    "大胆": "Bold",
    "温和": "Mild",
    "保守": "Modest",
    "爽朗": "Jolly",
    "冷静": "Quiet",
}

NATURE_EN_VALUES = sorted(set(NATURE_CN_MAP.values()) | {"Serious"})


def nature_en_to_zh(en: Optional[str]) -> str:
    if not en:
        return "—"
    for zh, e in NATURE_CN_MAP.items():
        if e == en:
            return zh
    return "—"


@dataclass
class PokemonSet:
    species: str = ""
    gender: Optional[str] = None
    item: Optional[str] = None
    alpha: Optional[bool] = None
    ball: Optional[str] = None
    ability: Optional[str] = None
    nature: Optional[str] = None
    effort_raw: Optional[str] = None
    evs: dict = field(default_factory=lambda: {k: 0 for k in STAT_ORDER})
    ivs: dict = field(default_factory=lambda: {k: 31 for k in STAT_ORDER})
    moves: List[str] = field(default_factory=list)

    def to_pkhex_text(self) -> str:
        poke_m, abil_m, item_m, move_m = translation_maps()
        species = ensure_latin_field(self.species, poke_m) or "Unknown"
        item_lat = ensure_latin_field(self.item, item_m) if self.item else ""
        ball_lat = ensure_latin_field(self.ball, item_m) if self.ball else ""
        ability_lat = ensure_latin_field(self.ability, abil_m) if self.ability else ""

        first = species.strip() or "Unknown"
        if self.gender in {"M", "F"}:
            first += f" ({self.gender})"
        if item_lat and item_lat != "Unknown":
            first += f" @ {item_lat}"

        lines = [first]
        lines.append("Level: 100")
        lines.append("Shiny: Yes")
        if ball_lat and ball_lat != "Unknown":
            lines.append(f"Ball: {ball_lat}")
        elif self.ball:
            lines.append(f"Ball: {self.ball}")
        else:
            lines.append("Ball: Poke Ball")
        if ability_lat and ability_lat != "Unknown":
            lines.append(f"Ability: {ability_lat}")
        else:
            lines.append("Ability: Blaze")
        if self.nature:
            lines.append(f".Nature={self.nature}")
            lines.append(f"{self.nature} Nature")
        else:
            lines.append(".Nature=Serious")
            lines.append("Serious Nature")
        lines.append(
            "EVs: "
            + " / ".join(f"{self.evs[s]} {s}" for s in STAT_ORDER)
        )
        lines.append(
            "IVs: "
            + " / ".join(f"{self.ivs[s]} {s}" for s in STAT_ORDER)
        )
        lines.append("Language: ChineseS")
        for mv in dedupe_moves(
            [ensure_move_latin(m, move_m) for m in self.moves if str(m).strip()]
        ):
            if mv and mv != "Unknown":
                lines.append(f"-{mv}")
        return "\n".join(lines)

    def tag_title_en(self) -> str:
        """Short English label for UI (species + optional gender, no item)."""
        poke_m, _, _, _ = translation_maps()
        name = ensure_latin_field(self.species, poke_m) or "Unknown"
        if self.gender in {"M", "F"}:
            return f"{name} ({self.gender})"
        return name

    def zh_reference_block(self) -> str:
        """Bilingual reference: raw / 中文 → PKHeX English."""
        poke_m, abil_m, item_m, move_m = translation_maps()
        lines = [
            "── 对照（原文 / 中文 → PKHeX 英文）──",
            f"宝可梦 : {(self.species or '—').strip()} → {ensure_latin_field(self.species, poke_m) or 'Unknown'}",
        ]
        if self.item and str(self.item).strip():
            lines.append(
                f"道具    : {self.item.strip()} → {ensure_latin_field(self.item, item_m) or '—'}"
            )
        else:
            lines.append("道具    : —")
        if self.ability and str(self.ability).strip():
            lines.append(
                f"特性    : {self.ability.strip()} → {ensure_latin_field(self.ability, abil_m) or '—'}"
            )
        else:
            lines.append("特性    : —")
        nz = self.nature
        lines.append(
            f"性格    : {nature_en_to_zh(nz)} → {nz or '—'}"
        )
        lines.append(
            "努力值  : "
            + (
                f"{self.effort_raw}（原始） -> "
                if self.effort_raw and self.effort_raw.strip()
                else ""
            )
            + " / ".join(f"{self.evs[s]} {s}" for s in STAT_ORDER)
        )
        dm = dedupe_moves([m for m in self.moves if str(m).strip()])
        lines.append("招式    :")
        if not dm:
            lines.append("  （无）")
        else:
            for mv in dm:
                raw = mv.strip()
                zh = raw if has_cjk(raw) else lookup_en_zh(raw, move_m)
                en = ensure_move_latin(raw, move_m)
                lines.append(f"  · {zh} → {en}")
        return "\n".join(lines)


def normalize_space(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def map_text(value: str, mapping: dict) -> str:
    v = value.strip()
    return mapping.get(v, v)


def parse_ev_line(line: str) -> Optional[dict]:
    # OCR often turns 0 -> O. Normalize first.
    fixed = re.sub(r"(?<=\b)[oO](?=\d|[HhAaDdSs])", "0", line)
    fixed = fixed.replace("OHP", "0HP").replace("ODef", "0Def").replace("OAtk", "0Atk")
    fixed = fixed.replace("OSpA", "0SpA").replace("OSpD", "0SpD").replace("OSpe", "0Spe")

    # Prefer parsing by stat tokens when possible.
    by_stat = {}
    for m in re.finditer(r"(\d{1,3})\s*(HP|Atk|Def|SpA|SpD|Spe)", fixed, flags=re.I):
        val = int(m.group(1))
        stat = m.group(2).upper()
        key = {
            "HP": "HP",
            "ATK": "Atk",
            "DEF": "Def",
            "SPA": "SpA",
            "SPD": "SpD",
            "SPE": "Spe",
        }.get(stat)
        if key:
            by_stat[key] = val
    if len(by_stat) >= 2:
        out = {k: 0 for k in STAT_ORDER}
        out.update(by_stat)
        return out

    # OCR export like: 努力24/20+/0/-/1/21
    seg = re.split(r"[：:\s]", fixed, maxsplit=1)
    tail = seg[1] if len(seg) == 2 else fixed
    if "/" in tail:
        parts = [p.strip() for p in tail.split("/")]
        if len(parts) >= 6:
            vals: List[int] = []
            for p in parts[:6]:
                p2 = p.replace("+", "").replace(" ", "")
                if p2 in {"-", "—", ""}:
                    vals.append(0)
                    continue
                m = re.search(r"\d+", p2)
                if m:
                    vals.append(int(m.group(0)))
                else:
                    vals.append(0)
            return {s: n for s, n in zip(STAT_ORDER, vals)}

    nums = [int(x) for x in re.findall(r"\d+", fixed)]
    if len(nums) < 6:
        return None
    nums = nums[:6]
    return {s: n for s, n in zip(STAT_ORDER, nums)}


def extract_effort_raw(line: str) -> str:
    body = re.sub(r"^(努力|EVs?)\s*[:：]?\s*", "", normalize_space(line), flags=re.I)
    body = body.replace(" ", "")
    body = body.replace("O", "0").replace("o", "0")
    return body


def parse_nature(line: str) -> Optional[str]:
    token = normalize_token(line)
    if token:
        for en in NATURE_EN_VALUES:
            if token.casefold() == en.casefold():
                return en
    for cn, en in NATURE_CN_MAP.items():
        if cn in line:
            return en
    m = re.search(r"([HABCDS])\s*[↑\^]\s*([HABCDS])\s*[↓v]", line, flags=re.I)
    if not m:
        m = re.search(r"([HABCDS])\+.*?([HABCDS])-", line, flags=re.I)
    if not m:
        return None
    up = LETTER_TO_STAT.get(m.group(1).upper())
    down = LETTER_TO_STAT.get(m.group(2).upper())
    if up and down:
        return NATURE_BY_BOOST_DROP.get((up, down))
    return None


def parse_header_line(line: str) -> Tuple[str, Optional[str], Optional[str]]:
    parts = line.split("@", maxsplit=1)
    left = normalize_space(parts[0])
    item = normalize_space(parts[1]) if len(parts) > 1 else None

    gender = None
    m = re.search(r"\((F|M|雌|雄)\)", left, flags=re.I)
    if m:
        token = m.group(1).upper()
        if token == "雌":
            gender = "F"
        elif token == "雄":
            gender = "M"
        else:
            gender = token
        left = re.sub(r"\((F|M|雌|雄)\)", "", left, flags=re.I).strip()

    species = normalize_token(left)
    item_out = normalize_token(item) if item else None
    if item_out == "":
        item_out = None
    return species, gender, item_out


def _is_header_line(line: str) -> bool:
    s = normalize_space(line)
    return "@" in s and not s.startswith("-")


def _is_ability_label(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return bool(re.match(r"^特.{0,2}性", compact) or re.match(r"^ability\b", line, flags=re.I))


def _is_nature_label(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return bool(re.match(r"^性.{0,2}格", compact) or re.match(r"^nature\b", line, flags=re.I))


def _is_ev_label(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return bool(compact.startswith("努力") or re.match(r"^evs?\b", line, flags=re.I))


def _line_to_move_token(line: str, move_map: Dict[str, str], item_map: Dict[str, str]) -> str:
    stripped = line.strip()
    had_bullet = bool(re.match(r"^[-—–·•●▪■◆◇一]\s*", stripped))
    if ":" in line or "：" in line:
        return ""
    token = normalize_token(re.sub(r"^[-—–·•●▪■◆◇一\s]+", "", line))
    if not token or token in {"无", "—", "-", "暂无"}:
        return ""
    if re.match(r"^(特性|性格|努力|个体|道具|宝可梦|ability|nature|evs?|ivs?)\b", token, flags=re.I):
        return ""
    # Exclude common non-move lines (item / tera markers) that OCR often mixes in.
    low = token.casefold()
    if "太晶" in token or low.startswith("tera ") or low == "tera orb":
        return ""

    mapped_move = lookup_move_zh_en(token, move_map)
    mapped_item = lookup_zh_en(token, item_map)
    item_values = {v.casefold() for v in item_map.values()}
    if mapped_item != normalize_token(token) or low in item_values:
        if mapped_move == normalize_token(token):
            return ""

    # If line has no bullet and cannot map to known move, skip to avoid random text.
    if not had_bullet and mapped_move == normalize_token(token):
        return ""

    # For Chinese move text, require successful mapping to reduce false positives.
    if has_cjk(token) and mapped_move == normalize_token(token):
        return ""

    return mapped_move if mapped_move and mapped_move != "Unknown" else token


def _move_side_hint(line: str) -> Optional[int]:
    """Heuristic for two-column OCR move ownership: left=0, right=1."""
    s = line.strip()
    if not s:
        return None
    # In many screenshots OCR keeps different dash variants for left/right columns.
    if s.startswith("-"):
        return 0
    if s.startswith("—") or s.startswith("–") or s.startswith("一"):
        return 1
    return None


def _move_side_hint(line: str) -> Optional[int]:
    """Conservative side hint for two-column OCR move ownership.

    OCR dash variants are too unstable to map to a fixed column reliably, so
    generic bullets should fall back to the alternating assignment logic.
    """
    return None


def _parse_pair_section(lines: List[str], left: PokemonSet, right: PokemonSet) -> None:
    _, abil_map, item_map, move_map = translation_maps()
    pending: Optional[Tuple[str, int]] = None
    ability_count = 0
    nature_count = 0
    ev_count = 0
    last_nature_target: Optional[int] = None
    mons = [left, right]
    move_phase = False
    move_turn = 0
    ability_values = {normalize_token(v).casefold() for v in abil_map.values()}

    def first_missing(field: str) -> Optional[int]:
        for idx, mon in enumerate(mons):
            if not getattr(mon, field):
                return idx
        return None

    def first_zero_evs() -> Optional[int]:
        for idx, mon in enumerate(mons):
            if all(int(mon.evs.get(stat, 0)) == 0 for stat in STAT_ORDER):
                return idx
        return None

    def assign_move(token: str) -> None:
        nonlocal move_turn
        # Two-column OCR usually comes out row-by-row, so moves tend to alternate
        # between left and right mons once the move section starts.
        if len(mons[0].moves) >= 4 and len(mons[1].moves) < 4:
            target = 1
        elif len(mons[1].moves) >= 4 and len(mons[0].moves) < 4:
            target = 0
        else:
            target = move_turn % 2
            move_turn += 1
        mons[target].moves.append(token)

    for raw in lines:
        line = normalize_space(raw)
        if not line:
            continue

        if _is_ability_label(line):
            pending = ("ability", ability_count % 2)
            ability_count += 1
            continue
        if _is_nature_label(line):
            pending = ("nature", nature_count % 2)
            nature_count += 1
            continue
        if _is_ev_label(line):
            evs = parse_ev_line(line)
            if evs:
                mons[ev_count % 2].evs = evs
                mons[ev_count % 2].effort_raw = extract_effort_raw(line)
            ev_count += 1
            if ev_count >= 2:
                # In two-column screenshots, move list typically starts after both EV lines.
                move_phase = True
            pending = None
            continue

        if pending:
            field, tid = pending
            if field == "ability":
                mons[tid].ability = normalize_token(line)
            elif field == "nature":
                parsed = parse_nature(line) or normalize_token(line)
                mons[tid].nature = parsed
                last_nature_target = tid
            pending = None
            continue

        # Often nature boost/drop line comes right after nature text.
        if last_nature_target is not None:
            n2 = parse_nature(line)
            if n2:
                other_missing = first_missing("nature")
                if other_missing is not None and other_missing != last_nature_target:
                    mons[other_missing].nature = n2
                    nature_count = max(nature_count, other_missing + 1)
                    last_nature_target = other_missing
                    continue
                mons[last_nature_target].nature = n2
                continue
            last_nature_target = None

        if not move_phase:
            evs = parse_ev_line(line)
            if evs:
                target = first_zero_evs()
                if target is not None:
                    mons[target].evs = evs
                    mons[target].effort_raw = extract_effort_raw(line)
                    ev_count = max(ev_count, target + 1)
                    if all(any(int(mon.evs.get(stat, 0)) for stat in STAT_ORDER) for mon in mons):
                        move_phase = True
                    continue

            parsed_nature = parse_nature(line)
            if parsed_nature:
                target = first_missing("nature")
                if target is not None:
                    mons[target].nature = parsed_nature
                    nature_count = max(nature_count, target + 1)
                    last_nature_target = target
                    continue

            ability_guess = lookup_zh_en(line, abil_map)
            normalized_line = normalize_token(line)
            if (
                normalized_line
                and (
                    ability_guess != normalized_line
                    or normalized_line.casefold() in ability_values
                )
            ):
                target = first_missing("ability")
                if target is not None:
                    mons[target].ability = (
                        normalized_line if normalized_line.casefold() in ability_values else normalize_token(ability_guess)
                    )
                    ability_count = max(ability_count, target + 1)
                    continue

            maybe_move = _line_to_move_token(line, move_map, item_map)
            if maybe_move:
                move_phase = True

        if not move_phase:
            continue
        if len(mons[0].moves) + len(mons[1].moves) >= 8:
            continue

        mv = _line_to_move_token(line, move_map, item_map)
        if mv:
            hint = _move_side_hint(line)
            if hint is not None:
                # Respect side hint first; overflow to the other side if full.
                if len(mons[hint].moves) < 4:
                    mons[hint].moves.append(mv)
                elif len(mons[1 - hint].moves) < 4:
                    mons[1 - hint].moves.append(mv)
                else:
                    mons[hint].moves.append(mv)
                move_turn = (hint + 1) % 2
            else:
                assign_move(mv)


def _parse_blocks_two_column(lines: List[str]) -> List[PokemonSet]:
    blocks: List[PokemonSet] = []
    i = 0
    n = len(lines)
    while i < n:
        line = normalize_space(lines[i])
        if not line:
            i += 1
            continue
        if _is_header_line(line) and i + 1 < n and _is_header_line(normalize_space(lines[i + 1])):
            s1, g1, it1 = parse_header_line(normalize_space(lines[i]))
            s2, g2, it2 = parse_header_line(normalize_space(lines[i + 1]))
            left = PokemonSet(species=s1, gender=g1, item=it1)
            right = PokemonSet(species=s2, gender=g2, item=it2)
            j = i + 2
            body: List[str] = []
            while j < n:
                cur = normalize_space(lines[j])
                nxt = normalize_space(lines[j + 1]) if j + 1 < n else ""
                if _is_header_line(cur) and _is_header_line(nxt):
                    break
                body.append(lines[j])
                j += 1
            _parse_pair_section(body, left, right)
            blocks.extend([left, right])
            i = j
            continue
        # fallback for non-paired single header
        if _is_header_line(line):
            s, g, it = parse_header_line(line)
            blocks.append(PokemonSet(species=s, gender=g, item=it))
        i += 1
    return blocks


def _parse_blocks_single(lines: List[str]) -> List[PokemonSet]:
    blocks: List[PokemonSet] = []
    current: Optional[PokemonSet] = None
    in_move_section = False
    _, abil_map, _, move_map = translation_maps()

    for raw in lines:
        line = normalize_space(raw)
        if not line:
            continue
        compact = re.sub(r"\s+", "", line)

        # New Pokémon block: header line usually contains @.
        if "@" in line and not line.startswith("-"):
            if current:
                blocks.append(current)
            species, gender, item = parse_header_line(line)
            current = PokemonSet(species=species, gender=gender, item=item)
            in_move_section = False
            continue

        if current is None:
            continue

        if line.startswith("-") or line.startswith("—") or line.startswith("–"):
            move = normalize_token(line.lstrip("-—– ·•●▪■◆◇ "))
            current.moves.append(move)
            in_move_section = True
            continue

        if re.match(r"^特.{0,2}性", compact):
            ability = re.sub(r"^特.{0,2}性[:：]?", "", compact, count=1).strip()
            current.ability = normalize_token(ability)
            in_move_section = False
            continue
        if re.match(r"^ability\b", line, flags=re.I):
            ability = re.sub(r"^ability\s*[:：]?\s*", "", line, flags=re.I).strip()
            current.ability = normalize_token(ability)
            in_move_section = False
            continue
        if not current.ability:
            maybe_ability = lookup_zh_en(line, abil_map)
            # Accept when lookup changed text or matched known ability token.
            if maybe_ability != normalize_token(line):
                current.ability = normalize_token(maybe_ability)
                in_move_section = False
                continue

        if re.match(r"^性.{0,2}格", compact):
            current.nature = parse_nature(line)
            in_move_section = False
            continue
        if re.match(r"^nature\b", line, flags=re.I):
            nature_text = re.sub(r"^nature\s*[:：]?\s*", "", line, flags=re.I).strip()
            current.nature = normalize_token(nature_text) or parse_nature(line)
            in_move_section = False
            continue

        if compact.startswith("努力"):
            evs = parse_ev_line(line)
            if evs:
                current.evs = evs
                current.effort_raw = extract_effort_raw(line)
            in_move_section = False
            continue
        if re.match(r"^evs?\b", line, flags=re.I):
            evs = parse_ev_line(line)
            if evs:
                current.evs = evs
                current.effort_raw = extract_effort_raw(line)
            in_move_section = False
            continue

        # OCR 里“招式:”后可能是单行、分隔符列表，或后续多行无横杠。
        if re.match(r"^(招式|技能|Moves?)", line, flags=re.I):
            in_move_section = True
            move_part = re.split(r"[：:]", line, maxsplit=1)
            if len(move_part) == 2 and move_part[1].strip():
                for mv in re.split(r"[、,/，；;|]", move_part[1]):
                    token = normalize_token(mv)
                    if token and token not in {"无", "—", "-", "暂无"}:
                        current.moves.append(token)
            continue

        # Move bullet styles that OCR often outputs.
        if re.match(r"^[·•●▪■◆◇]\s*", line):
            move = normalize_token(re.sub(r"^[·•●▪■◆◇\s]+", "", line))
            if move and move not in {"无", "—", "-", "暂无"}:
                current.moves.append(move)
                in_move_section = True
            continue

        # Dictionary fallback: if line itself maps to a move name, keep it.
        maybe_move = lookup_move_zh_en(line, move_map)
        if (
            maybe_move != normalize_token(line)
            and maybe_move not in {"Unknown", "", "—"}
            and len(current.moves) < 6
        ):
            current.moves.append(maybe_move)
            continue

        # If we're already in move section, treat plain lines as moves unless they are known fields.
        if in_move_section:
            if re.match(r"^(特性|性格|努力|个体|道具|宝可梦|Ability|Nature|EVs|IVs)\b", line, flags=re.I):
                in_move_section = False
                continue
            if ":" in line or "：" in line:
                # Usually another field-like line misread by OCR, skip it.
                continue
            token = normalize_token(line)
            if token and token not in {"无", "—", "-", "暂无"}:
                current.moves.append(token)
            continue

    if current:
        blocks.append(current)
    return blocks


def parse_blocks(lines: List[str]) -> List[PokemonSet]:
    normalized = [normalize_space(x) for x in lines if normalize_space(x)]
    header_idx = [i for i, ln in enumerate(normalized) if _is_header_line(ln)]
    adjacent_pairs = sum(
        1 for a, b in zip(header_idx, header_idx[1:]) if b == a + 1
    )
    if len(header_idx) >= 4 and adjacent_pairs >= max(1, len(header_idx) // 3):
        paired = _parse_blocks_two_column(normalized)
        if paired:
            return paired
    return _parse_blocks_single(normalized)


def ocr_lines_from_image(image_path: Path, lang: str) -> List[str]:
    """
    Run OCR in a separate subprocess to avoid re-init issues.
    """
    payload = {"image_path": str(image_path), "lang": lang}
    worker_code = r"""
import json
import os
import sys

data = json.loads(sys.stdin.read())
image_path = data["image_path"]
lang = data.get("lang", "zh")
ocr_lang = "ch" if str(lang).lower().startswith("zh") else "en"

# Workarounds for some Paddle CPU/oneDNN/PIR execution errors
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["OMP_NUM_THREADS"] = "1"

merged = []

def add_text(t):
    if isinstance(t, str) and t.strip():
        merged.append(t.strip())

def absorb_result(result):
    if isinstance(result, list):
        for page in result:
            if isinstance(page, dict):
                for key in ("rec_texts", "texts", "text"):
                    val = page.get(key)
                    if isinstance(val, list):
                        for t in val:
                            add_text(t)
                    else:
                        add_text(val)
                continue

            if isinstance(page, list):
                for item in page:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        right = item[1]
                        if isinstance(right, (list, tuple)) and right:
                            add_text(right[0])
                        else:
                            add_text(right)
                    elif isinstance(item, dict):
                        for key in ("rec_text", "text"):
                            add_text(item.get(key))
                    else:
                        add_text(item)
                continue

            add_text(page)

paddle_error = None
try:
    from paddleocr import PaddleOCR  # type: ignore
    try:
        ocr = PaddleOCR(use_angle_cls=True, lang=ocr_lang, show_log=False)
    except Exception as exc:
        if "show_log" not in str(exc):
            raise
        ocr = PaddleOCR(use_angle_cls=True, lang=ocr_lang)
    try:
        result = ocr.ocr(image_path, cls=True)
    except Exception as exc:
        if "cls" in str(exc):
            try:
                result = ocr.ocr(image_path)
            except Exception:
                result = ocr.predict(image_path)
        else:
            raise
    absorb_result(result)
except Exception as exc:
    paddle_error = str(exc)

if not merged:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        rapid = RapidOCR()
        rapid_result, _ = rapid(image_path)
        if rapid_result:
            for line in rapid_result:
                # Typical row: [box, text, score]
                if isinstance(line, (list, tuple)) and len(line) >= 2:
                    add_text(line[1])
                elif isinstance(line, dict):
                    add_text(line.get("text"))
                else:
                    add_text(line)
    except Exception as rapid_exc:
        msg = f"OCR failed: Paddle={paddle_error}; RapidOCR={rapid_exc}"
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=True))
        raise SystemExit(0)

if not merged:
    msg = "OCR returned no text. Try a clearer screenshot."
    if paddle_error:
        msg = f"{msg} Paddle error: {paddle_error}"
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=True))
    raise SystemExit(0)

print(json.dumps({"ok": True, "lines": merged}, ensure_ascii=True))
"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", worker_code],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        check=False,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"OCR subprocess produced no output. stderr: {err}")

    last_line = raw.splitlines()[-1]
    try:
        obj = json.loads(last_line)
    except Exception as exc:
        raise RuntimeError(f"Unparseable OCR response: {last_line}") from exc

    if not obj.get("ok"):
        raise RuntimeError(obj.get("error", "Unknown OCR error"))
    return obj.get("lines", [])


def save_output(sets: List[PokemonSet], output_file: Path) -> None:
    output = "\n\n".join(p.to_pkhex_text() for p in sets)
    output_file.write_text(output + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PKHeX import text from a team screenshot (OCR) or OCR text."
    )
    parser.add_argument("--image", type=str, help="Team screenshot path")
    parser.add_argument("--text", type=str, help="OCR plain text file path")
    parser.add_argument("--output", type=str, default="pkhex_sets.txt", help="Output file")
    parser.add_argument("--lang", type=str, default="zh", help="OCR language: zh/en")
    parser.add_argument("--alpha", action="store_true", help="Set Alpha: Yes for all")
    parser.add_argument("--ball", type=str, default="", help="Set Ball for all")
    args = parser.parse_args()

    if not args.image and not args.text:
        raise SystemExit("Please provide --image or --text.")

    lines: List[str] = []
    if args.text:
        lines.extend(Path(args.text).read_text(encoding="utf-8").splitlines())
    if args.image:
        lines.extend(ocr_lines_from_image(Path(args.image), args.lang))

    sets = parse_blocks(lines)
    if not sets:
        raise SystemExit("No Pokémon sets parsed. Check screenshot clarity or debug via --text.")

    for s in sets:
        if args.alpha:
            s.alpha = True
        if args.ball:
            s.ball = args.ball

    save_output(sets, Path(args.output))
    print(f"Wrote {len(sets)} Pokémon set(s) to: {args.output}")


if __name__ == "__main__":
    main()
