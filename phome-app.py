# -*- coding: utf-8 -*-
"""PChome 商品價格爬蟲 + 視覺化 (Streamlit)

執行方式：
    pip install -r requirements.txt
    streamlit run app.py
"""
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

API_URL = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SORT_OPTIONS = {
    "銷量高到低": "sale/dc",
    "價格低到高": "prc/ac",
    "價格高到低": "prc/dc",
    "綜合排序": "rnk/dc",
}

st.set_page_config(page_title="PChome 價格分析", page_icon="🛒", layout="wide")


# ---------------------------------------------------------------- 爬蟲
@st.cache_data(show_spinner=False, ttl=600)
def fetch_page(keyword: str, page: int, sort: str) -> dict:
    """抓取單頁搜尋結果（以 params 傳參，中文關鍵字會自動編碼）。"""
    resp = requests.get(
        API_URL,
        params={"q": keyword, "page": page, "sort": sort},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def crawl(keyword: str, pages: int, sort: str, delay: float) -> pd.DataFrame:
    frames = []
    progress = st.progress(0.0, text="開始爬取…")
    for i in range(1, pages + 1):
        progress.progress((i - 1) / pages, text=f"正在爬取第 {i}/{pages} 頁…")
        data = fetch_page(keyword, i, sort)
        prods = data.get("prods") or []
        if not prods:  # 已無更多資料
            break
        frames.append(pd.DataFrame(prods))
        total_page = data.get("totalPage")
        if total_page and i >= int(total_page):
            break
        if i < pages:
            time.sleep(delay)  # 避免請求過於頻繁
    progress.empty()
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------- 資料處理
def extract_brand(name: str) -> str:
    """從商品名稱擷取品牌，例如『【Anker】…』→ Anker；找不到則為『其他』。"""
    m = re.search(r"[【\[「](.+?)[】\]」]", str(name))
    return m.group(1).strip() if m else "其他"


def add_price_range(df: pd.DataFrame) -> pd.DataFrame:
    """依價格分位數切成最多 5 個區間（資料量少時自動減少）。"""
    n_unique = df["price"].nunique()
    if n_unique < 2:
        df["價格區間"] = "全部"
        return df
    cats = pd.qcut(df["price"], q=min(5, n_unique), duplicates="drop")
    df["價格區間"] = cats.apply(lambda iv: f"${iv.left:,.0f} ~ ${iv.right:,.0f}")
    order = (
        df.assign(_c=cats).sort_values("_c")["價格區間"].drop_duplicates().tolist()
    )
    df["價格區間"] = pd.Categorical(df["價格區間"], categories=order, ordered=True)
    return df


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw[["name", "price"]].copy()
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"]).reset_index(drop=True)
    df["品牌"] = df["name"].apply(extract_brand)
    return add_price_range(df)


# ---------------------------------------------------------------- 圖表
def line_chart(df: pd.DataFrame, keyword: str, max_items: int):
    d = df.head(max_items).copy()
    d["序號"] = d.index + 1
    avg = df["price"].mean()
    fig = px.line(
        d, x="序號", y="price", markers=True, hover_name="name",
        labels={"序號": "商品序號", "price": "價格 (NT$)"},
        title=f"PChome「{keyword}」商品售價（前 {len(d)} 筆）",
    )
    fig.add_hline(
        y=avg, line_dash="dash", line_color="red",
        annotation_text=f"平均 {avg:,.0f}", annotation_position="top left",
    )
    fig.update_layout(height=480, title_font_size=22)
    return fig


def pie_chart(df: pd.DataFrame):
    counts = df["價格區間"].value_counts().sort_index().reset_index()
    counts.columns = ["價格區間", "數量"]
    fig = px.pie(counts, names="價格區間", values="數量", hole=0.3,
                 title="價格區間分布")
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(height=480, title_font_size=22)
    return fig


def sunburst_chart(df: pd.DataFrame, top_n: int):
    d = df.copy()
    top = d["品牌"].value_counts().head(top_n).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    d["價格區間"] = d["價格區間"].astype(str)
    d["數量"] = 1
    fig = px.sunburst(
        d, path=["價格區間", "品牌"], values="數量",
        color="price", color_continuous_scale="Blues",
        title=f"價格區間 → 品牌（前 {top_n} 大品牌，其餘歸『其他』）",
    )
    fig.update_layout(height=560, title_font_size=22)
    return fig


# ---------------------------------------------------------------- 介面
st.title("🛒 PChome 商品價格爬蟲與視覺化")

with st.sidebar:
    st.header("搜尋設定")
    keyword = st.text_input("產品名稱", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    sort_label = st.selectbox("排序方式", list(SORT_OPTIONS))
    delay = st.slider("每頁間隔秒數", 0.5, 10.0, 2.0, 0.5,
                      help="間隔越長越不容易被網站封鎖")
    max_items = st.slider("折線圖顯示筆數", 10, 200, 70, 10)
    top_n = st.slider("旭日圖品牌數量", 3, 15, 8)
    run = st.button("開始爬取", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入產品名稱")
        st.stop()
    try:
        raw = crawl(keyword.strip(), int(pages), SORT_OPTIONS[sort_label], delay)
    except requests.RequestException as e:
        st.error(f"請求失敗：{e}")
        st.stop()
    except ValueError:
        st.error("回傳內容不是有效的 JSON，可能被網站阻擋或 API 已變更。")
        st.stop()

    if raw.empty:
        st.warning("查無資料，請換個關鍵字試試。")
        st.stop()
    st.session_state["df"] = prepare(raw)
    st.session_state["raw"] = raw
    st.session_state["keyword"] = keyword.strip()

if "df" in st.session_state:
    df = st.session_state["df"]
    kw = st.session_state["keyword"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("商品數", f"{len(df):,}")
    c2.metric("平均價格", f"{df['price'].mean():,.0f}")
    c3.metric("最高價格", f"{df['price'].max():,.0f}")
    c4.metric("最低價格", f"{df['price'].min():,.0f}")

    tab1, tab2, tab3, tab4 = st.tabs(["📈 折線圖", "🥧 圓餅圖", "☀️ 旭日圖", "📋 原始資料"])
    with tab1:
        st.plotly_chart(line_chart(df, kw, max_items), use_container_width=True)
    with tab2:
        st.plotly_chart(pie_chart(df), use_container_width=True)
    with tab3:
        st.plotly_chart(sunburst_chart(df, top_n), use_container_width=True)
    with tab4:
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "下載 CSV",
            data=st.session_state["raw"].to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{time.strftime('%Y%m%d')}_PCHOME_{kw}.csv",
            mime="text/csv",
        )
else:
    st.info("請在左側輸入產品名稱與頁數，然後按下「開始爬取」。")
