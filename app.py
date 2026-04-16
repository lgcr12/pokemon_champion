from pathlib import Path
import html
import json
import importlib.util
import sys
import tempfile
import io
import zipfile
from copy import deepcopy
from difflib import unified_diff
from collections import Counter
import streamlit as st
import streamlit.components.v1 as components

from history_storage import append_entry, clear_all, load_history
from main import (
    ocr_lines_from_image,
    parse_blocks,
    PokemonSet,
    STAT_ORDER,
    translation_maps,
    ensure_move_latin,
    ensure_latin_field,
    dedupe_moves,
    nature_en_to_zh,
    has_cjk,
    lookup_en_zh,
)


st.set_page_config(
    page_title="PKHeX 队伍生成器",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

SS_BLOCKS = "pkhex_card_blocks"
SS_BLOCKS_ZH = "pkhex_card_blocks_zh"
SS_TITLES = "pkhex_card_titles"
SS_FULL = "pkhex_full_text"
SS_HISTORY = "pkhex_history"
SS_THEME = "pkhex_ui_theme"
SS_OCR_DEBUG_LINES = "pkhex_ocr_debug_lines"
SS_PARSE_DEBUG_TEXT = "pkhex_parse_debug_text"
SS_SETS = "pkhex_sets_struct"
SS_BATCH_RESULTS = "pkhex_batch_results"
SS_BATCH_ZIP = "pkhex_batch_zip"
SS_LOCKED_FIELDS = "pkhex_locked_fields"

if SS_BLOCKS not in st.session_state:
    st.session_state[SS_BLOCKS] = None
if SS_BLOCKS_ZH not in st.session_state:
    st.session_state[SS_BLOCKS_ZH] = None
if SS_TITLES not in st.session_state:
    st.session_state[SS_TITLES] = None
if SS_FULL not in st.session_state:
    st.session_state[SS_FULL] = None
if SS_THEME not in st.session_state:
    st.session_state[SS_THEME] = "Minimal (index)"
if SS_OCR_DEBUG_LINES not in st.session_state:
    st.session_state[SS_OCR_DEBUG_LINES] = []
if SS_PARSE_DEBUG_TEXT not in st.session_state:
    st.session_state[SS_PARSE_DEBUG_TEXT] = ""
if SS_SETS not in st.session_state:
    st.session_state[SS_SETS] = []
if SS_BATCH_RESULTS not in st.session_state:
    st.session_state[SS_BATCH_RESULTS] = []
if SS_BATCH_ZIP not in st.session_state:
    st.session_state[SS_BATCH_ZIP] = None
if SS_LOCKED_FIELDS not in st.session_state:
    st.session_state[SS_LOCKED_FIELDS] = []

st.session_state[SS_HISTORY] = load_history()


THEME_HINTS = """
**主题说明（在侧边栏切换）**  
- **极光 · 玻璃**：蓝青渐变，偏未来感。  
- **苔绿 · 自然**：青绿系，视觉更柔和。  
- **暖阳**：橙粉暖色，观感更活跃。  
- **暗夜 · 金**：深色底配金色高光。  
- **白雾 · 纸张**：浅色高对比，白天更易读。  
- **极简 · 纯白**：接近原始简洁风格。  
"""

THEME_ZH = {
    "Aurora (glass)": "极光 · 玻璃",
    "Moss (nature)": "苔绿 · 自然",
    "Solar (warm)": "暖阳",
    "Noir (gold)": "暗夜 · 金",
    "Lumen (paper)": "白雾 · 纸张",
    "Minimal (index)": "极简 · 纯白",
}


def apply_team_options(sets: list):
    for s in sets:
        if alpha_all:
            s.alpha = True
        if ball.strip():
            s.ball = ball.strip()


def push_history(
    label: str,
    blocks_en: list,
    blocks_zh: list,
    titles: list,
    full_text: str,
):
    st.session_state[SS_HISTORY] = append_entry(
        label, blocks_en, blocks_zh, titles, full_text
    )


def _clone_set(s: PokemonSet) -> PokemonSet:
    return deepcopy(s)


def _sync_outputs_from_sets(sets: list[PokemonSet], save_history_label: str | None = None):
    blocks_en = [s.to_pkhex_text() for s in sets]
    blocks_zh = [s.zh_reference_block() for s in sets]
    titles = [s.tag_title_en() for s in sets]
    output_text = "\n\n".join(blocks_en) + ("\n" if blocks_en else "")
    st.session_state[SS_SETS] = [_clone_set(s) for s in sets]
    st.session_state[SS_BLOCKS] = blocks_en
    st.session_state[SS_BLOCKS_ZH] = blocks_zh
    st.session_state[SS_TITLES] = titles
    st.session_state[SS_FULL] = output_text
    if save_history_label:
        push_history(save_history_label, blocks_en, blocks_zh, titles, output_text)


def _parse_sets_from_text(raw_text: str) -> list[PokemonSet]:
    lines = [x.rstrip() for x in (raw_text or "").splitlines() if x.strip()]
    if not lines:
        return []
    return parse_blocks(lines)


def _apply_locked_fields(
    old_sets: list[PokemonSet], new_sets: list[PokemonSet], locked_fields: list[str]
) -> list[PokemonSet]:
    if not old_sets or not new_sets or not locked_fields:
        return new_sets
    out = [_clone_set(s) for s in new_sets]
    for i in range(min(len(old_sets), len(out))):
        old_s = old_sets[i]
        cur_s = out[i]
        for field in locked_fields:
            if hasattr(cur_s, field) and hasattr(old_s, field):
                setattr(cur_s, field, deepcopy(getattr(old_s, field)))
    return out


def _move_quality(move_text: str, move_map: dict) -> str:
    t = (move_text or "").strip()
    if not t:
        return "未知"
    en = ensure_move_latin(t, move_map)
    if en and en != "Unknown":
        return "合法"
    if has_cjk(t):
        return "可疑"
    return "未知"


def _validate_sets(sets: list[PokemonSet]) -> list[tuple[str, str]]:
    _, ability_map, item_map, move_map = translation_maps()
    findings: list[tuple[str, str]] = []
    for idx, s in enumerate(sets, start=1):
        name = s.tag_title_en() or f"宝可梦#{idx}"
        ev_sum = sum(int(s.evs.get(k, 0) or 0) for k in STAT_ORDER)
        if ev_sum > 510:
            findings.append(("高", f"[{name}] 努力值总和 {ev_sum} 超过 510。"))
        for stat in STAT_ORDER:
            ev = int(s.evs.get(stat, 0) or 0)
            iv = int(s.ivs.get(stat, 31) or 31)
            if ev < 0 or ev > 252:
                findings.append(("中", f"[{name}] {stat} 努力值 {ev} 超出 0-252。"))
            if iv < 0 or iv > 31:
                findings.append(("中", f"[{name}] {stat} 个体值 {iv} 超出 0-31。"))
        if not (s.ability or "").strip():
            findings.append(("中", f"[{name}] 缺少特性。"))
        else:
            abil = ensure_latin_field(s.ability, ability_map)
            if abil == "Unknown":
                findings.append(("中", f"[{name}] 特性 `{s.ability}` 不在词典中。"))
        if not (s.nature or "").strip():
            findings.append(("中", f"[{name}] 缺少性格。"))
        item = (s.item or "").strip()
        if item and ensure_latin_field(item, item_map) == "Unknown":
            findings.append(("低", f"[{name}] 道具 `{item}` 无法映射到英文。"))
        raw_moves = [str(x).strip() for x in s.moves if str(x).strip()]
        moves = [m for m in dedupe_moves(raw_moves)]
        if len(raw_moves) != len(set(raw_moves)):
            findings.append(("中", f"[{name}] 招式存在重复。"))
        for mv in moves:
            q = _move_quality(mv, move_map)
            if q == "可疑":
                findings.append(("中", f"[{name}] 招式 `{mv}` 词典未命中，可能识别错误。"))
            elif q == "未知":
                findings.append(("低", f"[{name}] 招式 `{mv}` 未知。"))
    return findings


def _to_showdown_text(s: PokemonSet) -> str:
    poke_m, ability_m, item_m, move_m = translation_maps()
    species = ensure_latin_field(s.species, poke_m) or "Unknown"
    item = ensure_latin_field(s.item, item_m) if s.item else ""
    ability = ensure_latin_field(s.ability, ability_m) if s.ability else ""
    first = species
    if s.gender in {"M", "F"}:
        first += f" ({s.gender})"
    if item and item != "Unknown":
        first += f" @ {item}"
    lines = [first]
    lines.append(f"Ability: {ability if ability and ability != 'Unknown' else (s.ability or 'Blaze')}")
    lines.append("Level: 100")
    lines.append("Shiny: Yes")
    lines.append("EVs: " + " / ".join(f"{int(s.evs.get(k, 0) or 0)} {k}" for k in STAT_ORDER))
    lines.append(
        (s.nature if (s.nature or "").strip() else "Serious") + " Nature"
    )
    for mv in dedupe_moves([str(m).strip() for m in s.moves if str(m).strip()]):
        mv_en = ensure_move_latin(mv, move_m)
        lines.append(f"- {mv_en if mv_en and mv_en != 'Unknown' else mv}")
    return "\n".join(lines)


def _sets_to_json(sets: list[PokemonSet]) -> str:
    payload = []
    for s in sets:
        payload.append(
            {
                "species": s.species,
                "gender": s.gender,
                "item": s.item,
                "ball": s.ball,
                "ability": s.ability,
                "nature": s.nature,
                "evs": {k: int(s.evs.get(k, 0) or 0) for k in STAT_ORDER},
                "ivs": {k: int(s.ivs.get(k, 31) or 31) for k in STAT_ORDER},
                "moves": [str(m).strip() for m in s.moves if str(m).strip()],
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_batch_zip(batch_items: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        summary_rows = []
        for idx, item in enumerate(batch_items, start=1):
            name = item.get("name") or f"batch_{idx}"
            safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
            sets = item.get("sets") or []
            if not sets:
                summary_rows.append(f"{name}\t失败\t0")
                continue
            en_text = "\n\n".join([s.to_pkhex_text() for s in sets]) + "\n"
            showdown_text = "\n\n".join([_to_showdown_text(s) for s in sets]) + "\n"
            zf.writestr(f"{safe}.pkhex.txt", en_text.encode("utf-8"))
            zf.writestr(f"{safe}.showdown.txt", showdown_text.encode("utf-8"))
            zf.writestr(f"{safe}.json", _sets_to_json(sets).encode("utf-8"))
            summary_rows.append(f"{name}\t成功\t{len(sets)}")
        zf.writestr("summary.tsv", ("\n".join(summary_rows) + "\n").encode("utf-8"))
    return buffer.getvalue()


def _ev_team_stats(sets: list[PokemonSet]) -> dict[str, int]:
    out = {k: 0 for k in STAT_ORDER}
    for s in sets:
        for k in STAT_ORDER:
            out[k] += int(s.evs.get(k, 0) or 0)
    return out


def render_hero(theme: str):
    theme_label = THEME_ZH.get(theme, theme)
    st.markdown(
        f"""
<section class="showcase-shell">
  <div class="showcase-grid"></div>
  <div class="showcase-orb showcase-orb-a"></div>
  <div class="showcase-orb showcase-orb-b"></div>
  <div class="showcase-copy">
    <div class="showcase-eyebrow">PKHeX 队伍工作台</div>
    <h1 class="showcase-title">玻璃霓虹 OCR 工作台</h1>
    <p class="showcase-sub">
      上传队伍截图后，自动完成 OCR、字段整理、中英对照和 PKHeX 导出，
      让校对、复制和下载都集中在一个霓虹玻璃界面里完成。
    </p>
    <div class="showcase-badges">
      <span class="showcase-badge showcase-badge-strong">当前主题 · {html.escape(theme_label)}</span>
      <span class="showcase-badge">OCR 解析</span>
      <span class="showcase-badge">中英对照</span>
      <span class="showcase-badge">历史回看</span>
    </div>
    <div class="showcase-metrics">
      <div class="showcase-metric">
        <span>输入</span>
        <strong>截图 / OCR 文本</strong>
      </div>
      <div class="showcase-metric">
        <span>输出</span>
        <strong>PKHeX 导入文本</strong>
      </div>
      <div class="showcase-metric">
        <span>模式</span>
        <strong>先校对再导出</strong>
      </div>
    </div>
  </div>
  <div class="showcase-side">
    <div class="showcase-mini-card">
      <span class="showcase-mini-kicker">操作舱</span>
      <strong>识别 · 校对 · 导出</strong>
      <div class="dock-list">
        <div class="dock-item">
          <span class="dock-dot"></span>
          <div>
            <label>视觉主题</label>
            <p>默认启用玻璃霓虹风格，也可以在侧边栏即时切换。</p>
          </div>
        </div>
        <div class="dock-item">
          <span class="dock-dot"></span>
          <div>
            <label>OCR 调整</label>
            <p>识别语言、球种、Alpha 标记和调试信息都集中在一处。</p>
          </div>
        </div>
        <div class="dock-item">
          <span class="dock-dot"></span>
          <div>
            <label>结果流转</label>
            <p>先看双面板卡片，再复制或下载最终文本，避免直接盲导出。</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>
""",
        unsafe_allow_html=True,
    )


def render_workspace_panel(history_count: int, uploaded_name: str | None, show_debug: bool):
    upload_label = uploaded_name or "等待上传截图"
    debug_label = "已开启" if show_debug else "关闭"
    st.markdown(
        f"""
<div class="workspace-strip">
  <div class="workspace-card workspace-card-wide">
    <span class="workspace-label">当前截图</span>
    <strong>{html.escape(upload_label)}</strong>
    <p class="workspace-sub">从侧边栏上传截图后，就会进入 OCR 识别和卡片生成流程。</p>
  </div>
  <div class="workspace-card">
    <span class="workspace-label">历史记录</span>
    <strong>{history_count}</strong>
  </div>
  <div class="workspace-card">
    <span class="workspace-label">调试信息</span>
    <strong>{debug_label}</strong>
  </div>
  <div class="workspace-card">
    <span class="workspace-label">结果画布</span>
    <strong>双面板对照卡</strong>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def inject_global_css(theme: str):
    themes = {
        "Aurora (glass)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'DM Sans', system-ui, sans-serif;
    background:
      radial-gradient(900px 500px at 0% 0%, rgba(56, 189, 248, 0.2), transparent 55%),
      radial-gradient(800px 600px at 100% 0%, rgba(167, 139, 250, 0.16), transparent 50%),
      linear-gradient(165deg, #0c1222 0%, #111827 45%, #0f172a 100%);
    color: #e2e8f0;
}
.hero-title { color: #f8fafc; }
.hero-sub { color: #94a3b8; }
[data-testid="stSidebar"] {
    background: rgba(15, 23, 42, 0.72) !important;
    border-right: 1px solid rgba(148, 163, 184, 0.12);
}
.theme-hint { color: #64748b; font-size: 0.88rem; }
""",
        "Moss (nature)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'DM Sans', system-ui, sans-serif;
    background:
      radial-gradient(800px 480px at 10% 0%, rgba(52, 211, 153, 0.18), transparent 55%),
      radial-gradient(700px 500px at 90% 10%, rgba(45, 212, 191, 0.12), transparent 50%),
      linear-gradient(165deg, #0a1628 0%, #0f2918 50%, #0c1929 100%);
    color: #d1fae5;
}
.hero-title { color: #ecfdf5; }
.hero-sub { color: #6ee7b7; opacity: 0.85; }
[data-testid="stSidebar"] {
    background: rgba(6, 40, 32, 0.75) !important;
    border-right: 1px solid rgba(52, 211, 153, 0.15);
}
.theme-hint { color: #34d399; opacity: 0.75; font-size: 0.88rem; }
""",
        "Solar (warm)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'DM Sans', system-ui, sans-serif;
    background:
      radial-gradient(900px 520px at 0% 0%, rgba(251, 191, 36, 0.14), transparent 50%),
      radial-gradient(800px 500px at 100% 0%, rgba(244, 114, 182, 0.12), transparent 48%),
      linear-gradient(165deg, #1a0f14 0%, #24120a 45%, #1c1020 100%);
    color: #fde68a;
}
.hero-title { color: #fffbeb; }
.hero-sub { color: #fcd34d; opacity: 0.9; }
[data-testid="stSidebar"] {
    background: rgba(40, 20, 12, 0.78) !important;
    border-right: 1px solid rgba(251, 191, 36, 0.2);
}
.theme-hint { color: #fbbf24; opacity: 0.75; font-size: 0.88rem; }
""",
        "Noir (gold)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'DM Sans', system-ui, sans-serif;
    background:
      radial-gradient(600px 400px at 50% 0%, rgba(212, 175, 55, 0.08), transparent 55%),
      linear-gradient(180deg, #050505 0%, #0a0a0a 50%, #050508 100%);
    color: #e7e5e4;
}
.hero-title { color: #fafaf9; }
.hero-sub { color: #a8a29e; }
[data-testid="stSidebar"] {
    background: rgba(12, 10, 8, 0.92) !important;
    border-right: 1px solid rgba(212, 175, 55, 0.18);
}
.theme-hint { color: #78716c; font-size: 0.88rem; }
""",
        "Lumen (paper)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'DM Sans', system-ui, sans-serif;
    background:
      radial-gradient(1000px 600px at 20% 0%, rgba(59, 130, 246, 0.08), transparent 50%),
      linear-gradient(165deg, #f8fafc 0%, #f1f5f9 45%, #e2e8f0 100%);
    color: #0f172a;
}
.hero-title { color: #020617; }
.hero-sub { color: #475569; }
[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.92) !important;
    border-right: 1px solid #cbd5e1;
}
.theme-hint { color: #64748b; font-size: 0.88rem; }
""",
        "Minimal (index)": """
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #ffffff;
    color: #1a1a1a;
}
.hero-title { color: #111827; font-weight: 500; letter-spacing: -0.03em; }
.hero-sub { color: #6b7280; }
[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.9) !important;
    border-right: 1px solid #e5e7eb;
}
.theme-hint { color: #6b7280; font-size: 0.88rem; }
""",
    }
    body = themes.get(theme, themes["Aurora (glass)"])
    st.markdown(
        f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700&family=JetBrains+Mono:wght@400;500;600&display=swap');
{body}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{
    padding-top: 1.25rem;
    padding-bottom: 3rem;
    max-width: 1480px;
}}
.hero-title {{
    font-size: clamp(2.45rem, 4.2vw, 3.2rem);
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 0.4rem 0;
    line-height: 1.2;
    animation: fade-up 0.65s ease both;
}}
.hero-sub {{
    font-size: 1.26rem;
    margin: 0 0 1rem 0;
    max-width: 44rem;
    line-height: 1.5;
    animation: fade-up 0.75s ease both;
}}
.hero-badges {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0 0 1rem 0;
}}
.hero-badge {{
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184, 0.25);
    background: rgba(15, 23, 42, 0.38);
    color: #cbd5e1;
    font-size: 0.9rem;
    padding: 6px 12px;
    transition: transform 0.2s ease, border-color 0.2s ease, background 0.2s ease;
}}
.hero-badge:hover {{
    transform: translateY(-2px);
    border-color: rgba(100, 116, 139, 0.45);
    background: rgba(148, 163, 184, 0.12);
}}
[data-testid="stSidebar"] .block-container {{ padding-top: 1.1rem; }}
[data-testid="stSidebar"] h2 {{
    font-size: 1.12rem;
    font-weight: 600;
    margin-bottom: 0.85rem;
    padding-bottom: 0.45rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.15);
}}
div.stButton > button[kind="primary"] {{
    border-radius: 12px !important;
    font-weight: 600 !important;
    background: linear-gradient(135deg, #0ea5e9, #8b5cf6) !important;
    border: none !important;
    box-shadow: 0 4px 14px rgba(14, 165, 233, 0.35);
}}
html body[data-theme="minimal"] div.stButton > button[kind="primary"] {{
    background: #1a1a1a !important;
    box-shadow: none !important;
}}
div.stDownloadButton > button {{
    border-radius: 12px !important;
    font-weight: 600 !important;
    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
}}
div.stButton > button:hover,
div.stDownloadButton > button:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(17, 24, 39, 0.12);
}}
[data-testid="stExpander"] {{
    border: 1px solid rgba(148, 163, 184, 0.2) !important;
    border-radius: 12px !important;
    background: rgba(15, 23, 42, 0.22) !important;
}}
.empty-state {{
    text-align: center;
    padding: 3rem 1.5rem;
    border-radius: 16px;
    border: 1px dashed rgba(148, 163, 184, 0.25);
    background: rgba(15, 23, 42, 0.25);
    font-size: 1.05rem;
}}
@keyframes fade-up {{
    from {{ opacity: 0; transform: translateY(12px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}
</style>
""",
        unsafe_allow_html=True,
    )


def inject_layout_polish():
    st.markdown(
        """
<style>
p.hero-title,
p.hero-sub,
div.hero-badges {
    display: none !important;
}
.showcase-shell {
    position: relative;
    overflow: hidden;
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(280px, 0.85fr);
    gap: 18px;
    margin: 0 0 1.2rem 0;
    padding: 26px 28px;
    border-radius: 28px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background:
      linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03)),
      rgba(15, 23, 42, 0.14);
    box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
    backdrop-filter: blur(18px);
}
.showcase-grid {
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(125, 211, 252, 0.08) 1px, transparent 1px),
      linear-gradient(90deg, rgba(125, 211, 252, 0.08) 1px, transparent 1px);
    background-size: 28px 28px;
    mask-image: radial-gradient(circle at 40% 30%, black 15%, transparent 78%);
    pointer-events: none;
}
.showcase-orb {
    position: absolute;
    border-radius: 999px;
    pointer-events: none;
    filter: blur(10px);
    opacity: 0.62;
}
.showcase-orb-a {
    width: 240px;
    height: 240px;
    right: -70px;
    top: -80px;
    background: radial-gradient(circle, rgba(56, 189, 248, 0.34), transparent 72%);
}
.showcase-orb-b {
    width: 220px;
    height: 220px;
    right: 140px;
    bottom: -120px;
    background: radial-gradient(circle, rgba(244, 114, 182, 0.22), transparent 72%);
}
.showcase-copy, .showcase-side {
    position: relative;
    z-index: 1;
}
.showcase-eyebrow {
    display: inline-flex;
    margin-bottom: 0.85rem;
    padding: 0.42rem 0.78rem;
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: rgba(255,255,255,0.08);
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.showcase-title {
    margin: 0 0 0.55rem 0 !important;
    font-size: clamp(2.6rem, 4.5vw, 3.6rem);
    font-weight: 700;
    letter-spacing: -0.04em;
    line-height: 1.05;
    text-shadow: 0 0 30px rgba(96, 165, 250, 0.18);
}
.showcase-sub {
    margin: 0;
    max-width: 46rem;
    line-height: 1.7;
    font-size: 1.08rem;
}
.showcase-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 1.1rem;
}
.showcase-badge {
    color: inherit !important;
    background: rgba(255,255,255,0.08) !important;
    border-radius: 999px;
    border: 1px solid rgba(148, 163, 184, 0.25);
    font-size: 0.9rem;
    padding: 7px 12px;
}
.showcase-badge-strong {
    background: linear-gradient(135deg, rgba(14, 165, 233, 0.18), rgba(99, 102, 241, 0.16)) !important;
    border-color: rgba(56, 189, 248, 0.28) !important;
}
.showcase-metrics {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin-top: 1.15rem;
}
.showcase-metric {
    padding: 14px 16px;
    border-radius: 18px;
    border: 1px solid rgba(125, 211, 252, 0.14);
    background: linear-gradient(180deg, rgba(15, 23, 42, 0.24), rgba(15, 23, 42, 0.08));
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
}
.showcase-metric span {
    display: block;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    opacity: 0.72;
}
.showcase-metric strong {
    display: block;
    margin-top: 6px;
    font-size: 1rem;
    line-height: 1.45;
}
.showcase-side {
    display: flex;
}
.showcase-mini-card {
    width: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 10px;
    padding: 18px;
    border-radius: 22px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(255,255,255,0.08);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.1);
}
.dock-list {
    display: grid;
    gap: 14px;
}
.dock-item {
    display: grid;
    grid-template-columns: 12px 1fr;
    gap: 12px;
    align-items: start;
}
.dock-dot {
    width: 12px;
    height: 12px;
    margin-top: 0.25rem;
    border-radius: 999px;
    background: linear-gradient(135deg, #67e8f9, #818cf8);
    box-shadow: 0 0 16px rgba(103, 232, 249, 0.75);
}
.dock-item label {
    display: block;
    font-size: 0.85rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.8;
}
.dock-item p {
    margin: 0.2rem 0 0;
    font-size: 0.95rem;
    line-height: 1.6;
    opacity: 0.82;
}
.showcase-mini-kicker {
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    opacity: 0.76;
}
.showcase-mini-card strong {
    font-size: 1.24rem;
    line-height: 1.2;
}
.showcase-mini-card p {
    margin: 0;
    font-size: 0.96rem;
    line-height: 1.6;
    opacity: 0.84;
}
.workspace-strip {
    display: grid;
    grid-template-columns: 1.6fr repeat(3, minmax(0, 0.8fr));
    gap: 14px;
    margin: 0 0 1rem 0;
}
.workspace-card {
    padding: 15px 18px;
    border-radius: 20px;
    border: 1px solid rgba(148, 163, 184, 0.16);
    background: rgba(255,255,255,0.08);
    box-shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
    backdrop-filter: blur(12px);
}
.workspace-card-wide {
    background:
      linear-gradient(135deg, rgba(56, 189, 248, 0.12), rgba(129, 140, 248, 0.08)),
      rgba(255,255,255,0.08);
}
.workspace-label {
    display: block;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    opacity: 0.72;
}
.workspace-card strong {
    display: block;
    margin-top: 4px;
    font-size: 1.05rem;
    line-height: 1.45;
}
.workspace-sub {
    margin: 0.3rem 0 0;
    font-size: 0.92rem;
    line-height: 1.6;
    opacity: 0.8;
}
[data-testid="stSidebar"] .block-container {
    padding-top: 1rem;
}
[data-testid="stSidebar"] > div:first-child {
    background:
      radial-gradient(260px 200px at 10% 0%, rgba(56, 189, 248, 0.18), transparent 60%),
      radial-gradient(240px 220px at 100% 12%, rgba(168, 85, 247, 0.14), transparent 58%);
}
.sidebar-shell {
    display: grid;
    gap: 14px;
    margin-bottom: 0.8rem;
}
.sidebar-panel {
    padding: 14px 14px 10px;
    border-radius: 20px;
    border: 1px solid rgba(125, 211, 252, 0.14);
    background:
      linear-gradient(180deg, rgba(15, 23, 42, 0.34), rgba(15, 23, 42, 0.16)),
      rgba(255,255,255,0.04);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,0.08),
      0 18px 40px rgba(15, 23, 42, 0.18);
    backdrop-filter: blur(12px);
}
.sidebar-panel h3 {
    margin: 0 0 0.4rem 0;
    font-size: 1rem;
    letter-spacing: -0.02em;
}
.sidebar-panel p {
    margin: 0 0 0.65rem 0;
    font-size: 0.88rem;
    line-height: 1.55;
    opacity: 0.78;
}
[data-testid="stSidebar"] [data-baseweb="select"] > div,
[data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: rgba(255,255,255,0.08) !important;
    border: 1px solid rgba(125, 211, 252, 0.12) !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] {
    border-color: rgba(103, 232, 249, 0.28);
    background:
      linear-gradient(180deg, rgba(15, 23, 42, 0.26), rgba(15, 23, 42, 0.1)),
      rgba(255,255,255,0.04);
}
[data-testid="stSidebar"] .stButton > button,
[data-testid="stSidebar"] .stDownloadButton > button {
    width: 100%;
}
[data-testid="stFileUploader"] {
    border-radius: 18px;
    border: 1px dashed rgba(148, 163, 184, 0.35);
    background: rgba(255,255,255,0.06);
    box-shadow: inset 0 0 0 1px rgba(125, 211, 252, 0.05), 0 16px 40px rgba(15, 23, 42, 0.08);
}
[data-testid="stFileUploader"] section {
    padding: 0.8rem 0.45rem;
}
div.stButton > button,
div.stDownloadButton > button {
    min-height: 2.9rem !important;
    border-radius: 14px !important;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #0ea5e9, #6366f1) !important;
    box-shadow: 0 12px 28px rgba(14, 165, 233, 0.24) !important;
}
.result-shell {
    padding: 16px 18px 6px;
    border-radius: 24px;
    border: 1px solid rgba(125, 211, 252, 0.12);
    background:
      radial-gradient(700px 220px at 20% -20%, rgba(56, 189, 248, 0.12), transparent 60%),
      radial-gradient(540px 220px at 100% 0%, rgba(129, 140, 248, 0.12), transparent 58%),
      rgba(255,255,255,0.08);
    box-shadow: 0 22px 60px rgba(15, 23, 42, 0.1);
    backdrop-filter: blur(12px);
}
.empty-state {
    border-radius: 24px !important;
    background:
      radial-gradient(520px 180px at 50% -10%, rgba(56, 189, 248, 0.12), transparent 60%),
      rgba(255,255,255,0.08) !important;
}
@media (max-width: 980px) {
    .showcase-shell {
        grid-template-columns: 1fr;
        padding: 22px;
    }
    .showcase-metrics {
        grid-template-columns: 1fr;
    }
    .workspace-strip {
        grid-template-columns: 1fr;
    }
}
</style>
""",
        unsafe_allow_html=True,
    )


def iframe_theme_vars(theme: str) -> str:
    """CSS variables for card iframe (dark vs light)."""
    if theme == "Lumen (paper)":
        return """
        --bg-card: linear-gradient(155deg, #ffffff 0%, #f1f5f9 100%);
        --border-card: #cbd5e1;
        --text-main: #0f172a;
        --text-muted: #475569;
        --pre-bg: #f8fafc;
        --accent: #2563eb;
        --tag-bg: #dbeafe;
        --tag-text: #1e40af;
        --zh-accent: #059669;
        --head-bg: #e2e8f0;
        """
    if theme == "Minimal (index)":
        return """
        --bg-card: #ffffff;
        --border-card: #e5e7eb;
        --text-main: #111827;
        --text-muted: #6b7280;
        --pre-bg: #ffffff;
        --accent: #111827;
        --tag-bg: #f3f4f6;
        --tag-text: #111827;
        --zh-accent: #374151;
        --head-bg: #ffffff;
        """
    if theme == "Noir (gold)":
        return """
        --bg-card: linear-gradient(155deg, #141210 0%, #0c0a08 100%);
        --border-card: rgba(212, 175, 55, 0.25);
        --text-main: #fafaf9;
        --text-muted: #a8a29e;
        --pre-bg: rgba(0,0,0,0.35);
        --accent: #d4af37;
        --tag-bg: rgba(212, 175, 55, 0.15);
        --tag-text: #fcd34d;
        --zh-accent: #fbbf24;
        --head-bg: rgba(20, 18, 16, 0.95);
        """
    if theme == "Moss (nature)":
        return """
        --bg-card: linear-gradient(155deg, rgba(15, 45, 35, 0.92) 0%, rgba(10, 30, 24, 0.95) 100%);
        --border-card: rgba(52, 211, 153, 0.25);
        --text-main: #ecfdf5;
        --text-muted: #a7f3d0;
        --pre-bg: rgba(5, 25, 20, 0.45);
        --accent: #34d399;
        --tag-bg: rgba(52, 211, 153, 0.18);
        --tag-text: #6ee7b7;
        --zh-accent: #2dd4bf;
        --head-bg: rgba(8, 35, 28, 0.88);
        """
    if theme == "Solar (warm)":
        return """
        --bg-card: linear-gradient(155deg, rgba(55, 25, 20, 0.9) 0%, rgba(35, 15, 28, 0.94) 100%);
        --border-card: rgba(251, 191, 36, 0.22);
        --text-main: #fffbeb;
        --text-muted: #fde68a;
        --pre-bg: rgba(40, 15, 10, 0.5);
        --accent: #fbbf24;
        --tag-bg: rgba(251, 191, 36, 0.15);
        --tag-text: #fcd34d;
        --zh-accent: #fb7185;
        --head-bg: rgba(45, 20, 15, 0.9);
        """
    return """
        --bg-card: linear-gradient(155deg, rgba(30, 41, 59, 0.88) 0%, rgba(15, 23, 42, 0.94) 100%);
        --border-card: rgba(148, 163, 184, 0.2);
        --text-main: #f1f5f9;
        --text-muted: #94a3b8;
        --pre-bg: rgba(15, 23, 42, 0.55);
        --accent: #38bdf8;
        --tag-bg: rgba(56, 189, 248, 0.18);
        --tag-text: #7dd3fc;
        --zh-accent: #a78bfa;
        --head-bg: rgba(15, 23, 42, 0.72);
    """


def build_cards_html(
    theme: str,
    blocks_en: list,
    blocks_zh: list,
    titles: list,
    font_scale: float = 1.2,
) -> str:
    pairs = []
    for i, (en, zh, title) in enumerate(zip(blocks_en, blocks_zh, titles)):
        esc_en = html.escape(en)
        esc_zh = html.escape(zh)
        esc_title = html.escape(title)
        pairs.append(
            f"""
        <div class="pair-wrap" style="animation-delay: {i * 0.05:.2f}s">
            <div class="pair-inner">
                <section class="poke-panel en-panel">
                    <header class="panel-head">
                        <div class="title-row">
                            <span class="name-tag">{esc_title}</span>
                            <span class="slot-badge">英文 #{i + 1}</span>
                        </div>
                        <button type="button" class="btn-copy" data-target="pk-en-{i}">澶嶅埗鑻辨枃</button>
                    </header>
                    <pre class="poke-pre" id="pk-en-{i}">{esc_en}</pre>
                </section>
                <section class="poke-panel zh-panel">
                    <header class="panel-head">
                        <div class="title-row">
                            <span class="name-tag zh-label">涓枃瀵圭収</span>
                        </div>
                        <button type="button" class="btn-copy zh-copy" data-target="pk-zh-{i}">澶嶅埗瀵圭収</button>
                    </header>
                    <pre class="poke-pre zh-pre" id="pk-zh-{i}">{esc_zh}</pre>
                </section>
            </div>
        </div>
            """
        )
    joined_en = "\n\n".join(blocks_en)
    joined_all = "\n\n---\n\n".join(
        [f"銆愯嫳鏂?#{i+1}銆慭n{en}\n\n銆愪腑鏂囧鐓?#{i+1}銆慭n{zh}" for i, (en, zh) in enumerate(zip(blocks_en, blocks_zh))]
    )
    vars_css = iframe_theme_vars(theme)
    fs = f"{14.2 * font_scale:.1f}px"
    fs_small = f"{14 * font_scale:.1f}px"
    fs_tag = f"{17.2 * font_scale:.1f}px"

    return f"""
<div class="cards-root" style="font-size: {fs_small}; {vars_css}">
    <div class="cards-toolbar">
        <button type="button" class="btn-copy-all" id="copy-all-en">澶嶅埗鍏ㄩ儴锛堜粎鑻辨枃锛?/button>
        <button type="button" class="btn-copy-all secondary" id="copy-all-both">澶嶅埗鍏ㄩ儴锛堣嫳鏂?涓枃瀵圭収锛?/button>
        <span class="cards-hint">鎮仠鍗＄墖鏈変笂娴晥鏋?路 瀛椾綋澶у皬鍦ㄤ晶杈规爮璋冭妭</span>
    </div>
    <div class="pairs-grid">
        {"".join(pairs)}
    </div>
</div>
<script>
(function() {{
    const enText = {json.dumps(joined_en)};
    const bothText = {json.dumps(joined_all)};
    document.getElementById('copy-all-en').addEventListener('click', function() {{
        navigator.clipboard.writeText(enText).then(() => {{
            const o = this.textContent; this.textContent = '宸插鍒讹紒'; setTimeout(() => {{ this.textContent = o; }}, 1500);
        }}).catch(() => {{}});
    }});
    document.getElementById('copy-all-both').addEventListener('click', function() {{
        navigator.clipboard.writeText(bothText).then(() => {{
            const o = this.textContent; this.textContent = '宸插鍒讹紒'; setTimeout(() => {{ this.textContent = o; }}, 1500);
        }}).catch(() => {{}});
    }});
    document.querySelectorAll('.btn-copy').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
            const id = this.getAttribute('data-target');
            const el = document.getElementById(id);
            if (!el) return;
            navigator.clipboard.writeText(el.innerText).then(() => {{
                const o = this.textContent;
                this.textContent = '宸插鍒?;
                setTimeout(() => {{ this.textContent = o; }}, 1200);
            }}).catch(() => {{}});
        }});
    }});
}})();
</script>
<style>
    .cards-root {{
        font-family: 'JetBrains Mono', 'Consolas', monospace;
        color: var(--text-main);
    }}
    .cards-toolbar {{
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
        margin-bottom: 20px;
    }}
    .btn-copy-all {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        font-weight: 600;
        padding: 11px 18px;
        border-radius: 10px;
        border: 1px solid var(--accent);
        background: color-mix(in srgb, var(--accent) 22%, transparent);
        color: var(--text-main);
        cursor: pointer;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .btn-copy-all.secondary {{
        border-color: var(--zh-accent);
        background: color-mix(in srgb, var(--zh-accent) 18%, transparent);
    }}
    .btn-copy-all:hover {{
        transform: translateY(-2px);
        box-shadow: 0 10px 28px color-mix(in srgb, var(--accent) 35%, transparent);
    }}
    .cards-hint {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        color: var(--text-muted);
        margin-left: auto;
    }}
    .pairs-grid {{
        display: flex;
        flex-direction: column;
        gap: 22px;
    }}
    .pair-wrap {{
        animation: card-in 0.55s ease backwards;
    }}
    .pair-inner {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
        gap: 14px;
        align-items: stretch;
    }}
    @keyframes card-in {{
        from {{ opacity: 0; transform: translateY(14px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    .poke-panel {{
        background: var(--bg-card);
        border: 1px solid var(--border-card);
        border-radius: 16px;
        overflow: hidden;
        box-shadow: 0 6px 24px rgba(0,0,0,0.18);
        transition: transform 0.28s cubic-bezier(0.34, 1.45, 0.64, 1), box-shadow 0.28s ease;
    }}
    .pair-wrap:hover .poke-panel {{
        transform: translateY(-8px) scale(1.01);
        box-shadow: 0 18px 52px rgba(0,0,0,0.22);
    }}
    .panel-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 12px 14px;
        background: var(--head-bg);
        border-bottom: 1px solid var(--border-card);
    }}
    .title-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        min-width: 0;
    }}
    .name-tag {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_tag};
        font-weight: 700;
        padding: 6px 14px;
        border-radius: 999px;
        background: var(--tag-bg);
        color: var(--tag-text);
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }}
    .name-tag.zh-label {{
        background: color-mix(in srgb, var(--zh-accent) 22%, transparent);
        color: var(--zh-accent);
    }}
    .slot-badge {{
        font-family: 'DM Sans', sans-serif;
        font-size: {fs_small};
        font-weight: 700;
        opacity: 0.75;
    }}
    .btn-copy {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        font-weight: 600;
        padding: 8px 14px;
        border-radius: 10px;
        border: 1px solid var(--border-card);
        background: var(--pre-bg);
        color: var(--text-main);
        cursor: pointer;
        flex-shrink: 0;
        transition: transform 0.2s ease, border-color 0.2s ease;
    }}
    .btn-copy:hover {{
        border-color: var(--accent);
        transform: translateY(-1px);
    }}
    .poke-pre {{
        margin: 0;
        padding: 16px 18px 18px;
        font-size: {fs};
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--text-main);
        background: var(--pre-bg);
        max-height: 380px;
        overflow-y: auto;
    }}
    .zh-pre {{
        font-family: 'DM Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif;
        font-size: {fs};
        line-height: 1.65;
    }}
</style>
"""


def build_cards_html(
    theme: str,
    blocks_en: list,
    blocks_zh: list,
    titles: list,
    font_scale: float = 1.2,
) -> str:
    pairs = []
    for i, (en, zh, title) in enumerate(zip(blocks_en, blocks_zh, titles)):
        esc_en = html.escape(en)
        esc_zh = html.escape(zh)
        esc_title = html.escape(title)
        pairs.append(
            f"""
        <div class="pair-wrap" style="animation-delay: {i * 0.05:.2f}s">
            <div class="pair-inner">
                <section class="poke-panel en-panel">
                    <header class="panel-head">
                        <div class="title-row">
                            <span class="name-tag">{esc_title}</span>
                            <span class="slot-badge">英文 #{i + 1}</span>
                        </div>
                        <button type="button" class="btn-copy" data-target="pk-en-{i}">复制英文</button>
                    </header>
                    <pre class="poke-pre" id="pk-en-{i}">{esc_en}</pre>
                </section>
                <section class="poke-panel zh-panel">
                    <header class="panel-head">
                        <div class="title-row">
                            <span class="name-tag zh-label">中文对照</span>
                            <span class="slot-badge zh-slot">说明面板</span>
                        </div>
                        <button type="button" class="btn-copy zh-copy" data-target="pk-zh-{i}">复制对照</button>
                    </header>
                    <pre class="poke-pre zh-pre" id="pk-zh-{i}">{esc_zh}</pre>
                </section>
            </div>
        </div>
            """
        )

    joined_en = "\n\n".join(blocks_en)
    joined_all = "\n\n---\n\n".join(
        [f"【英文 #{i+1}】\n{en}\n\n【中文对照 #{i+1}】\n{zh}" for i, (en, zh) in enumerate(zip(blocks_en, blocks_zh))]
    )
    vars_css = iframe_theme_vars(theme)
    fs = f"{14.2 * font_scale:.1f}px"
    fs_small = f"{14 * font_scale:.1f}px"
    fs_tag = f"{17.2 * font_scale:.1f}px"

    return f"""
<div class="cards-root" style="font-size: {fs_small}; {vars_css}">
    <div class="cards-toolbar">
        <button type="button" class="btn-copy-all" id="copy-all-en">复制全部英文</button>
        <button type="button" class="btn-copy-all secondary" id="copy-all-both">复制全部中英对照</button>
        <span class="cards-hint">霓虹双面板结果区，可在侧栏调节字体大小</span>
    </div>
    <div class="pairs-grid">
        {"".join(pairs)}
    </div>
</div>
<script>
(function() {{
    const enText = {json.dumps(joined_en)};
    const bothText = {json.dumps(joined_all)};
    document.getElementById('copy-all-en').addEventListener('click', function() {{
        navigator.clipboard.writeText(enText).then(() => {{
            const o = this.textContent; this.textContent = '已复制'; setTimeout(() => {{ this.textContent = o; }}, 1500);
        }}).catch(() => {{}});
    }});
    document.getElementById('copy-all-both').addEventListener('click', function() {{
        navigator.clipboard.writeText(bothText).then(() => {{
            const o = this.textContent; this.textContent = '已复制'; setTimeout(() => {{ this.textContent = o; }}, 1500);
        }}).catch(() => {{}});
    }});
    document.querySelectorAll('.btn-copy').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
            const id = this.getAttribute('data-target');
            const el = document.getElementById(id);
            if (!el) return;
            navigator.clipboard.writeText(el.innerText).then(() => {{
                const o = this.textContent;
                this.textContent = '已复制';
                setTimeout(() => {{ this.textContent = o; }}, 1200);
            }}).catch(() => {{}});
        }});
    }});
}})();
</script>
<style>
    .cards-root {{
        font-family: 'JetBrains Mono', 'Consolas', monospace;
        color: var(--text-main);
    }}
    .cards-toolbar {{
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
        margin-bottom: 22px;
    }}
    .btn-copy-all {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        font-weight: 700;
        padding: 11px 18px;
        border-radius: 12px;
        border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
        background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 20%, transparent), rgba(15, 23, 42, 0.18));
        color: var(--text-main);
        cursor: pointer;
        transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
    }}
    .btn-copy-all.secondary {{
        border-color: color-mix(in srgb, var(--zh-accent) 48%, transparent);
        background: linear-gradient(135deg, color-mix(in srgb, var(--zh-accent) 18%, transparent), rgba(15, 23, 42, 0.18));
    }}
    .btn-copy-all:hover {{
        transform: translateY(-2px);
        box-shadow: 0 10px 28px color-mix(in srgb, var(--accent) 28%, transparent);
        border-color: color-mix(in srgb, var(--accent) 75%, transparent);
    }}
    .cards-hint {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        color: var(--text-muted);
        margin-left: auto;
    }}
    .pairs-grid {{
        display: flex;
        flex-direction: column;
        gap: 24px;
    }}
    .pair-wrap {{
        animation: card-in 0.55s ease backwards;
    }}
    .pair-inner {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
        gap: 18px;
        align-items: stretch;
    }}
    @keyframes card-in {{
        from {{ opacity: 0; transform: translateY(14px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    .poke-panel {{
        background: var(--bg-card);
        border: 1px solid var(--border-card);
        border-radius: 22px;
        overflow: hidden;
        position: relative;
        box-shadow: 0 14px 38px rgba(2, 6, 23, 0.32);
        transition: transform 0.28s cubic-bezier(0.34, 1.45, 0.64, 1), box-shadow 0.28s ease;
    }}
    .poke-panel::before {{
        content: "";
        position: absolute;
        inset: 0;
        background:
          radial-gradient(420px 120px at 0% 0%, color-mix(in srgb, var(--accent) 18%, transparent), transparent 60%),
          radial-gradient(320px 120px at 100% 0%, color-mix(in srgb, var(--zh-accent) 16%, transparent), transparent 58%);
        pointer-events: none;
    }}
    .en-panel {{
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 12%, transparent), 0 16px 38px rgba(2, 6, 23, 0.3);
    }}
    .zh-panel {{
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--zh-accent) 12%, transparent), 0 16px 38px rgba(2, 6, 23, 0.3);
    }}
    .pair-wrap:hover .poke-panel {{
        transform: translateY(-10px) scale(1.012);
        box-shadow: 0 20px 56px rgba(2, 6, 23, 0.4);
    }}
    .panel-head {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 14px 16px;
        background: var(--head-bg);
        border-bottom: 1px solid var(--border-card);
        position: relative;
        z-index: 1;
    }}
    .title-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        min-width: 0;
    }}
    .name-tag {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_tag};
        font-weight: 700;
        padding: 7px 14px;
        border-radius: 999px;
        background: var(--tag-bg);
        color: var(--tag-text);
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 16%, transparent);
    }}
    .name-tag.zh-label {{
        background: color-mix(in srgb, var(--zh-accent) 22%, transparent);
        color: var(--zh-accent);
        box-shadow: 0 0 20px color-mix(in srgb, var(--zh-accent) 16%, transparent);
    }}
    .slot-badge {{
        font-family: 'DM Sans', sans-serif;
        font-size: {fs_small};
        font-weight: 700;
        opacity: 0.76;
    }}
    .zh-slot {{
        color: var(--zh-accent);
    }}
    .btn-copy {{
        font-family: 'DM Sans', system-ui, sans-serif;
        font-size: {fs_small};
        font-weight: 700;
        padding: 8px 14px;
        border-radius: 12px;
        border: 1px solid var(--border-card);
        background: color-mix(in srgb, var(--pre-bg) 84%, transparent);
        color: var(--text-main);
        cursor: pointer;
        flex-shrink: 0;
        transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
    }}
    .btn-copy:hover {{
        border-color: var(--accent);
        transform: translateY(-1px);
        box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 16%, transparent);
    }}
    .zh-copy:hover {{
        border-color: var(--zh-accent);
        box-shadow: 0 0 20px color-mix(in srgb, var(--zh-accent) 16%, transparent);
    }}
    .poke-pre {{
        margin: 0;
        padding: 18px 20px 20px;
        font-size: {fs};
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
        color: var(--text-main);
        background: var(--pre-bg);
        position: relative;
        z-index: 1;
        max-height: 420px;
        overflow-y: auto;
    }}
    .zh-pre {{
        font-family: 'DM Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif;
        font-size: {fs};
        line-height: 1.65;
    }}
</style>
"""


def _looks_like_generated_result(lines: list[str]) -> bool:
    probe = "\n".join(lines[:80]).lower()
    flags = [
        ".relearnmoves=$suggestall",
        ".plusmoves=$suggestall",
        "pkhex",
        "对照（原文/ 中文",
        "复制英文",
        "中文对照",
    ]
    return sum(1 for key in flags if key in probe) >= 2


def _build_parse_debug_text(sets: list) -> str:
    if not sets:
        return "解析结果：0 只宝可梦"
    rows = [f"解析结果：{len(sets)} 只宝可梦"]
    for i, s in enumerate(sets, start=1):
        moves = ", ".join(s.moves) if s.moves else "（无）"
        rows.append(
            "\n".join(
                [
                    f"[{i}] {s.species or 'Unknown'}",
                    f"  - 道具: {s.item or '—'}",
                    f"  - 特性: {s.ability or '—'}",
                    f"  - 性格: {s.nature or '—'}",
                    "  - 努力值: "
                    + " / ".join(
                        f"{s.evs.get(k, 0)} {k}" for k in ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
                    ),
                    f"  - 招式: {moves}",
                ]
            )
        )
    return "\n\n".join(rows)


with st.sidebar:
    st.markdown(
        """
<div class="sidebar-shell">
  <div class="sidebar-panel">
    <h3>操作舱</h3>
    <p>调整主题、上传截图、设置 OCR 参数，然后开始生成。</p>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    theme_options = [
        "Aurora (glass)",
        "Moss (nature)",
        "Solar (warm)",
        "Noir (gold)",
        "Lumen (paper)",
        "Minimal (index)",
    ]
    ui_theme = st.selectbox(
        "界面主题",
        theme_options,
        index=theme_options.index(st.session_state[SS_THEME])
        if st.session_state[SS_THEME] in theme_options
        else 0,
        format_func=lambda k: THEME_ZH.get(k, k),
        help="控制背景、卡片和整体氛围。",
    )
    st.session_state[SS_THEME] = ui_theme

    font_scale = st.slider("卡片字体大小", 1.0, 1.9, 1.45, 0.05)
    input_mode = st.radio(
        "输入模式",
        ["截图 OCR", "纯文本粘贴", "混合修正", "批量截图"],
        index=0,
        help="纯文本可直接粘贴识别结果；混合修正可在 OCR 基础上补充文本。",
    )
    lang = st.selectbox(
        "识别语言",
        ["zh", "en"],
        index=0,
        format_func=lambda x: "中文" if x == "zh" else "英文",
    )
    alpha_all = st.toggle("全部标记为 Alpha", value=False)
    ball = st.text_input(
        "球种（应用于全部）",
        value="",
        placeholder="例如：计时球",
    )
    locked_fields = st.multiselect(
        "字段锁定（重新解析时保留）",
        ["item", "ball", "ability", "nature", "evs", "ivs", "moves"],
        default=st.session_state.get(SS_LOCKED_FIELDS, []),
        help="锁定后，再次点击开始生成会保留这些字段。",
    )
    st.session_state[SS_LOCKED_FIELDS] = locked_fields

    uploaded = None
    uploaded_batch = []
    raw_text = ""
    if input_mode in {"截图 OCR", "混合修正"}:
        uploaded = st.file_uploader(
            "队伍截图",
            type=["png", "jpg", "jpeg", "webp"],
            help="尽量包含道具、特性、性格、努力值和招式区域。",
        )
    if input_mode == "批量截图":
        uploaded_batch = st.file_uploader(
            "批量队伍截图",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help="可一次处理多张图并导出 ZIP。",
        )
    if input_mode in {"纯文本粘贴", "混合修正"}:
        raw_text = st.text_area(
            "识别文本/手工文本",
            height=180,
            placeholder="在这里粘贴 OCR 文本或 Showdown/PKHeX 文本。",
        )

    parse_btn = st.button("开始生成", type="primary", use_container_width=True)
    show_debug = st.toggle("显示 OCR 调试信息", value=False, help="查看 OCR 原始行与解析字段。")

    st.markdown(
        """
<div class="sidebar-panel">
  <h3>历史记录</h3>
  <p>快速恢复最近结果；完整列表仍然可以从历史页面查看。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    hist = st.session_state.get(SS_HISTORY) or []
    if hist:
        search_kw = st.text_input("搜索历史", value="", placeholder="按文件名、时间、宝可梦名检索")
        hist_pick = hist[:200]
        if search_kw.strip():
            k = search_kw.strip().lower()
            hist_pick = [
                h
                for h in hist_pick
                if k in (h.get("label", "").lower() + " " + " ".join(h.get("titles") or []).lower())
            ]
        hist_pick = hist_pick[:40]
        labels = [f"{h['at']} - {h['label']}" for h in hist_pick]
        pick = st.selectbox("恢复某次结果", ["请选择"] + labels, index=0)
        if pick != "请选择" and st.button("载入所选结果", use_container_width=True):
            idx = labels.index(pick)
            ent = hist_pick[idx]
            st.session_state[SS_BLOCKS] = ent["blocks_en"]
            st.session_state[SS_BLOCKS_ZH] = ent.get("blocks_zh") or []
            st.session_state[SS_TITLES] = ent.get("titles") or []
            st.session_state[SS_FULL] = ent["full"]
            st.session_state[SS_SETS] = _parse_sets_from_text(ent["full"])
            st.rerun()
        if len(labels) >= 2:
            diff_left = st.selectbox("对比 A", labels, key="hist_diff_a")
            diff_right = st.selectbox("对比 B", labels, index=1, key="hist_diff_b")
            if st.button("对比差异", use_container_width=True):
                left = hist_pick[labels.index(diff_left)]["full"].splitlines()
                right = hist_pick[labels.index(diff_right)]["full"].splitlines()
                diff = "\n".join(
                    unified_diff(left, right, fromfile="A", tofile="B", lineterm="")
                )
                st.session_state["history_diff_text"] = diff or "两次结果完全一致。"
        if st.session_state.get("history_diff_text"):
            with st.expander("历史差异结果", expanded=False):
                st.code(st.session_state["history_diff_text"], language="diff")
        if st.button("清空历史", use_container_width=True):
            clear_all()
            st.session_state[SS_HISTORY] = []
            st.rerun()
    else:
        st.caption("暂时没有保存记录，生成成功后会自动加入。")

    st.markdown(
        """
<div class="sidebar-panel">
  <h3>运行环境</h3>
  <p>确认当前 Python 环境和 OCR 依赖状态。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    with st.expander("运行环境"):
        st.caption(sys.executable)
        spec = importlib.util.find_spec("paddleocr")
        st.caption("PaddleOCR: " + ("已安装" if spec else "未找到"))

inject_global_css(ui_theme)
inject_layout_polish()
render_hero(ui_theme)
render_workspace_panel(
    history_count=len(st.session_state.get(SS_HISTORY) or []),
    uploaded_name=uploaded.name if uploaded else None,
    show_debug=show_debug,
)

st.markdown('<p class="hero-title">PKHeX 队伍生成器</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="hero-sub">上传配队截图自动识别，导出英文 PKHeX 文本，并提供中文对照。</p>',
    unsafe_allow_html=True,
)
st.markdown(
    """
<div class="hero-badges">
  <span class="hero-badge">双语卡片展示</span>
  <span class="hero-badge">本地历史记录</span>
  <span class="hero-badge">按宝可梦筛选</span>
</div>
""",
    unsafe_allow_html=True,
)
with st.expander("主题说明", expanded=False):
    st.markdown(THEME_HINTS)

try:
    st.page_link("pages/2_历史记录.py", label="打开永久历史记录页")
except Exception:
    pass

results_area = st.container()

if parse_btn:
    with results_area:
        try:
            parsed_sets: list[PokemonSet] = []
            ocr_lines: list[str] = []
            st.session_state[SS_BATCH_RESULTS] = []
            st.session_state[SS_BATCH_ZIP] = None

            if input_mode == "截图 OCR":
                if not uploaded:
                    st.warning("请先上传截图。")
                else:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=Path(uploaded.name).suffix
                    ) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = Path(tmp.name)
                    ocr_lines = ocr_lines_from_image(tmp_path, lang=lang)
                    if _looks_like_generated_result(ocr_lines):
                        st.warning(
                            "检测到截图更像是本工具结果页，建议改传游戏原始配队截图。"
                        )
                    parsed_sets = parse_blocks(ocr_lines)
            elif input_mode == "纯文本粘贴":
                parsed_sets = _parse_sets_from_text(raw_text)
                ocr_lines = [x for x in (raw_text or "").splitlines() if x.strip()]
            elif input_mode == "混合修正":
                if raw_text.strip():
                    parsed_sets = _parse_sets_from_text(raw_text)
                    ocr_lines = [x for x in raw_text.splitlines() if x.strip()]
                elif uploaded:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=Path(uploaded.name).suffix
                    ) as tmp:
                        tmp.write(uploaded.getbuffer())
                        tmp_path = Path(tmp.name)
                    ocr_lines = ocr_lines_from_image(tmp_path, lang=lang)
                    parsed_sets = parse_blocks(ocr_lines)
                else:
                    st.warning("混合修正模式下，请至少提供截图或文本。")
            else:  # 批量截图
                if not uploaded_batch:
                    st.warning("请先上传至少一张批量截图。")
                else:
                    batch_results = []
                    for file in uploaded_batch:
                        one_sets = []
                        one_lines = []
                        try:
                            with tempfile.NamedTemporaryFile(
                                delete=False, suffix=Path(file.name).suffix
                            ) as tmp:
                                tmp.write(file.getbuffer())
                                tmp_path = Path(tmp.name)
                            one_lines = ocr_lines_from_image(tmp_path, lang=lang)
                            one_sets = parse_blocks(one_lines)
                            apply_team_options(one_sets)
                        except Exception:
                            one_sets = []
                        batch_results.append({"name": file.name, "sets": one_sets, "lines": one_lines})
                    st.session_state[SS_BATCH_RESULTS] = batch_results
                    st.session_state[SS_BATCH_ZIP] = _build_batch_zip(batch_results)
                    combined = []
                    for it in batch_results:
                        for s in it["sets"]:
                            combined.append(s)
                    parsed_sets = combined
                    ocr_lines = []

            st.session_state[SS_OCR_DEBUG_LINES] = ocr_lines
            st.session_state[SS_PARSE_DEBUG_TEXT] = _build_parse_debug_text(parsed_sets)
            if parsed_sets:
                apply_team_options(parsed_sets)
                parsed_sets = _apply_locked_fields(
                    st.session_state.get(SS_SETS) or [],
                    parsed_sets,
                    st.session_state.get(SS_LOCKED_FIELDS) or [],
                )
                save_label = uploaded.name if uploaded else input_mode
                _sync_outputs_from_sets(parsed_sets, save_history_label=save_label)
            else:
                st.session_state[SS_SETS] = []
                st.session_state[SS_BLOCKS] = None
                st.session_state[SS_BLOCKS_ZH] = None
                st.session_state[SS_TITLES] = None
                st.session_state[SS_FULL] = None
                if input_mode != "批量截图":
                    st.warning("未能解析出宝可梦队伍，请换更清晰截图或补充文本。")
        except Exception as exc:
            st.session_state[SS_SETS] = []
            st.session_state[SS_BLOCKS] = None
            st.session_state[SS_BLOCKS_ZH] = None
            st.session_state[SS_TITLES] = None
            st.session_state[SS_FULL] = None
            st.session_state[SS_OCR_DEBUG_LINES] = []
            st.session_state[SS_PARSE_DEBUG_TEXT] = ""
            st.error(f"处理失败：{exc}")

def _titles_from_en_blocks(blocks: list) -> list:
    out = []
    for b in blocks:
        first = b.split("\n")[0].strip()
        if " @ " in first:
            first = first.split(" @ ")[0].strip()
        out.append((first[:64] or "宝可梦").strip())
    return out


with results_area:
    if show_debug:
        with st.expander("OCR 调试输出", expanded=True):
            ocr_dbg = st.session_state.get(SS_OCR_DEBUG_LINES) or []
            if ocr_dbg:
                st.caption("OCR 原始行：")
                st.code("\n".join(f"{i+1:02d}. {t}" for i, t in enumerate(ocr_dbg)), language="text")
            else:
                st.caption("暂无 OCR 原始行。")
            parse_dbg = st.session_state.get(SS_PARSE_DEBUG_TEXT) or ""
            if parse_dbg:
                st.caption("解析字段：")
                st.code(parse_dbg, language="text")

    sets = st.session_state.get(SS_SETS) or []
    blocks = st.session_state.get(SS_BLOCKS)
    blocks_zh = list(st.session_state.get(SS_BLOCKS_ZH) or [])
    titles = list(st.session_state.get(SS_TITLES) or [])
    output_text = st.session_state.get(SS_FULL)
    if sets:
        with st.expander("字段纠错与一键应用", expanded=False):
            source_idx = st.selectbox(
                "一键应用来源宝可梦",
                list(range(len(sets))),
                format_func=lambda x: f"#{x+1} {sets[x].tag_title_en()}",
                key="bulk_source_idx",
            )
            apply_fields = st.multiselect(
                "应用字段",
                ["item", "ball", "ability", "nature", "evs", "ivs", "moves"],
                default=["ability", "nature", "evs", "moves"],
                key="bulk_apply_fields",
            )
            if st.button("把选中字段应用到全队", use_container_width=True):
                src = sets[source_idx]
                for i, s in enumerate(sets):
                    if i == source_idx:
                        continue
                    for f in apply_fields:
                        setattr(s, f, deepcopy(getattr(src, f)))
                _sync_outputs_from_sets(sets)
                st.success("已应用到全队。")
                st.rerun()

            tabs = st.tabs([f"#{i+1} {s.tag_title_en()}" for i, s in enumerate(sets)])
            for i, (tab, s) in enumerate(zip(tabs, sets)):
                with tab:
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        s.species = st.text_input("宝可梦", value=s.species or "", key=f"ed_species_{i}")
                        s.item = st.text_input("道具", value=s.item or "", key=f"ed_item_{i}")
                        s.ball = st.text_input("球种", value=s.ball or "", key=f"ed_ball_{i}")
                    with c2:
                        s.ability = st.text_input("特性", value=s.ability or "", key=f"ed_ability_{i}")
                        s.nature = st.text_input("性格(英文)", value=s.nature or "", key=f"ed_nature_{i}")
                        s.gender = st.selectbox("性别", ["", "M", "F"], index=(["", "M", "F"].index(s.gender) if s.gender in {"M", "F"} else 0), key=f"ed_gender_{i}")
                    with c3:
                        moves_text = st.text_area(
                            "招式（每行一条）",
                            value="\n".join([str(m).strip() for m in s.moves if str(m).strip()]),
                            height=140,
                            key=f"ed_moves_{i}",
                        )
                        s.moves = [x.strip() for x in moves_text.splitlines() if x.strip()]
                    ev_cols = st.columns(6)
                    for j, stat in enumerate(STAT_ORDER):
                        with ev_cols[j]:
                            s.evs[stat] = st.number_input(
                                f"EV-{stat}",
                                min_value=0,
                                max_value=252,
                                value=int(s.evs.get(stat, 0) or 0),
                                step=1,
                                key=f"ed_ev_{i}_{stat}",
                            )
                    iv_cols = st.columns(6)
                    for j, stat in enumerate(STAT_ORDER):
                        with iv_cols[j]:
                            s.ivs[stat] = st.number_input(
                                f"IV-{stat}",
                                min_value=0,
                                max_value=31,
                                value=int(s.ivs.get(stat, 31) or 31),
                                step=1,
                                key=f"ed_iv_{i}_{stat}",
                            )
            if st.button("保存编辑并刷新结果", type="primary", use_container_width=True):
                _sync_outputs_from_sets(sets)
                st.success("编辑已保存。")
                st.rerun()

        findings = _validate_sets(sets)
        with st.expander("智能冲突检测与招式合法性", expanded=False):
            sev_order = {"高": 0, "中": 1, "低": 2}
            findings = sorted(findings, key=lambda x: sev_order.get(x[0], 9))
            if not findings:
                st.success("未发现明显冲突。")
            else:
                st.warning(f"发现 {len(findings)} 条问题，请优先处理高/中等级。")
                for sev, msg in findings:
                    badge = "🔴" if sev == "高" else ("🟠" if sev == "中" else "🟡")
                    st.write(f"{badge} [{sev}] {msg}")

            _, _, _, move_map = translation_maps()
            st.markdown("**招式来源校验**")
            for i, s in enumerate(sets, start=1):
                move_rows = []
                for mv in dedupe_moves([str(m).strip() for m in s.moves if str(m).strip()]):
                    q = _move_quality(mv, move_map)
                    en = ensure_move_latin(mv, move_map)
                    move_rows.append(f"- #{i} `{mv}` → `{en}`（{q}）")
                if move_rows:
                    st.markdown("\n".join(move_rows))

        with st.expander("队伍统计看板", expanded=False):
            ev_total = _ev_team_stats(sets)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("队伍数量", len(sets))
            with c2:
                avg_speed = int(sum(int(s.evs.get("Spe", 0) or 0) for s in sets) / max(1, len(sets)))
                st.metric("平均速度EV", avg_speed)
            with c3:
                total_moves = sum(len([m for m in s.moves if str(m).strip()]) for s in sets)
                st.metric("总招式数", total_moves)
            st.bar_chart(ev_total)
            natures = Counter([(s.nature or "—") for s in sets])
            st.bar_chart(dict(natures))
            move_counter = Counter()
            for s in sets:
                for mv in [str(m).strip() for m in s.moves if str(m).strip()]:
                    move_counter[mv] += 1
            top_moves = dict(move_counter.most_common(10))
            if top_moves:
                st.bar_chart(top_moves)

    if blocks and output_text:
        st.markdown('<div class="result-shell">', unsafe_allow_html=True)
        if len(blocks_zh) != len(blocks):
            blocks_zh = [
                "（此历史记录无中文对照缓存，请对当前截图重新点击「开始生成」。）\n"
                for _ in blocks
            ]
        if len(titles) != len(blocks):
            titles = _titles_from_en_blocks(blocks)
        h = min(3200, 480 + len(blocks) * 520)
        components.html(
            build_cards_html(
                ui_theme, blocks, blocks_zh, titles, font_scale=font_scale
            ),
            height=h,
            scrolling=True,
        )
        st.download_button(
            "下载结果文本",
            data=output_text.encode("utf-8"),
            file_name="pkhex_sets.txt",
            mime="text/plain",
            use_container_width=True,
        )
        showdown_text = "\n\n".join([_to_showdown_text(s) for s in sets]) + ("\n" if sets else "")
        st.download_button(
            "下载 Showdown 文本",
            data=showdown_text.encode("utf-8"),
            file_name="showdown_sets.txt",
            mime="text/plain",
            use_container_width=True,
        )
        st.download_button(
            "下载 JSON 结构化数据",
            data=_sets_to_json(sets).encode("utf-8"),
            file_name="team_sets.json",
            mime="application/json",
            use_container_width=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
    batch_results = st.session_state.get(SS_BATCH_RESULTS) or []
    batch_zip = st.session_state.get(SS_BATCH_ZIP)
    if batch_results:
        with st.expander("批量处理结果", expanded=True):
            rows = []
            for it in batch_results:
                rows.append(
                    {
                        "文件名": it.get("name"),
                        "状态": "成功" if it.get("sets") else "失败",
                        "识别宝可梦数": len(it.get("sets") or []),
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
            if batch_zip:
                st.download_button(
                    "下载批量结果 ZIP（PKHeX/Showdown/JSON）",
                    data=batch_zip,
                    file_name="batch_export.zip",
                    mime="application/zip",
                    use_container_width=True,
                )
    elif not parse_btn:
        st.markdown(
            '<div class="empty-state">请在左侧边栏上传截图，然后点击 <strong>开始生成</strong>。</div>',
            unsafe_allow_html=True,
        )

