"""
独立页面：永久历史记录（读写 data/pkhex_history.json）
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from history_storage import (
    MAX_ENTRIES,
    clear_all,
    delete_entry,
    history_file_path,
    load_history,
)

SS_BLOCKS = "pkhex_card_blocks"
SS_BLOCKS_ZH = "pkhex_card_blocks_zh"
SS_TITLES = "pkhex_card_titles"
SS_FULL = "pkhex_full_text"


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _first_line_name(text: str) -> str:
    first = (text or "").split("\n", 1)[0].strip()
    if " @ " in first:
        first = first.split(" @ ", 1)[0].strip()
    return first.strip(" -:\t") or "未知宝可梦"


def _extract_species_zh_from_ref(zh_block: str) -> str:
    for raw in (zh_block or "").splitlines():
        line = raw.strip()
        if "宝可梦" not in line:
            continue
        if ":" in line:
            right = line.split(":", 1)[1].strip()
        elif "：" in line:
            right = line.split("：", 1)[1].strip()
        else:
            continue
        if "→" in right:
            left = right.split("→", 1)[0].strip()
            if left:
                return left
        if right:
            return right
    return ""


def _extract_species_en_from_en_block(en_block: str) -> str:
    first = _first_line_name(en_block or "")
    if " (" in first:
        first = first.split(" (", 1)[0].strip()
    return first.strip()


def _entry_variants(ent: dict) -> list[dict]:
    blocks_en = list(ent.get("blocks_en") or [])
    blocks_zh = list(ent.get("blocks_zh") or [])
    titles = list(ent.get("titles") or [])
    out = []
    for i, en in enumerate(blocks_en):
        zh = blocks_zh[i] if i < len(blocks_zh) else "（缺少中文对照缓存）"
        title = titles[i] if i < len(titles) else _first_line_name(en)
        zh_name = _extract_species_zh_from_ref(zh)
        en_name = _extract_species_en_from_en_block(en) or _first_line_name(title)
        mon_key = zh_name or en_name or _first_line_name(title)
        alias = [x for x in {mon_key, zh_name, en_name, _first_line_name(title)} if x]
        out.append(
            {
                "mon": mon_key,
                "alias": alias,
                "title": title,
                "zh": zh,
                "en": en,
                "at": str(ent.get("at") or "—"),
                "label": str(ent.get("label") or "—"),
                "entry_id": str(ent.get("id") or ""),
                "idx": i + 1,
            }
        )
    return out


def _build_grouped_cards_html(groups: list[tuple[str, list[dict]]]) -> str:
    sections = []
    for gidx, (mon_name, variants) in enumerate(groups):
        cards = []
        for cidx, var in enumerate(variants):
            esc_title = html.escape(var["title"])
            esc_mon = html.escape(mon_name)
            esc_zh = html.escape(var["zh"])
            esc_en = html.escape(var["en"])
            esc_meta = html.escape(f"{var['at']} · {var['label']} · 第{var['idx']}只")
            cid = f"v-{gidx}-{cidx}"
            cards.append(
                f"""
<article class="flip-card" data-id="{cid}">
  <div class="flip-inner">
    <section class="flip-face face-zh">
      <div class="face-head">
        <span class="chip chip-mon">{esc_mon}</span>
        <span class="chip chip-side">中文配置</span>
      </div>
      <pre id="{cid}-zh">{esc_zh}</pre>
      <div class="meta-row">
        <span>{esc_meta}</span>
        <button type="button" class="copy-btn" data-target="{cid}-zh">复制中文</button>
      </div>
    </section>
    <section class="flip-face face-en">
      <div class="face-head">
        <span class="chip chip-mon">{esc_title}</span>
        <span class="chip chip-side en">英文配置</span>
      </div>
      <pre id="{cid}-en">{esc_en}</pre>
      <div class="meta-row">
        <span>{esc_meta}</span>
        <button type="button" class="copy-btn" data-target="{cid}-en">复制英文</button>
      </div>
    </section>
  </div>
</article>
                """
            )
        sections.append(
            f"""
<section class="mon-group">
  <header class="group-head">
    <h3>{html.escape(mon_name)}</h3>
    <span class="group-count">{len(variants)} 套配置</span>
  </header>
  <div class="cards-grid">
    {"".join(cards)}
  </div>
</section>
            """
        )
    return f"""
<div class="hist-cards-root">
  <div class="board-tip">点击卡片翻转：中文配置 ⇄ 英文配置</div>
  {"".join(sections)}
</div>
<script>
(function() {{
  document.querySelectorAll('.flip-card').forEach(function(card) {{
    card.addEventListener('click', function(ev) {{
      if (ev.target && ev.target.closest('.copy-btn')) return;
      card.classList.toggle('is-flipped');
    }});
  }});
  document.querySelectorAll('.copy-btn').forEach(function(btn) {{
    btn.addEventListener('click', function(ev) {{
      ev.preventDefault();
      ev.stopPropagation();
      const target = btn.getAttribute('data-target');
      const el = document.getElementById(target);
      if (!el) return;
      navigator.clipboard.writeText(el.innerText || '').then(function() {{
        const old = btn.textContent;
        btn.textContent = '已复制';
        setTimeout(function() {{ btn.textContent = old; }}, 1200);
      }}).catch(function() {{}});
    }});
  }});
}})();
</script>
<style>
  .hist-cards-root {{
    --bg-1: #ffffff;
    --bg-2: #f9fafb;
    --line: #e5e7eb;
    --txt: #111827;
    --muted: #6b7280;
    --chip: #f3f4f6;
    --chip-en: #e5e7eb;
    --chip-t: #111827;
  }}
  .board-tip {{
    margin: 6px 0 14px;
    color: var(--muted);
    font-size: 15px;
  }}
  .mon-group {{
    margin: 0 0 20px;
    padding: 14px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: #ffffff;
  }}
  .group-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }}
  .group-head h3 {{
    margin: 0;
    color: #111827;
    font-size: 22px;
  }}
  .group-count {{
    color: var(--muted);
    font-size: 14px;
  }}
  .cards-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 12px;
  }}
  .flip-card {{
    perspective: 1200px;
    min-height: 320px;
    cursor: pointer;
    transition: transform 0.2s ease;
  }}
  .flip-card:hover {{
    transform: translateY(-4px);
  }}
  .flip-inner {{
    position: relative;
    width: 100%;
    min-height: 320px;
    transform-style: preserve-3d;
    transition: transform 0.5s ease;
  }}
  .flip-card.is-flipped .flip-inner {{ transform: rotateY(180deg); }}
  .flip-face {{
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: linear-gradient(155deg, var(--bg-2), var(--bg-1));
    overflow: hidden;
    backface-visibility: hidden;
    box-shadow: 0 8px 20px rgba(17, 24, 39, 0.06);
  }}
  .face-en {{ transform: rotateY(180deg); }}
  .face-head {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--line);
    background: #f9fafb;
  }}
  .chip {{
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 14px;
    color: var(--chip-t);
    background: var(--chip);
    white-space: nowrap;
  }}
  .chip-side {{
    color: #166534;
    background: #dcfce7;
  }}
  .chip-side.en {{
    color: #1f2937;
    background: var(--chip-en);
  }}
  .flip-face pre {{
    margin: 0;
    padding: 12px;
    font-size: 14.6px;
    line-height: 1.62;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--txt);
    flex: 1;
    overflow-y: auto;
  }}
  .meta-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 9px 12px;
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 13.5px;
  }}
  .copy-btn {{
    border: 1px solid var(--line);
    background: #ffffff;
    color: var(--txt);
    border-radius: 10px;
    padding: 5px 9px;
    font-size: 14px;
    cursor: pointer;
    flex-shrink: 0;
    transition: transform 0.2s ease, border-color 0.2s ease;
  }}
  .copy-btn:hover {{
    border-color: #111827;
    transform: translateY(-1px);
  }}
</style>
    """


def _dedupe_variants(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (
            str(row.get("en") or "").strip(),
            str(row.get("zh") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


st.set_page_config(
    page_title="历史记录 · PKHeX",
    page_icon="📜",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
html, body, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: #ffffff;
    color: #1a1a1a;
}
[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1200px; padding-top: 1.2rem; padding-bottom: 3rem; }
.hist-hero {
    font-size: clamp(2.25rem, 3.8vw, 3rem);
    font-weight: 500;
    letter-spacing: -0.03em;
    color: #111827;
    margin: 0 0 0.35rem 0;
    animation: rise-in 0.65s ease both;
}
.hist-sub { color: #6b7280; font-size: 1.24rem; margin-bottom: 1.5rem; line-height: 1.62; animation: rise-in 0.75s ease both; }
.filter-wrap {
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 12px 14px;
    margin: 10px 0 14px 0;
    background: #fafafa;
}
.stat-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    border-radius: 999px;
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    font-size: 1.06rem;
    margin-right: 10px;
    margin-bottom: 10px;
}
[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.9) !important;
    border-right: 1px solid #e5e7eb;
}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button {
    border-radius: 12px !important;
    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
}
[data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 24px rgba(17, 24, 39, 0.1);
}
@keyframes rise-in {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown('<p class="hist-hero">永久生成历史</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="hist-sub">所有「开始生成」成功的队伍会<strong>自动写入本地文件</strong>，关闭浏览器后仍可在此查看、载入或导出。</p>',
    unsafe_allow_html=True,
)

hist = load_history()
path = history_file_path()

with st.sidebar:
    st.markdown("### 快捷操作")
    st.caption(f"存储文件\n`{path}`")
    backup = json.dumps(hist, ensure_ascii=False, indent=2) if hist else "[]"
    st.download_button(
        "导出备份 JSON",
        data=backup.encode("utf-8"),
        file_name="pkhex_history_backup.json",
        mime="application/json",
        use_container_width=True,
    )
    st.divider()
    st.markdown("### 危险操作")
    confirm = st.checkbox("我确认要清空全部历史", value=False)
    if st.button("清空全部记录", type="primary", disabled=not confirm, use_container_width=True):
        clear_all()
        st.success("已清空。")
        st.rerun()

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("记录条数", len(hist))
with m2:
    st.metric("队列上限（超出则删最旧）", f"{MAX_ENTRIES} 条")
with m3:
    size_kb = path.stat().st_size / 1024 if path.is_file() else 0
    st.metric("文件大小", f"{size_kb:.1f} KB")

st.markdown(
    f'<div style="margin:12px 0 20px 0;"><span class="stat-pill">路径：<code style="font-size:0.85em;">{html_escape(str(path))}</code></span></div>',
    unsafe_allow_html=True,
)

q = st.text_input("搜索（文件名、时间）", placeholder="输入关键词过滤…")
qv = q.strip().lower()
if qv:
    hist_view = []
    for e in hist:
        texts = [str(e.get("label") or ""), str(e.get("at") or "")]
        texts.extend(str(v.get("mon") or "") for v in _entry_variants(e))
        hay = " ".join(texts).lower()
        if qv in hay:
            hist_view.append(e)
else:
    hist_view = list(hist)

if not hist_view:
    st.info("暂无记录。请到 **PKHeX 队伍生成器** 主页上传截图并点击「开始生成」。")
    st.stop()

st.markdown("---")

all_variants = []
for ent in hist_view:
    all_variants.extend(_entry_variants(ent))

if not all_variants:
    st.info("当前筛选条件下没有可展示的宝可梦配置。")
    st.stop()

grouped: dict[str, list[dict]] = {}
for var in all_variants:
    grouped.setdefault(var["mon"], []).append(var)

for mon in list(grouped.keys()):
    grouped[mon] = _dedupe_variants(grouped[mon])

mon_options = sorted(grouped.keys(), key=lambda x: (len(grouped[x]), x), reverse=True)
st.markdown('<div class="filter-wrap">', unsafe_allow_html=True)
f1, f2 = st.columns([1.35, 1])
with f1:
    mon_pick = st.selectbox("按宝可梦筛选", ["全部宝可梦"] + mon_options, index=0)
with f2:
    mon_query = st.text_input("宝可梦名搜索", placeholder="例如：烈咬陆鲨 / Garchomp")
st.markdown("</div>", unsafe_allow_html=True)

mq = mon_query.strip().lower()
filtered_grouped: dict[str, list[dict]] = {}
for mon, rows in grouped.items():
    if mon_pick != "全部宝可梦" and mon != mon_pick:
        continue
    if mq:
        hit = False
        for row in rows:
            for alias in row.get("alias") or []:
                if mq in str(alias).lower():
                    hit = True
                    break
            if hit:
                break
        if not hit and mq not in mon.lower():
            continue
    filtered_grouped[mon] = rows

groups_sorted = sorted(filtered_grouped.items(), key=lambda x: len(x[1]), reverse=True)

if groups_sorted:
    st.markdown("### 宝可梦配置卡片库")
    filtered_count = sum(len(v) for _, v in groups_sorted)
    cards_h = min(7200, 300 + filtered_count * 185)
    components.html(_build_grouped_cards_html(groups_sorted), height=cards_h, scrolling=True)
    st.caption("每张卡片会保留生成时间与来源记录；点击卡片即可在中文/英文之间翻转。")
    if mon_pick != "全部宝可梦" and mon_pick in filtered_grouped:
        st.markdown(f"### {mon_pick} · 全部历史配置")
        rows = filtered_grouped[mon_pick]
        for i, row in enumerate(rows, start=1):
            title = f"{i}. {row['at']} · {row['label']}"
            with st.expander(title, expanded=(i == 1)):
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("中文配置")
                    st.code(row["zh"], language="text")
                with c2:
                    st.caption("英文配置")
                    st.code(row["en"], language="text")
    st.markdown("---")
    st.markdown("### 原始记录操作")
    st.caption("这里保留载入、下载、删除等按“整次生成记录”维度的操作。")
else:
    st.info("没有匹配到目标宝可梦。可尝试清空筛选或换关键词。")
    st.markdown("---")

_app_root = Path(__file__).resolve().parents[1]
_main_script = _app_root / "app.py"


for idx, ent in enumerate(hist_view):
    eid = str(ent.get("id") or "")
    if not eid:
        continue
    n_mons = len(ent.get("blocks_en") or [])
    label = html_escape(str(ent.get("label") or "—"))
    at = html_escape(str(ent.get("at") or "—"))
    preview_titles = ent.get("titles") or []
    preview_str = " · ".join(str(t) for t in preview_titles[:6]) if preview_titles else "—"
    if len(preview_str) > 120:
        preview_str = preview_str[:117] + "…"

    st.markdown(
        f"""
<div style="
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 18px;
  padding: 18px 20px;
  margin-bottom: 16px;
  box-shadow: 0 8px 24px rgba(17,24,39,0.08);
">
  <div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;">
    <div>
      <div style="font-size:1.15rem;font-weight:700;color:#111827;">{label}</div>
      <div style="font-size:0.98rem;color:#6b7280;margin-top:4px;">{at} · <span style="color:#374151;">{n_mons} 只宝可梦</span></div>
      <div style="font-size:0.92rem;color:#9ca3af;margin-top:8px;max-width:720px;">{html_escape(preview_str)}</div>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1, 1])
    with c1:
        if st.button("载入到生成页", key=f"load_{eid}_{idx}", use_container_width=True):
            st.session_state[SS_BLOCKS] = ent.get("blocks_en")
            st.session_state[SS_BLOCKS_ZH] = ent.get("blocks_zh") or []
            st.session_state[SS_TITLES] = ent.get("titles") or []
            st.session_state[SS_FULL] = ent.get("full") or ""
            try:
                st.switch_page("app.py")
            except Exception:
                try:
                    st.switch_page(str(_main_script))
                except Exception:
                    st.success("已载入到会话。请从左侧导航打开「PKHeX 队伍生成器」主页查看。")
    with c2:
        full_txt = ent.get("full") or ""
        st.download_button(
            "下载此条 txt",
            data=full_txt.encode("utf-8"),
            file_name=f"pkhex_{eid[:8]}.txt",
            mime="text/plain",
            key=f"dl_{eid}_{idx}",
            use_container_width=True,
        )
    with c3:
        with st.expander("预览英文"):
            body = ent.get("full") or ""
            st.code(body[:4000] + ("…" if len(body) > 4000 else ""), language="text")
    with c4:
        if st.button("删除此条", key=f"del_{eid}_{idx}", use_container_width=True):
            delete_entry(eid)
            st.rerun()

st.caption("提示：载入后会跳转到主页；若跳转失败，请手动打开「PKHeX 队伍生成器」页面。")
