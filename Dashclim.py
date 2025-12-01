# Dashclim.py
import re
import io
import math
from typing import Optional, Tuple, Dict
import pandas as pd
import folium
from folium import plugins
import numpy as np
import streamlit as st
import plotly.express as px
import html
import streamlit.components.v1 as components
from folium import Map, CircleMarker, Popup
import requests



# Optional visualization libs
try:
    import pydeck as pdk
except Exception:
    pdk = None

# Optional AgGrid for nicer tables (install streamlit-aggrid if used)
try:
    from st_aggrid import AgGrid, GridOptionsBuilder
except Exception:
    AgGrid = None
    GridOptionsBuilder = None

# --------------------------
#  CONFIG
# --------------------------
st.set_page_config(page_title="Monitoring CLIMAT BMKG", layout="wide", initial_sidebar_state="expanded")
from utils.ui import setup_header

setup_header()


# Default PUBLIC Google Sheet links (you can replace with your own):
DEFAULT_MONTHLY_SHEET_URL = "https://docs.google.com/spreadsheets/d/1gJ1NcQAWl4bvJwvw591AMgwOkue9PqFJxw1CUuxXMIw/edit#gid=0"
DEFAULT_YEARLY_SHEET_URL = "https://docs.google.com/spreadsheets/d/13IwxZ6a4O2QeMe7OsGbYnfqqH2uNwNxXiyKPI-1Hlbw/edit#gid=0"

# If using private sheets, set USE_SERVICE_ACCOUNT=True and store JSON creds in st.secrets["gcp_service_account"]
USE_SERVICE_ACCOUNT = False  # toggle to True only if you will configure st.secrets with service account

# --------------------------
#  HELPERS: Google Sheet loader
# --------------------------
def extract_sheet_id_and_gid(url_or_id: str) -> Tuple[Optional[str], Optional[str]]:
    if not url_or_id:
        return None, None
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url_or_id)
    sheet_id = None
    gid = None
    if m:
        sheet_id = m.group(1)
        mg = re.search(r"gid=([0-9]+)", url_or_id)
        if mg:
            gid = mg.group(1)
        else:
            mg = re.search(r"#gid=([0-9]+)", url_or_id)
            if mg:
                gid = mg.group(1)
    else:
        if "," in url_or_id:
            parts = url_or_id.split(",")
            sheet_id = parts[0].strip()
            gid = parts[1].strip()
        else:
            sheet_id = url_or_id.strip()
    if not gid:
        gid = "0"
    return sheet_id, gid

def gsheet_csv_export_url(sheet_id: str, gid: str = "0") -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
#------------------------------------------------------------------------------
def discover_gids_for_sheet(sheet_id: str, max_tries: int = 60) -> list:
    """
    Try several heuristics to discover gids for a PUBLIC Google Spreadsheet:
      1) fetch /docs.google.com/spreadsheets/d/{id} and regex for gid=
      2) fetch /gviz/tq?tqx=out:json and regex for sheetId / gid
    Returns sorted list of unique gid strings (may be empty if sheet is private).
    """
    if not sheet_id:
        return []

    gids = set()

    # 1) naive HTML scrape (existing method) - quick attempt
    try:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200 and resp.text:
            found = re.findall(r"gid=([0-9]+)", resp.text)
            for g in found:
                gids.add(str(int(g)))  # normalize
    except Exception:
        pass

    # 2) try gviz JSON endpoint (works on many public sheets)
    # The response is JavaScript that wraps JSON; we extract numbers like "sheetId":123456789
    try:
        gviz_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:json"
        resp = requests.get(gviz_url, timeout=6)
        if resp.status_code == 200 and resp.text:
            txt = resp.text
            # extract numeric sheetId occurrences
            sheet_ids = re.findall(r'"sheetId"\s*:\s*([0-9]+)', txt)
            for sid in sheet_ids:
                gids.add(str(int(sid)))
            # sometimes gviz contains "gid=NNN" too
            found2 = re.findall(r"gid=([0-9]+)", txt)
            for g in found2:
                gids.add(str(int(g)))
    except Exception:
        pass

    # 3) fallback: try gid 0 (default) and some typical common gids used earlier
    if not gids:
        fallback = ["0", "172027705", "1493298409"]
        for f in fallback:
            gids.add(f)

    # sort numerically as strings
    sorted_gids = sorted(list(gids), key=lambda x: int(x))
    return sorted_gids
#------------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_sheet_csv_url(url_or_id: str) -> pd.DataFrame:
    sheet_id, gid = extract_sheet_id_and_gid(url_or_id)
    if not sheet_id:
        raise ValueError("Tidak dapat mengekstrak sheet id.")
    csv_url = gsheet_csv_export_url(sheet_id, gid)
    try:
        df = pd.read_csv(csv_url)
        return df
    except Exception as e:
        raise RuntimeError(f"Gagal memuat CSV dari URL: {csv_url}. Error: {e}")

# --------------------------
#  HELPERS: Delta parser
# --------------------------
def parse_delta_to_hours(text: str) -> Optional[float]:
    if pd.isna(text):
        return None
    s = str(text).strip()
    if s == "":
        return None
    s = s.replace("–", "-").replace("—", "-").replace("hrs", "jam").replace("hr", "hari")
    sign = 1
    if s.startswith("-"):
        sign = -1
    elif s.startswith("+"):
        sign = 1
    days = hours = mins = secs = 0
    m_day = re.search(r"(-?\s*\d+)\s*hari", s, flags=re.I)
    if m_day:
        try:
            days = int(re.sub(r"\D", "", m_day.group(1)))
        except:
            days = 0
    m_hour = re.search(r"(-?\s*\d+)\s*jam", s, flags=re.I)
    if m_hour:
        try:
            hours = int(re.sub(r"\D", "", m_hour.group(1)))
        except:
            hours = 0
    m_min = re.search(r"(-?\s*\d+)\s*(mnt|min)", s, flags=re.I)
    if m_min:
        try:
            mins = int(re.sub(r"\D", "", m_min.group(1)))
        except:
            mins = 0
    m_sec = re.search(r"(-?\s*\d+)\s*(dtk|sec|s)", s, flags=re.I)
    if m_sec:
        try:
            secs = int(re.sub(r"\D", "", m_sec.group(1)))
        except:
            secs = 0
    total_hours = days*24 + hours + mins/60.0 + secs/3600.0
    return sign * float(total_hours)

# --------------------------
#  DATA LOADER WRAPPER
# --------------------------
# @st.cache_data(ttl=300)
# def load_all_data(monthly_url: str, yearly_url: str, delta_gid: str=None, status_gid: str=None, monthly_summary_gid: str=None) -> Dict[str, pd.DataFrame]:
#     monthly_df = load_sheet_csv_url(monthly_url)
#     sheet_id_y, base_gid = extract_sheet_id_and_gid(yearly_url)
#     if not sheet_id_y:
#         raise ValueError("Invalid yearly url/id")
#     gids_try = []
#     if delta_gid:
#         gids_try.append(("deltahours", delta_gid))
#     if status_gid:
#         gids_try.append(("status", status_gid))
#     if monthly_summary_gid:
#         gids_try.append(("monthlysummary", monthly_summary_gid))
#     if not gids_try:
#         # common fallback gids (may need adjustment)
#         gids_try = [("deltahours","0"), ("status","172027705"), ("monthlysummary","0")]
#     out = {"monthly": monthly_df}
#     for name, gid in gids_try:
#         try:
#             url = gsheet_csv_export_url(sheet_id_y, gid)
#             df = pd.read_csv(url)
#             out[name] = df
#         except Exception:
#             out.setdefault(name, pd.DataFrame())
#     for k in ["deltahours","status","monthlysummary"]:
#         out.setdefault(k, pd.DataFrame())
#     return out
@st.cache_data(ttl=300)
def load_all_data(monthly_url: str, yearly_url: str, delta_gid: str=None, status_gid: str=None, monthly_summary_gid: str=None) -> Dict[str, pd.DataFrame]:
    """
    Load monthly and yearly sheets. If specific gids are given (delta_gid/status_gid/monthly_summary_gid), use them.
    Otherwise, attempt to auto-discover gids from yearly_url by fetching the spreadsheet HTML and extracting gid numbers.
    For each discovered gid we attempt to fetch CSV and heuristically determine if it is the 'status', 'deltahours', or 'monthlysummary' worksheet
    by inspecting column names.
    """
    monthly_df = load_sheet_csv_url(monthly_url)
    # extract sheet id (and optional gid) from yearly_url
    sheet_id_y, base_gid = extract_sheet_id_and_gid(yearly_url)
    if not sheet_id_y:
        raise ValueError("Invalid yearly url/id")

    out = {"monthly": monthly_df, "deltahours": pd.DataFrame(), "status": pd.DataFrame(), "monthlysummary": pd.DataFrame()}

    # if gids explicitly provided, prefer them
    provided = {}
    if delta_gid:
        provided["deltahours"] = delta_gid
    if status_gid:
        provided["status"] = status_gid
    if monthly_summary_gid:
        provided["monthlysummary"] = monthly_summary_gid

    # helper to try load a given gid and assign to target if heuristics match
    def try_load_and_classify(gid_str):
        url = gsheet_csv_export_url(sheet_id_y, gid_str)
        try:
            df = pd.read_csv(url)
        except Exception:
            return None, None
        # normalize column names for heuristics
        cols = [str(c).strip().lower() for c in df.columns]
        # heuristics: check for presence of month cols or keyword columns
        if any(c in cols for c in ['tepat_waktu','terlambat','tidak_mengirim']) and 'bulan' in cols:
            return 'monthlysummary', df
        if 'station_name' in cols or 'nama stasiun' in cols or 'stasiun' in cols:
            month_like = [c for c in df.columns if str(c).strip()[:3].lower() in ['jan','feb','mar','apr','mei','jun','jul','agt','sep','okt','nov','des']]
            if month_like:
                # peek first non-null cell in a month column
                sample_val = None
                for c in month_like:
                    v = df[c].dropna().astype(str)
                    if len(v)>0:
                        sample_val = v.iloc[0]
                        break
                if sample_val is not None and any(k in str(sample_val).lower() for k in ['tepat','terlambat','tidak']):
                    return 'status', df
                if sample_val is not None and any(k in str(sample_val).lower() for k in ['hari','jam','mnt','dtk','detik']):
                    return 'deltahours', df
                return 'status', df
        if 'bulan' in cols and any('tepat' in c for c in cols):
            return 'monthlysummary', df
        return None, df

    # 1) If user provided specific gids, load those directly
    for name, gid in provided.items():
        try:
            df = pd.read_csv(gsheet_csv_export_url(sheet_id_y, gid))
            out[name] = df
        except Exception:
            out[name] = pd.DataFrame()

    # 2) If some targets are still empty, attempt discover
    needed = [k for k,v in out.items() if k in ['deltahours','status','monthlysummary'] and (v is None or v.empty)]
    if needed:
        discovered = discover_gids_for_sheet(sheet_id_y)
        # ensure we always include base_gid if present and not already in list
        if base_gid and base_gid not in discovered:
            discovered.insert(0, base_gid)
        # try each discovered gid and classify
        tried = set()
        for gid in discovered:
            if not needed:
                break
            if gid in tried:
                continue
            tried.add(gid)
            cls, df_try = try_load_and_classify(gid)
            if cls and (out.get(cls) is None or out.get(cls).empty):
                out[cls] = df_try
                if cls in needed:
                    needed.remove(cls)
            else:
                # fallback heuristic assign to deltahours if looks like durations
                if (out.get('deltahours') is None or out.get('deltahours').empty):
                    cols_lower = [c.lower() for c in df_try.columns]
                    month_like = [c for c in df_try.columns if str(c).strip()[:3].lower() in ['jan','feb','mar','apr','mei','jun','jul','agt','sep','okt','nov','des']]
                    if 'station_name' in cols_lower and month_like:
                        sample_val = None
                        for c in month_like:
                            v = df_try[c].dropna().astype(str)
                            if len(v)>0:
                                sample_val = v.iloc[0]
                                break
                        if sample_val and any(k in str(sample_val).lower() for k in ['hari','jam','mnt','dtk','detik']):
                            out['deltahours'] = df_try
                            if 'deltahours' in needed:
                                needed.remove('deltahours')

    # final ensure keys present
    for k in ['deltahours','status','monthlysummary']:
        out.setdefault(k, pd.DataFrame())

    return out


# --------------------------
#  PLOTTING HELPERS
# --------------------------
def plot_monthly_stacked(df_monthly: pd.DataFrame):
    month_order = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agt","Sep","Okt","Nov","Des"]
    if 'Bulan' in df_monthly.columns:
        df = df_monthly.copy()
        df['Bulan'] = pd.Categorical(df['Bulan'], categories=month_order, ordered=True)
        df = df.sort_values('Bulan')
    else:
        df = df_monthly.copy()
    cols = ['TEPAT_WAKTU','TERLAMBAT','TIDAK_MENGIRIM']
    existing = [c for c in cols if c in df.columns]
    if not existing:
        return None
    row_sums = df[existing].sum(axis=1).replace(0, np.nan)
    df_perc = df.copy()
    df_perc[existing] = df_perc[existing].div(row_sums, axis=0).fillna(0) * 100
    df_melt = df_perc.melt(id_vars='Bulan', value_vars=existing, var_name='Kategori', value_name='Percent')
    label_map = {'TEPAT_WAKTU':'Tepat Waktu','TERLAMBAT':'Terlambat','TIDAK_MENGIRIM':'Tidak Mengirim'}
    df_melt['Label'] = df_melt['Kategori'].map(label_map).fillna(df_melt['Kategori'])
    color_map = {'Tepat Waktu':'#2ecc71','Terlambat':'#f1c40f','Tidak Mengirim':'#e74c3c'}
    fig = px.bar(df_melt, x='Bulan', y='Percent', color='Label',
                 color_discrete_map=color_map,
                 category_orders={'Bulan': month_order})
    fig.update_layout(barmode='stack', yaxis=dict(range=[0,100], title='Percent'), template='simple_white', height=420)
    fig.update_xaxes(tickangle=-45)
    return fig

def plot_pie_total(df_monthly: pd.DataFrame):
    cols = ['TEPAT_WAKTU','TERLAMBAT','TIDAK_MENGIRIM']
    existing = [c for c in cols if c in df_monthly.columns]
    if not existing:
        return None
    totals = df_monthly[existing].sum()
    labels = {'TEPAT_WAKTU':'Tepat Waktu','TERLAMBAT':'Terlambat','TIDAK_MENGIRIM':'Tidak Mengirim'}
    series = pd.Series({labels[k]: totals[k] for k in existing})
    fig = px.pie(values=series.values, names=series.index, hole=0.3,
                 color_discrete_map={'Tepat Waktu':'#2ecc71','Terlambat':'#f1c40f','Tidak Mengirim':'#e74c3c'})
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(height=420, template='simple_white')
    return fig

# # --------------------------
# #  UI: Sidebar config for data sources
# # --------------------------
# st.sidebar.markdown("### Data Source (Google Sheets)")
# monthly_input = st.sidebar.text_input("Monthly sheet URL / ID / ID,gid", DEFAULT_MONTHLY_SHEET_URL)
# yearly_input = st.sidebar.text_input("Yearly sheet URL / ID / ID,gid", DEFAULT_YEARLY_SHEET_URL)
# # --- Auto-detect helper UI ---
# st.sidebar.markdown("**Auto-discover gids (publik only)**")
# # st.sidebar.caption("Kosongkan field gid agar dashboard mencoba menemukan worksheet gid secara otomatis (hanya untuk sheet publik).")
# if st.sidebar.button("🔎 Detect gids now"):
#     try:
#         sid, _ = extract_sheet_id_and_gid(yearly_input)
#         if not sid:
#             st.sidebar.error("Gagal extract sheet id dari Yearly sheet URL/ID. Periksa input.")
#         else:
#             detected = discover_gids_for_sheet(sid)
#             if detected:
#                 st.sidebar.success(f"Detected gids: {', '.join(detected)}")
#             else:
#                 st.sidebar.warning("Tidak ada gid terdeteksi — spreadsheet mungkin privat atau diblokir.")
#     except Exception as e:
#         st.sidebar.error(f"Error saat detect: {e}")


# # st.sidebar.markdown("*(optional) If the yearly spreadsheet has multiple sheets, specify gids (comma separated):*")
# delta_gid = st.sidebar.text_input("DeltaHours sheet gid (optional)", "")
# status_gid = st.sidebar.text_input("Status sheet gid (optional)", "")
# monthly_summary_gid = st.sidebar.text_input("MonthlySummary sheet gid (optional)", "")

# if USE_SERVICE_ACCOUNT:
#     st.sidebar.caption("Using service account from st.secrets['gcp_service_account']")

# if st.sidebar.button("🔁 Refresh data (force reload)"):
#     try:
#         load_sheet_csv_url.clear()
#         load_all_data.clear()
#     except Exception:
#         pass
#     st.experimental_rerun()

# --------------------------
# Minimal Sidebar: hanya navigasi (Pilih Halaman & Subpage)
# --------------------------
st.sidebar.title("Navigasi")
# main_page = st.sidebar.radio("Pilih Halaman:", ["Bulanan", "Tahunan"], index=0)

# Gunakan default sheet URL (tidak lagi diambil dari sidebar)
monthly_input = DEFAULT_MONTHLY_SHEET_URL
yearly_input = DEFAULT_YEARLY_SHEET_URL
# optional gids: kosongkan (atau set sesuai kebutuhan hard-coded)
delta_gid = None
status_gid = None
monthly_summary_gid = None

# The 'Pilih Sub Page' radio is already declared later inside the "Tahunan" branch:
# `sub_page = st.sidebar.radio("Pilih Sub Page:", [...])`
# leaving it there keeps sub-page choice in the sidebar only when Tahunan selected.


# --------------------------
#  LOAD DATA
# --------------------------
try:
    data = load_all_data(monthly_input, yearly_input, delta_gid=delta_gid or None, status_gid=status_gid or None, monthly_summary_gid=monthly_summary_gid or None)
except Exception as e:
    st.error(f"Gagal memuat data dari Google Sheets: {e}")
    st.stop()

df_monthly = data.get("monthly", pd.DataFrame())
df_delta = data.get("deltahours", pd.DataFrame())
df_status = data.get("status", pd.DataFrame())
df_monthly_summary = data.get("monthlysummary", pd.DataFrame())

# # Quick preview info
# st.sidebar.markdown("**Data preview**")
# st.sidebar.write("Monthly rows:", len(df_monthly))
# st.sidebar.write("DeltaHours rows:", len(df_delta))
# st.sidebar.write("Status rows:", len(df_status))
# st.sidebar.write("MonthlySummary rows:", len(df_monthly_summary))

# # ===========================
# # Debug block (temporary)
# # ===========================
# st.sidebar.markdown("### Debug: lihat struktur data (sementara)")
# st.sidebar.write("df_monthly columns:", list(df_monthly.columns))
# st.sidebar.write("df_monthly head:")
# st.sidebar.write(df_monthly.head())

# st.sidebar.write("df_monthly_summary columns:", list(df_monthly_summary.columns))
# st.sidebar.write("df_monthly_summary head:")
# st.sidebar.write(df_monthly_summary.head())

# st.sidebar.write("df_status columns:", list(df_status.columns))
# st.sidebar.write("df_delta columns:", list(df_delta.columns))

# --------------------------
#  App Main Navigation
# --------------------------
st.write("")  # spacing
main_page = st.sidebar.radio("Pilih Halaman:", ["Bulanan", "Tahunan"], index=0, key="main_page_radio")

#  HALAMAN BULANAN (robust)
# --------------------------
def detect_status_column(df: pd.DataFrame) -> Optional[str]:
    """Return a candidate column name that likely contains status text."""
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    lc = [c.lower() for c in cols]
    # common names
    candidates = []
    for i, c in enumerate(lc):
        if 'status' in c:
            candidates.append(cols[i])
        if 'tepat' in c or 'ketepatan' in c:
            candidates.append(cols[i])
        if 'terkirim' in c:
            candidates.append(cols[i])
    return candidates[0] if candidates else None

def normalize_monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalisasi df_monthly berdasarkan kolom:
    - 'terkirim' (1/0 atau Ya/Tidak)
    - 'tepat_waktu' (1/0)
    - 'time_diff_hours_num' (angka, jam)
    Membentuk kolom baru 'status' dengan 3 kategori:
    'TEPAT WAKTU', 'TERLAMBAT', 'TIDAK MENGIRIM'
    """
    if df is None or df.empty:
        return pd.DataFrame()

    df2 = df.copy()

    def to_bool(v):
        """Convert Ya/Yes/True/1 ke True"""
        if pd.isna(v):
            return False
        s = str(v).strip().upper()
        return s in ["YA", "Y", "YES", "TRUE", "T", "1", "OK"]

    # Normalisasi kolom terkirim dan tepat_waktu jika ada
    if "terkirim" in df2.columns:
        df2["terkirim_bool"] = df2["terkirim"].apply(to_bool)
    else:
        df2["terkirim_bool"] = False

    if "tepat_waktu" in df2.columns:
        df2["tepat_bool"] = df2["tepat_waktu"].apply(to_bool)
    else:
        df2["tepat_bool"] = False

    # Bentuk kolom status baru
    def derive_status(row):
        if not row["terkirim_bool"]:
            return "TIDAK MENGIRIM"
        if row["terkirim_bool"] and row["tepat_bool"]:
            return "TEPAT WAKTU"
        # kalau tidak tepat_waktu tapi ada info delta positif
        if "time_diff_hours_num" in row and not pd.isna(row["time_diff_hours_num"]):
            try:
                val = float(row["time_diff_hours_num"])
                if val > 0:
                    return "TEPAT WAKTU"
                else:
                    return "TERLAMBAT"
            except:
                pass
        return "TEPAT WAKTU"

    df2["status"] = df2.apply(derive_status, axis=1)

    # pastikan kolom tampil urut rapi
    col_order = ["station_name", "wmoid", "LAT", "LON", "report_month", "status", 
                 "terkirim", "tepat_waktu", "time_diff_hours_num", "received_at", "monitoring_bulan"]
    df2 = df2[[c for c in col_order if c in df2.columns]]

    return df2


    # 4) fallback: mark everything unknown -> TIDAK MENGIRIM
    df2['status'] = 'TIDAK MENGIRIM'
    return df2

# Normalize monthly df once
df_monthly_norm = normalize_monthly_df(df_monthly) if 'df_monthly' in globals() else pd.DataFrame()

if main_page == "Bulanan":
    if not df_monthly_norm.empty and "report_month" in df_monthly_norm.columns:
        # Ambil bulan-tahun unik dari kolom 'report_month'
        report_month = str(df_monthly_norm["report_month"].dropna().unique()[0])

        # Pecah format YYYY-MM
        tahun, bulan_num = report_month.split("-")
        bulan_nama = {
            "1": "Januari", "2": "Februari", "3": "Maret", "4": "April",
            "5": "Mei", "6": "Juni", "7": "Juli", "8": "Agustus",
            "9": "September", "10": "Oktober", "11": "November", "12": "Desember"
        }.get(bulan_num, bulan_num)

        st.header(f"Monitoring CLIMAT {bulan_nama} {tahun}")
    else:
        st.header("Monitoring CLIMAT — Bulanan (Preview)")


    # KPI Cards computed from normalized df if available
    col1, col2, col3, col4, col5 = st.columns(5)
    total = len(df_monthly_norm) if not df_monthly_norm.empty else len(df_monthly) if 'df_monthly' in globals() else 0
    col1.metric("Total Stasiun", total)

    if not df_monthly_norm.empty:
        sent_count = (df_monthly_norm['status'] != 'TIDAK MENGIRIM').sum()
        tepat = (df_monthly_norm['status'] == 'TEPAT WAKTU').sum()
        terlambat = (df_monthly_norm['status'] == 'TERLAMBAT').sum()
        tidak = (df_monthly_norm['status'] == 'TIDAK MENGIRIM').sum()
        col2.metric("% Mengirim", f"{(sent_count/total*100):.0f}%")
        col3.metric("% Tepat Waktu", f"{(tepat/total*100):.0f}%")
        col4.metric("% Terlambat", f"{(terlambat/total*100):.0f}%")
        col5.metric("% Tidak Mengirim", f"{(tidak/total*100):.0f}%")

    else:
        col2.metric("% Mengirim", "0%")
        col3.metric("% Tepat Waktu", "—")
        col4.metric("% Terlambat", "—")
        col5.metric("% Tidak Mengirim", "—")

    st.markdown("---")
    
    # ---------- MAP FULL-WIDTH (atas) ----------
    st.subheader("Availability")

    # tampilkan peta full-width (keluarkan dari st.columns)
    if not df_monthly_norm.empty and {'LAT','LON','status'}.issubset(df_monthly_norm.columns):
        import folium
        from folium import Map, Marker, CircleMarker
        try:
            from streamlit_folium import st_folium
            use_st_folium = True
        except Exception:
            use_st_folium = False
            import streamlit.components.v1 as components

        df_map = df_monthly_norm.copy()

        # Esri tile URL (World Street Map)
        esri_tiles = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
        esri_attr = "Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, NGA, USGS"

        # center map di rata-rata koordinat
        try:
            center_lat = -2.2331
            center_lon = 117.2841
            # center_lat = float(df_map['LAT'].astype(float).mean())
            # center_lon = float(df_map['LON'].astype(float).mean())
        except Exception:
            center_lat, center_lon = -2.2331, 117.2841

        m = folium.Map(location=[center_lat, center_lon], zoom_start=5, tiles=None)
        folium.TileLayer(tiles=esri_tiles, attr=esri_attr, name="Esri World Street").add_to(m)

        # warna hex untuk status
        color_map_hex = {
            "TEPAT WAKTU": "#09ba53",
            "TERLAMBAT": "#ff8c00",
            "TIDAK MENGIRIM": "#000000"
        }

        def fmt_time_diff(val):
            try:
                if pd.isna(val):
                    return "-"
                v = float(val)
                return f"{v:+.2f} jam"
            except:
                return str(val)

        # Tambahkan markers TANPA clustering, radius tetap
        FIXED_RADIUS = 6
        for _, r in df_map.iterrows():
            try:
                lat = float(r['LAT'])
                lon = float(r['LON'])
            except:
                continue
            status = r.get('status', 'TIDAK MENGIRIM')
            color = color_map_hex.get(status, "#999999")
            td = fmt_time_diff(r.get('time_diff_hours_num', None))

            popup_html = f"""
            <div style="font-size:13px;">
            <b>{r.get('station_name','-')}</b><br>
            <small>WMO: {r.get('wmoid','-')}</small><br>
            <small>Status: <strong>{status}</strong></small><br>
            <small>Time diff: <code>{td}</code></small>
            </div>
            """

            folium.CircleMarker(
                location=[lat, lon],
                radius=FIXED_RADIUS,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=1,
                weight=1,
                popup=folium.Popup(popup_html, max_width=320)
            ).add_to(m)

        # Legend sederhana (HTML overlay)
        legend_html = """
        <div style="
            position: fixed;
            bottom: 18px;
            left: 18px;
            width:200px;
            background-color: white;
            border:1px solid grey;
            z-index:9999;
            padding:8px 10px;
            font-size:12px;
        ">
        <b>Ketepatan Waktu</b><br>
        <span style="display:inline-block;width:12px;height:12px;background:#09ba53;margin-right:6px;"></span> Tepat Waktu<br>
        <span style="display:inline-block;width:12px;height:12px;background:#ff8c00;margin-right:6px;"></span> Terlambat<br>
        <span style="display:inline-block;width:12px;height:12px;background:#000000;margin-right:6px;"></span> Tidak Mengirim
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))

        # render map full-width
        if use_st_folium:
            # st_folium menerima width="100%" sehingga memenuhi lebar area Streamlit
            st_folium(m, width="100%", height=470)
        else:
            components.html(m._repr_html_(), height=620, scrolling=True)
    else:
        st.info("Peta akan tampil jika sheet Monthly berisi kolom LAT, LON, dan status.")


    # ---------- PIE CHARTS (baris di bawah peta): dua kolom kiri & kanan ----------
    st.markdown("")  # spacing kecil
    pie_left, pie_right = st.columns(2)

    with pie_left:
        st.subheader("Persentase Pengiriman")
        if not df_monthly_norm.empty:
            # Pie 1: Mengirim vs Tidak Mengirim
            sent_series = df_monthly_norm["status"].apply(lambda s: "Mengirim" if s != "TIDAK MENGIRIM" else "Tidak Mengirim").value_counts()
            df_sent = pd.DataFrame({"label": sent_series.index.astype(str), "count": sent_series.values})

            color_map_sent = {"Mengirim": "#70B2B2", "Tidak Mengirim": "#E5E9C5"}
            fig_sent = px.pie(df_sent, names="label", values="count", hole=0.4, color="label",
                            color_discrete_map=color_map_sent)
            fig_sent.update_traces(textinfo="percent+label", textposition="inside", insidetextorientation="radial")
            fig_sent.update_layout(height=280, margin=dict(t=30, b=10, l=10, r=10))
            st.plotly_chart(fig_sent, use_container_width=True)
        else:
            st.info("Tidak ada data untuk Pie 1.")

    with pie_right:
        st.subheader("Persentase Ketepatan Waktu")
        if not df_monthly_norm.empty:
            # Pie 2: Ketepatan Waktu (TEPAT WAKTU, TERLAMBAT, TIDAK MENGIRIM)
            status_order = ["TEPAT WAKTU", "TERLAMBAT", "TIDAK MENGIRIM"]
            status_series = df_monthly_norm["status"].value_counts().reindex(status_order).fillna(0)
            df_status_pie = pd.DataFrame({"label": status_series.index.astype(str), "count": status_series.values})

            color_map_status = {
                "TEPAT WAKTU": "#35A29F",
                "TERLAMBAT": "#0B666A",
                "TIDAK MENGIRIM": "#071952"
            }
            fig_status = px.pie(df_status_pie, names="label", values="count", hole=0.4, color="label",
                                color_discrete_map=color_map_status)
            fig_status.update_traces(textinfo="percent+label", textposition="inside", insidetextorientation="radial")
            fig_status.update_layout(height=280, margin=dict(t=30, b=10, l=10, r=10))
            st.plotly_chart(fig_status, use_container_width=True)
        else:
            st.info("Tidak ada data untuk Pie 2.")
    # --------------------------
    # Render tabel per stasiun (FIX FINAL: ambil time_diff_hours dari df_monthly jika perlu; atur AgGrid)
    # --------------------------
    st.markdown("---")
    st.subheader("Tabel Data Per Stasiun")

    def ensure_time_diff_from_source(df_norm: pd.DataFrame, df_src: pd.DataFrame) -> pd.DataFrame:
        """
        Pastikan kolom 'time_diff_hours' tersedia di df_norm:
        - jika df_norm sudah punya, return langsung
        - jika tidak, ambil dari df_src (sheet monthly) berdasarkan station_name,wmoid,report_month
        """
        if df_norm is None:
            df_norm = pd.DataFrame()
        if df_src is None:
            df_src = pd.DataFrame()

        df_out = df_norm.copy() if not df_norm.empty else pd.DataFrame()

        # jika sudah ada column time_diff_hours di normalized, kita gunakan itu
        if "time_diff_hours" in df_out.columns and df_out["time_diff_hours"].notna().any():
            return df_out

        # kalau tidak ada, ambil dari source df_src (asumsi df_src berisi kolom time_diff_hours asli)
        if "time_diff_hours" not in df_out.columns and not df_src.empty:
            # pilih kolom kunci untuk merge
            key_cols = []
            for k in ["station_name","wmoid","report_month"]:
                if k in df_out.columns and k in df_src.columns:
                    key_cols.append(k)
            # jika df_out kosong (misalnya kita akan tunjukkan df_src langsung), set df_out = df_src subset
            if df_out.empty:
                # hanya ambil kolom yang relevan dari source
                df_out = df_src.copy()
            else:
                # lakukan merge left untuk menambahkan time_diff_hours dari df_src jika ada
                if key_cols:
                    # ambil unique source columns
                    src_cols = ["time_diff_hours"] + [c for c in key_cols if c in df_src.columns]
                    src_unique = df_src[src_cols].drop_duplicates()
                    try:
                        df_out = df_out.merge(src_unique, on=key_cols, how="left", suffixes=("","_from_src"))
                        # jika merge menghasilkan kolom time_diff_hours_from_src, gunakan yang itu jika original kosong
                        if "time_diff_hours" not in df_out.columns and "time_diff_hours_from_src" in df_out.columns:
                            df_out = df_out.rename(columns={"time_diff_hours_from_src":"time_diff_hours"})
                        else:
                            # jika kolom time_diff_hours ada tapi banyak NaN, fillna dari sumber
                            if "time_diff_hours" in df_out.columns and "time_diff_hours_from_src" in df_out.columns:
                                df_out["time_diff_hours"] = df_out["time_diff_hours"].fillna(df_out["time_diff_hours_from_src"])
                                df_out = df_out.drop(columns=["time_diff_hours_from_src"])
                    except Exception:
                        # kalau merge gagal, fallback: tambahkan kolom time_diff_hours kosong
                        if "time_diff_hours" not in df_out.columns:
                            df_out["time_diff_hours"] = "-"
                else:
                    # tidak ada kolom kunci cocok -> fallback: tambahkan kolom dari src by index if lengths match
                    if len(df_out) == len(df_src):
                        df_out["time_diff_hours"] = df_src.get("time_diff_hours", pd.Series(["-"]*len(df_out))).values
                    else:
                        if "time_diff_hours" not in df_out.columns:
                            df_out["time_diff_hours"] = "-"
        # ensure column exists
        if "time_diff_hours" not in df_out.columns:
            df_out["time_diff_hours"] = "-"
        return df_out

    def prepare_display_df_for_table(df_norm: pd.DataFrame, df_src: pd.DataFrame) -> pd.DataFrame:
        """
        Siapkan DataFrame untuk ditampilkan:
        - drop LAT,LON,time_diff_hours_num,tepat_waktu,monitoring_bulan
        - pastikan time_diff_hours berasal dari source jika perlu
        - rename header ke Bahasa Indonesia
        """
        if (df_norm is None or df_norm.empty) and (df_src is None or df_src.empty):
            return pd.DataFrame()

        # prefer df_norm, tapi gunakan df_src sebagai fallback
        df_use = df_norm.copy() if not (df_norm is None or df_norm.empty) else df_src.copy()

        # drop kolom yg tidak mau tampil
        for c in ["LAT","LON","time_diff_hours_num","tepat_waktu","terkirim","monitoring_bulan"]:
            if c in df_use.columns:
                df_use = df_use.drop(columns=[c], errors='ignore')

        # pastikan time_diff_hours muncul (ambil dari normalized atau source)
        df_use = ensure_time_diff_from_source(df_use, df_src)

        # susun kolom relevant
        wanted = ["station_name","wmoid","report_month","status","received_at","time_diff_hours"]
        cols_present = [c for c in wanted if c in df_use.columns]
        other_cols = [c for c in df_use.columns if c not in cols_present]
        df_res = df_use[cols_present + other_cols].copy()

        # rename column ke bahasa indonesia
        rename_map = {
            "station_name":"Nama Stasiun",
            "wmoid":"WMO ID",
            "report_month":"Tahun-Bulan",
            "status":"Status",
            "received_at":"Diterima",
            "time_diff_hours":"Time Diff (jam)"
        }
        df_res = df_res.rename(columns={k:v for k,v in rename_map.items() if k in df_res.columns})

        return df_res

    # prepare display df: gunakan normalized dulu, source adalah df_monthly as original sheet
    df_display = prepare_display_df_for_table(df_monthly_norm if not df_monthly_norm.empty else pd.DataFrame(), df_monthly if not df_monthly.empty else pd.DataFrame())

    tab1, tab2, tab3 = st.tabs(["Tepat Waktu","Terlambat","Tidak Mengirim"])

    def render_table_html(df_show: pd.DataFrame, height: int = 420, table_id: str = "tbl"):
        """
        Render DataFrame sebagai full-width HTML table dengan:
        - header sticky (freeze)
        - cell teks dapat wrap (tidak terpotong)
        - scroll vertical/horizontal
        """
        if df_show is None or df_show.empty:
            st.info("Tidak ada data untuk ditampilkan.")
            return

        # Tombol download CSV
        csv_bytes = df_show.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Unduh CSV", data=csv_bytes, file_name=f"{table_id}.csv", mime="text/csv")

        cols = list(df_show.columns)

        # Build table head & body (escape teks)
        thead_cells = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
        tbody_rows = []
        for _, row in df_show.iterrows():
            cells = []
            for c in cols:
                val = row[c]
                cell_text = "" if pd.isna(val) else str(val)
                cells.append(f"<td>{html.escape(cell_text)}</td>")
            tbody_rows.append("<tr>" + "".join(cells) + "</tr>")
        tbody_html = "\n".join(tbody_rows)

        # Colgroup widths map — sesuaikan jika perlu
        width_map = {
            "Nama Stasiun": "40%",
            "WMO ID": "8%",
            "Bulan": "10%",
            "Tahun-Bulan": "10%",
            "Status": "12%",
            "Diterima": "20%",
            "Time Diff (jam)": "10%",
            "time_diff_hours": "12%",
        }
        colgroup = "<colgroup>"
        for c in cols:
            w = width_map.get(c, "auto")
            colgroup += f'<col style="width:{w}">'
        colgroup += "</colgroup>"

        css = f"""
        <style>
        /* container: fixed height, scroll inside */
        .table-wrap-{table_id} {{
            width: 100%;
            max-width: 100%;
            height: {height}px;
            overflow: auto;
            border: 1px solid #e6eef3;
            border-radius: 6px;
            background: #ffffff;
        }}
        table#{table_id} {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed; /* respect col widths but allow wrapping */
            font-family: "Segoe UI", Roboto, Arial, sans-serif;
        }}
        table#{table_id} thead th {{
            position: sticky;
            top: 0;
            background: #ffffff;
            z-index: 5;
            text-align: left;
            padding: 12px 14px;
            border-bottom: 1px solid #e6eef3;
            font-weight: 600;
            color: #243447;
        }}
        table#{table_id} tbody td {{
            padding: 10px 14px;
            border-bottom: 1px solid #f2f7fa;
            /* allow wrapping and long-word break */
            white-space: normal;
            word-wrap: break-word;
            overflow-wrap: anywhere;
            hyphens: auto;
            vertical-align: top;
        }}
        table#{table_id} tbody tr:nth-child(odd) {{
            background: #fbfeff;
        }}
        table#{table_id} tbody tr:hover {{
            background: #e8f6fb;
        }}
        /* Nama Stasiun prefer wide, but allow wrap if needed */
        table#{table_id} td:first-child {{
            min-width: 240px;
            max-width: 60%;
        }}
        /* make other columns flexible */
        table#{table_id} td:nth-child(2) {{ text-align:center; }}
        /* small screens adjustments */
        @media (max-width: 900px) {{
            .table-wrap-{table_id} {{ height: {max(300, height//2)}px; }}
            table#{table_id} thead th, table#{table_id} tbody td {{
                padding: 8px;
                font-size: 13px;
            }}
        }}
        </style>
        """

        table_html = f"""
        {css}
        <div class="table-wrap-{table_id}">
        <table id="{table_id}">
            {colgroup}
            <thead><tr>{thead_cells}</tr></thead>
            <tbody>
            {tbody_html}
            </tbody>
        </table>
        </div>
        """

        # Render HTML with scrolling area
        components.html(table_html, height=height+16, scrolling=True)
    with tab1:
        df_tp = df_display[df_display["Status"] == "TEPAT WAKTU"]
        render_table_html(df_tp, height=420, table_id="tp")

    with tab2:
        df_tl = df_display[df_display["Status"] == "TERLAMBAT"]
        render_table_html(df_tl, height=420, table_id="tl")

    with tab3:
        df_nm = df_display[df_display["Status"] == "TIDAK MENGIRIM"]
        render_table_html(df_nm, height=420, table_id="nm")

# --------------------------
#  HALAMAN TAHUNAN
# --------------------------
else:
    st.header("Monitoring CLIMAT — Tahunan")
    sub_page = st.sidebar.radio("Pilih Sub Page:", ["Monitoring Tahunan", "Performa Stasiun", "Rincian Data Tahunan"], key="sub_page_radio")

    # helper kecil: normalisasi header & deteksi bulan
    MONTH_ABBR = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agt","Sep","Okt","Nov","Des"]
    def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Strip column names, remove BOM, unify spacing."""
        df = df.copy()
        df.columns = [str(c).strip().replace('\ufeff','') for c in df.columns]
        return df

    def detect_month_columns(cols):
        """Return month cols in order Jan..Des if present (keeps original column names)."""
        res = []
        for m in MONTH_ABBR:
            # pick any column whose first 3 chars (after strip) match month abbr case-insensitive
            for c in cols:
                if str(c).strip()[:3].lower() == m[:3].lower():
                    if c not in res:
                        res.append(c)
                        break
        # also include any leftover columns that exactly match e.g. 'Bulan' or 'Jan-2025' forms
        # but we prefer the ordered res above
        return res

    # ========================
    # REKAP TAHUNAN NASIONAL
    # ========================
    if sub_page == "Monitoring Tahunan":
        st.subheader("Availability")

        # Pastikan df_status sudah ter-load; kalau kosong beri hint dan coba normalize header
        if df_status is None or df_status.empty:
            st.info("Sheet 'Status' kosong atau belum dimuat. Pastikan 'Yearly sheet URL' + optional 'Status sheet gid' di sidebar diisi.")
        else:
            df_status = df_status.copy()
            df_status = clean_columns(df_status)

            # Try to find LAT/LON column name variants
            lat_col = None
            lon_col = None
            for c in df_status.columns:
                cl = c.strip().lower()
                if cl in ['lat','latitude','coord_lat','y'] or 'lat' == cl[:3]:
                    lat_col = c
                if cl in ['lon','lng','longitude','coord_lon','x'] or cl[:3] in ['lon','lng']:
                    lon_col = c
            # fallback: common direct names
            lat_col = lat_col or ('LAT' if 'LAT' in df_status.columns else None)
            lon_col = lon_col or ('LON' if 'LON' in df_status.columns else None)

            if not lat_col or not lon_col:
                st.warning("Kolom koordinat (LAT / LON) tidak ditemukan. Nama kolom saat ini: " + ", ".join(df_status.columns))
            else:
                # Normalize numeric format (replace comma decimal -> dot) and coerce
                df_status[lat_col] = pd.to_numeric(df_status[lat_col].astype(str).str.replace(',','.'), errors='coerce')
                df_status[lon_col] = pd.to_numeric(df_status[lon_col].astype(str).str.replace(',','.'), errors='coerce')
                # Rename to standard names for downstream code
                df_status = df_status.rename(columns={lat_col: 'LAT', lon_col: 'LON'})

                # Detect month columns robustly
                bulan_cols = detect_month_columns(df_status.columns)
                if not bulan_cols:
                    # fallback: columns that contain any of the month abbreviations inside
                    possible = [c for c in df_status.columns if any(m[:3].lower() in str(c).lower() for m in MONTH_ABBR)]
                    # sort based on month order
                    ordered = []
                    for m in MONTH_ABBR:
                        for c in possible:
                            if c not in ordered and m[:3].lower() in str(c).lower():
                                ordered.append(c)
                    bulan_cols = ordered

                if not bulan_cols:
                    st.warning("Kolom bulan (Jan..Des) tidak terdeteksi pada sheet Status. Kolom yg tersedia: " + ", ".join(df_status.columns))
                else:
                    # Normalize status text in month cells
                    def norm_cell(x):
                        if pd.isna(x):
                            return ''
                        s = str(x).strip().upper()
                        # common normalization
                        if 'TEPAT' in s:
                            return 'TEPAT WAKTU'
                        if 'TERLAMBAT' in s:
                            return 'TERLAMBAT'
                        if 'TIDAK' in s or 'TIDAK MENGIRIM' in s or 'MENGIRIM' not in s and ('-' in s or s=='' ):
                            # preserve empty as not sending, but be conservative: only exact matches map
                            if 'TIDAK MENGIRIM' in s or 'TIDAK' in s:
                                return 'TIDAK MENGIRIM'
                            return s
                        # default: return original uppercase trimmed
                        return s

                    for c in bulan_cols:
                        df_status[c] = df_status[c].apply(norm_cell)

                    # compute counts per station
                    def hitung(row, kata):
                        return sum(1 for c in bulan_cols if str(row.get(c, '')).strip().upper() == kata)

                    df_status['TEPAT_WAKTU'] = df_status.apply(lambda r: hitung(r, 'TEPAT WAKTU'), axis=1)
                    df_status['TERLAMBAT'] = df_status.apply(lambda r: hitung(r, 'TERLAMBAT'), axis=1)
                    df_status['TIDAK_MENGIRIM'] = df_status.apply(lambda r: hitung(r, 'TIDAK MENGIRIM'), axis=1)
                    df_status['TOTAL'] = df_status[['TEPAT_WAKTU','TERLAMBAT','TIDAK_MENGIRIM']].sum(axis=1).replace(0, np.nan).fillna(0)
                    df_status['pct_tepat'] = (df_status['TEPAT_WAKTU'] / df_status['TOTAL']).fillna(0)

                    # Warna marker decision function (keputusan sama seperti spesifikasi)
                    def color_hex(row):
                        pct = float(row.get('pct_tepat', 0))
                        terl, tdk = int(row.get('TERLAMBAT', 0)), int(row.get('TIDAK_MENGIRIM', 0))
                        if pct >= 0.8:
                            return '#2ecc71'
                        if pct >= 0.3:
                            return '#f1c40f'
                        if pct < 0.3 and terl > tdk:
                            return '#e74c3c'
                        return '#000000'

                    # --- PETA ESRI ---
                    import folium
                    from folium import Map, CircleMarker, Popup
                    import streamlit.components.v1 as components

                    # # center map using median to be robust to outliers
                    # center_lat = -2.23
                    # center_lon = 117.3
                    # center map di rata-rata koordinat
                    try:
                        center_lat = -2.2331
                        center_lon = 117.2841
                        # center_lat = float(df_map['LAT'].astype(float).mean())
                        # center_lon = float(df_map['LON'].astype(float).mean())
                    except Exception:
                        center_lat, center_lon = -2.2331, 117.2841

                    m = Map(location=[center_lat, center_lon], zoom_start=5,
                            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
                            attr='Esri')

                    marker_coords = []
                    for _, r in df_status.iterrows():
                        if pd.isna(r['LAT']) or pd.isna(r['LON']):
                            continue
                        popup_html = f"""
                        <b>{r.get('station_name','')}</b><br>
                        WMO: {r.get('wmoid','')}<br>
                        Tepat Waktu: {int(r['TEPAT_WAKTU'])}<br>
                        Terlambat: {int(r['TERLAMBAT'])}<br>
                        Tidak Mengirim: {int(r['TIDAK_MENGIRIM'])}
                        """
                        CircleMarker(
                            location=[r['LAT'], r['LON']],
                            radius=7,
                            color=color_hex(r),
                            fill=True, fill_color=color_hex(r), fill_opacity=0.9,
                            popup=Popup(popup_html, max_width=250)
                        ).add_to(m)
                        marker_coords.append((r['LAT'], r['LON']))

                    if marker_coords:
                        m.fit_bounds(marker_coords, padding=(30,30))

                    # Legend
                    legend_html = """
                    <div style="position:absolute;top:10px;right:10px;z-index:9999;
                                background:rgba(255,255,255,0.95);padding:8px;border-radius:6px;
                                border:1px solid #ddd;font-size:13px;">
                        <b>Keterangan</b><br>
                        <span style="background:#2ecc71;width:12px;height:12px;display:inline-block;margin-right:6px;"></span> ≥80% Tepat Waktu<br>
                        <span style="background:#f1c40f;width:12px;height:12px;display:inline-block;margin-right:6px;"></span> 30–79% Tepat Waktu<br>
                        <span style="background:#e74c3c;width:12px;height:12px;display:inline-block;margin-right:6px;"></span> <30% (Terlambat > Tidak)<br>
                        <span style="background:#000;width:12px;height:12px;display:inline-block;margin-right:6px;"></span> <30% (Terlambat ≤ Tidak)
                    </div>
                    """
                    m.get_root().html.add_child(folium.Element(legend_html))
                    components.html(m._repr_html_(), height=680, scrolling=False)

                    # --- DIAGRAM BATANG + PIE CHART ---
                    import plotly.express as px
                    import plotly.graph_objects as go

                    st.markdown("Diagram Batang per Bulan (Januari—Desember)")

                    # Build df_bulan ensuring order Jan..Des even if some months missing
                    df_bulan_rows = []
                    for m in MONTH_ABBR:
                        if m in bulan_cols:
                            col = m
                        else:
                            # find actual column that contains same 3-letter code
                            col = next((c for c in bulan_cols if c.strip()[:3].lower() == m[:3].lower()), None)
                        if col:
                            tepat = (df_status[col].str.upper() == 'TEPAT WAKTU').sum()
                            terlambat = (df_status[col].str.upper() == 'TERLAMBAT').sum()
                            tidak = (df_status[col].str.upper() == 'TIDAK MENGIRIM').sum()
                            total = tepat + terlambat + tidak
                            df_bulan_rows.append({
                                'Bulan': m,
                                'Tepat Waktu': 100*tepat/total if total>0 else 0,
                                'Terlambat': 100*terlambat/total if total>0 else 0,
                                'Tidak Mengirim': 100*tidak/total if total>0 else 0
                            })
                        else:
                            # month not present in sheet -> append zeros
                            df_bulan_rows.append({'Bulan': m, 'Tepat Waktu':0, 'Terlambat':0, 'Tidak Mengirim':0})

                    df_bulan = pd.DataFrame(df_bulan_rows)

                    bar_col, pie_col = st.columns([3,1.5])

                    with bar_col:
                        fig_bar = go.Figure()
                        fig_bar.add_bar(x=df_bulan['Bulan'], y=df_bulan['Tepat Waktu'], name="Tepat Waktu", marker_color="#35A29F")
                        fig_bar.add_bar(x=df_bulan['Bulan'], y=df_bulan['Terlambat'], name="Terlambat", marker_color="#0B666A")
                        fig_bar.add_bar(x=df_bulan['Bulan'], y=df_bulan['Tidak Mengirim'], name="Tidak Mengirim", marker_color="#071952")
                        fig_bar.update_layout(barmode='group', height=400, yaxis_title="Persen (%)",
                                            margin=dict(t=30, b=40, l=40, r=10))
                        st.plotly_chart(fig_bar, use_container_width=True)

                    with pie_col:
                        st.markdown("### Pie Chart Total Nasional")
                        total_tepat = int(df_status['TEPAT_WAKTU'].sum())
                        total_terlambat = int(df_status['TERLAMBAT'].sum())
                        total_tidak = int(df_status['TIDAK_MENGIRIM'].sum())
                        df_total = pd.DataFrame({
                            'Kategori': ['Tepat Waktu', 'Terlambat', 'Tidak Mengirim'],
                            'Jumlah': [total_tepat, total_terlambat, total_tidak]
                        })
                        fig_pie = px.pie(df_total, names='Kategori', values='Jumlah',
                                        color='Kategori',
                                        color_discrete_map={'Tepat Waktu':'#35A29F','Terlambat':'#0B666A','Tidak Mengirim':'#071952'},
                                        hole=0.4)
                        fig_pie.update_layout(height=400, margin=dict(t=30, b=30, l=10, r=10))
                        st.plotly_chart(fig_pie, use_container_width=True)
        
    # ========================
    # PERFORMA STASIUN (FULL, fix duplicate-col bug)
    # ========================
    elif sub_page == "Performa Stasiun":
        st.subheader("Performa Stasiun")
        if df_status is None or df_status.empty:
            st.info("Sheet 'Status' kosong — pastikan sheet tersedia dan gid benar.")
        else:
            # --- PREPARE DATA ---
            df_st = df_status.copy()
            # normalize column names
            df_st.columns = [str(c).strip().replace('\ufeff', '') for c in df_st.columns]

            # detect month columns robustly
            MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agt", "Sep", "Okt", "Nov", "Des"]
            month_cols = []
            for m in MONTH_ABBR:
                match = next((c for c in df_st.columns if str(c).strip()[:3].lower() == m[:3].lower()), None)
                if match:
                    month_cols.append(match)

            # fallback: include any col that contains month abbrev anywhere
            if not month_cols:
                possible = [c for c in df_st.columns if any(m[:3].lower() in str(c).lower() for m in MONTH_ABBR)]
                ordered = []
                for m in MONTH_ABBR:
                    for c in possible:
                        if c not in ordered and m[:3].lower() in str(c).lower():
                            ordered.append(c)
                month_cols = ordered

            if not month_cols:
                st.warning("Gagal mendeteksi kolom bulan (Jan..Des) di sheet Status. Kolom tersedia: " + ", ".join(df_st.columns))
                st.stop()

            # Normalize month values -> canonical statuses
            def norm_status_cell(x):
                if pd.isna(x):
                    return ''
                s = str(x).strip().upper()
                if 'TEPAT' in s:
                    return 'TEPAT WAKTU'
                if 'TERLAMBAT' in s:
                    return 'TERLAMBAT'
                if 'TIDAK' in s or 'TIDAK MENGIRIM' in s:
                    return 'TIDAK MENGIRIM'
                if s in ['', '-', 'N/A', 'NA']:
                    return 'TIDAK MENGIRIM'
                return s

            for c in month_cols:
                df_st[c] = df_st[c].apply(norm_status_cell)

            # Compute counts per station
            df_counts = pd.DataFrame({
                'TEPAT_WAKTU': df_st[month_cols].apply(lambda col: col == 'TEPAT WAKTU').sum(axis=1),
                'TERLAMBAT': df_st[month_cols].apply(lambda col: col == 'TERLAMBAT').sum(axis=1),
                'TIDAK_MENGIRIM': df_st[month_cols].apply(lambda col: col == 'TIDAK MENGIRIM').sum(axis=1)
            })
            # ensure ints
            for c in ['TEPAT_WAKTU', 'TERLAMBAT', 'TIDAK_MENGIRIM']:
                df_counts[c] = pd.to_numeric(df_counts[c], errors='coerce').fillna(0).astype(int)
            df_counts['TOTAL'] = df_counts[['TEPAT_WAKTU', 'TERLAMBAT', 'TIDAK_MENGIRIM']].sum(axis=1)

            # concat counts with original info
            df_perf = pd.concat([df_st.reset_index(drop=True), df_counts.reset_index(drop=True)], axis=1)

            # ----- NEW: handle duplicate column names -----
            # detect duplicates
            dup_mask = df_perf.columns.duplicated(keep=False)
            if dup_mask.any():
                dup_cols = [c for i,c in enumerate(df_perf.columns) if dup_mask[i]]
                # show debug info in sidebar to help diagnose (can be removed later)
                # st.sidebar.warning(f"Terdeteksi kolom duplikat di df_perf: {dup_cols}. Duplikat akan dihapus dan kolom terakhir dipertahankan.")
                # keep last occurrence for each duplicated column name
                df_perf = df_perf.loc[:, ~df_perf.columns.duplicated(keep='last')]

            # compute pct_tepat vectorized (safe)
            # ensure numeric types for counts
            for c in ['TEPAT_WAKTU', 'TERLAMBAT', 'TIDAK_MENGIRIM', 'TOTAL']:
                if c in df_perf.columns:
                    df_perf[c] = pd.to_numeric(df_perf[c], errors='coerce').fillna(0).astype(int)

            if 'TEPAT_WAKTU' in df_perf.columns and 'TOTAL' in df_perf.columns:
                df_perf['pct_tepat'] = df_perf['TEPAT_WAKTU'].astype(float).div(df_perf['TOTAL'].replace({0: np.nan})).fillna(0)
            else:
                df_perf['pct_tepat'] = 0.0

            # prepare display columns
            display_station_col = None
            if 'station_name' in df_perf.columns:
                display_station_col = 'station_name'
            elif 'Nama Stasiun' in df_perf.columns:
                display_station_col = 'Nama Stasiun'
            else:
                display_station_col = df_perf.columns[0]

            # Default sort: pct_tepat descending
            df_perf = df_perf.sort_values(by='pct_tepat', ascending=False).reset_index(drop=True)

            # --- UI: filters & pagination ---
            left_col, right_col = st.columns([3,1])
            with left_col:
                search_text = st.text_input("🔎 Cari nama stasiun (substring, case-insensitive)", value="")
            with right_col:
                only_with_data = st.checkbox("Tampilkan hanya stasiun dengan data", value=True)

            df_filtered = df_perf.copy()
            if search_text.strip():
                mask = df_filtered[display_station_col].astype(str).str.contains(search_text.strip(), case=False, na=False)
                df_filtered = df_filtered[mask]

            if only_with_data:
                df_filtered = df_filtered[df_filtered['TOTAL'] > 0]

            total_items = len(df_filtered)
            if total_items == 0:
                st.info("Tidak ada stasiun yang cocok dengan filter.")
                st.stop()

            per_page = st.selectbox("Chart per halaman", options=[6,9,12,15,18,24], index=4, help="Jumlah pie chart per halaman")
            total_pages = max(1, math.ceil(total_items / per_page))
            page = st.number_input(f"Halaman (1..{total_pages})", min_value=1, max_value=total_pages, value=1, step=1)

            start_idx = (page - 1) * per_page
            end_idx = min(start_idx + per_page, total_items)
            subset = df_filtered.iloc[start_idx:end_idx]

            st.markdown(f"Menampilkan stasiun **{start_idx+1}**–**{end_idx}** dari **{total_items}** hasil filter.")

            # --- Render pie-chart grid ---
            ncols = 3
            rows = math.ceil(len(subset) / ncols)
            for r in range(rows):
                cols = st.columns(ncols)
                for ci in range(ncols):
                    idx = r * ncols + ci
                    if idx >= len(subset):
                        cols[ci].empty()
                        continue
                    row = subset.iloc[idx]
                    station_label = row.get(display_station_col, f"Stasiun {start_idx + idx + 1}")
                    wmoid = row.get('wmoid', '-')
                    vals = [int(row['TEPAT_WAKTU']), int(row['TERLAMBAT']), int(row['TIDAK_MENGIRIM'])]
                    labels = ['Tepat Waktu', 'Terlambat', 'Tidak Mengirim']
                    df_tmp = pd.DataFrame({'Kategori': labels, 'Jumlah': vals})

                    # --- Improved: wrap station name into lines and use <br> only (Plotly-safe) ---
                    def wrap_text_for_title(s: str, max_chars: int = 40) -> str:
                        """
                        Wrap text by inserting <br> at whitespace so the title fits in two (or more) lines.
                        Keeps words intact and prefers splitting at a space before max_chars.
                        """
                        s = str(s).strip()
                        if len(s) <= max_chars:
                            return s
                        parts = []
                        words = s.split()
                        current = ""
                        for w in words:
                            if current == "":
                                current = w
                            elif len(current) + 1 + len(w) <= max_chars:
                                current = current + " " + w
                            else:
                                parts.append(current)
                                current = w
                        if current:
                            parts.append(current)
                        return "<br>".join(parts)

                    # build wrapped title (Plotly accepts <br> for newlines)
                    wrapped_title = wrap_text_for_title(station_label, max_chars=42)
                    title_text = f"{wrapped_title}<br>WMOID: {wmoid}"

                    # prepare data
                    df_tmp = pd.DataFrame({'Kategori': labels, 'Jumlah': vals})

                    fig = px.pie(
                        df_tmp,
                        names='Kategori',
                        values='Jumlah',
                        color='Kategori',
                        color_discrete_map={
                            'Tepat Waktu': '#35A29F',
                            'Terlambat': '#0B666A',
                            'Tidak Mengirim': '#071952'
                        },
                        hole=0.42
                    )

                    # show percent inside donut and hover shows counts
                    fig.update_traces(
                        textinfo='percent',
                        textposition='inside',
                        insidetextorientation='radial',
                        hovertemplate="%{label}: %{value} (%.1f%%)<extra></extra>",
                        marker=dict(line=dict(color='white', width=1.5))
                    )

                    # Set title using only <br> (safe). Increase top margin & height for wrap.
                    fig.update_layout(
                        title={'text': title_text, 'x': 0.5, 'xanchor': 'center', 'yanchor': 'top'},
                        title_font=dict(size=13),
                        margin=dict(t=88, b=10, l=8, r=8),
                        height=320,
                        showlegend=False,
                        uniformtext_minsize=10,
                        uniformtext_mode='hide'
                    )

                    cols[ci].plotly_chart(fig, use_container_width=True)




            # --- Download CSV result (filtered full table) ---
            csv_cols = [display_station_col, 'wmoid', 'TEPAT_WAKTU', 'TERLAMBAT', 'TIDAK_MENGIRIM', 'TOTAL', 'pct_tepat']
            available_csv_cols = [c for c in csv_cols if c in df_filtered.columns]
            csv_bytes = df_filtered[available_csv_cols].to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Performa Stasiun (CSV hasil filter)", data=csv_bytes, file_name="Performa_Stasiun_filtered.csv", mime="text/csv")

            # optional: show small summary table below charts (first 20 rows)
            st.markdown("**Ringkasan (top rows)**")
            st.dataframe(df_filtered[available_csv_cols].head(20), height=260)



    # ========================
    # RINCIAN DATA TAHUNAN
    # ========================
    elif sub_page == "Rincian Data Tahunan":
        st.subheader("Rincian Data Tahunan")
        tab1, tab2 = st.tabs(["DeltaHours", "Status"])
        with tab1:
            st.markdown("Tabel DeltaHours (No | Stasiun | WMO ID | Jan..Des) — menampilkan nilai jam saja (header bulan tanpa '(hrs)')")

            def _prepare_delta_display_local(df_src: pd.DataFrame) -> pd.DataFrame:
                """
                Robust prepare:
                - Detect month-like columns in source (keadaan sheet bervariasi).
                - Compute numeric hours using parse_delta_to_hours -> new *_hrs columns.
                - Map one numeric column per month (Jan..Des). Jika numeric semua NaN, fallback tampilkan teks ringkas.
                - Selalu kembalikan Nama Stasiun + WMO ID + columns Jan..Des (jika tersedia).
                - Tulis debug ke sidebar: head, dtypes, non-null counts per column.
                """
                if df_src is None or df_src.empty:
                    return pd.DataFrame()

                df = clean_columns(df_src.copy())

                # drop coords to reduce width
                for c in ['LAT', 'LON']:
                    if c in df.columns:
                        df = df.drop(columns=c)

                # detect month-like original columns (preserve original names)
                month_like_cols = [c for c in df.columns if str(c).strip()[:3].title() in MONTH_ABBR]
                if not month_like_cols:
                    month_like_cols = [c for c in df.columns if any(m[:3].lower() in str(c).lower() for m in MONTH_ABBR)]

                # if none -> just rename station/wmoid and return
                if not month_like_cols:
                    df_res = df.copy()
                    if 'station_name' in df_res.columns:
                        df_res = df_res.rename(columns={'station_name': 'Nama Stasiun'})
                    if 'wmoid' in df_res.columns:
                        df_res = df_res.rename(columns={'wmoid': 'WMO ID'})
                    # debug
                    # try:
                    #     st.sidebar.write("DEBUG: no month-like cols detected. Columns:", list(df_res.columns))
                    #     st.sidebar.write(df_res.head(3))
                    # except Exception:
                    #     pass
                    return df_res

                # create numeric *_hrs columns and keep original text col mapping
                hrs_map = {}      # orig_col -> hrs_colname (numeric)
                orig_map = {}     # month_base -> list of original cols encountered (preserve order)
                for orig in month_like_cols:
                    col_hrs = f"{orig}_hrs"
                    try:
                        df[col_hrs] = df[orig].apply(parse_delta_to_hours)
                    except Exception:
                        df[col_hrs] = np.nan
                    hrs_map[orig] = col_hrs
                    base = str(orig).strip()[:3].title()
                    orig_map.setdefault(base, []).append(orig)

                # choose one numeric col per month base (first encountered)
                month_to_src = {}  # 'Jan' -> (src_orig_col, src_hrs_col)
                for orig, col_hrs in hrs_map.items():
                    base = str(orig).strip()[:3].title()
                    if base not in month_to_src:
                        month_to_src[base] = (orig, col_hrs)

                # build df_res and rename station/wmoid and chosen numeric cols -> short names
                df_res = df.copy()
                rename_map = {}
                if 'station_name' in df_res.columns:
                    rename_map['station_name'] = 'Nama Stasiun'
                if 'wmoid' in df_res.columns:
                    rename_map['wmoid'] = 'WMO ID'
                for m in MONTH_ABBR:
                    if m in month_to_src:
                        _, src_hrs = month_to_src[m]
                        if src_hrs in df_res.columns:
                            rename_map[src_hrs] = m

                df_res = df_res.rename(columns=rename_map)

                # Reorder: Nama Stasiun, WMO ID, Jan..Des (present), then any remaining cols
                cols_order = []
                if 'Nama Stasiun' in df_res.columns:
                    cols_order.append('Nama Stasiun')
                if 'WMO ID' in df_res.columns:
                    cols_order.append('WMO ID')
                for m in MONTH_ABBR:
                    if m in df_res.columns:
                        cols_order.append(m)
                remaining = [c for c in df_res.columns if c not in cols_order]
                df_res = df_res[cols_order + remaining].copy()

                # Remove duplicate-named columns keeping first (avoid Series-in-cell)
                df_res = df_res.loc[:, ~df_res.columns.duplicated(keep='first')]

                # For each month column, if it's all-NaN numeric, fallback fill with simplified original text:
                for m in MONTH_ABBR:
                    if m in df_res.columns:
                        # check non-null count
                        nonnull_cnt = df_res[m].notna().sum()
                        if nonnull_cnt == 0:
                            # find original source column(s) that map to this month
                            src_info = month_to_src.get(m)
                            if src_info:
                                orig_col = src_info[0]  # original textual column name
                                if orig_col in df.columns:
                                    # create simplified textual representation: try extract first number (days/hours)
                                    def simplify_text_cell(x):
                                        if pd.isna(x):
                                            return ""
                                        s = str(x).strip()
                                        # try to extract a leading number (possibly negative) with optional decimal
                                        mnum = re.search(r"(-?\d+(\.\d+)?)", s)
                                        if mnum:
                                            return mnum.group(1)
                                        # fallback: return first short token (max 12 chars)
                                        toks = s.split()
                                        return (toks[0][:12] if toks else "")
                                    df_res[m] = df_res[orig_col].apply(lambda v: simplify_text_cell(v) if pd.isna(df_res.at[df_res.index[df_res.index.get_loc(0) if len(df_res.index)>0 else 0], m]) else df_res[m])
                                    # note: above is safe filler; simpler approach: fillna with simplified text
                                    df_res[m] = df_res[m].fillna(df[orig_col].apply(simplify_text_cell))

                # Try convert month columns to numeric where possible (keeps text if not numeric)
                for m in MONTH_ABBR:
                    if m in df_res.columns:
                        # attempt numeric conversion; if conversion yields NaN for some rows, we'll keep the text fallback in those rows
                        try:
                            numeric = pd.to_numeric(df_res[m], errors='coerce')
                            # where numeric is notna -> replace with numeric (so we display numbers); else keep original string
                            mask_num = numeric.notna()
                            if mask_num.any():
                                # create a mixed column: where numeric present use numeric, else keep original string
                                df_res[m] = df_res[m].where(~mask_num, numeric)
                        except Exception:
                            pass

                # # Sidebar debug: head, dtypes, non-null counts
                # try:
                #     st.sidebar.write("DEBUG: df_prepared head (first 4 rows):")
                #     st.sidebar.write(df_res.head(4))
                #     st.sidebar.write("DEBUG: df_prepared dtypes:")
                #     st.sidebar.write(df_res.dtypes.to_dict())
                #     # non-null counts per column (helpful)
                #     counts = {c: int(df_res[c].notna().sum()) for c in df_res.columns}
                #     st.sidebar.write("DEBUG: non-null counts:", counts)
                # except Exception:
                #     pass

                return df_res

            
            # st.sidebar.write("raw df_delta columns:", list(df_delta.columns))
            # st.sidebar.write("sample df_delta head:", df_delta.head(3))

            def _render_table_html_delta(df_show: pd.DataFrame, height: int = 420, table_id: str = "delta_tbl_local"):
                """Render DeltaHours table with word-wrap, sticky header and wider month columns.
                Robust to cell values that are Series/arrays by picking first element safely.
                """
                if df_show is None or df_show.empty:
                    st.info("Tidak ada data untuk ditampilkan.")
                    return

                # download button for the displayed table
                csv_bytes = df_show.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Unduh DeltaHours CSV (tabel tampil)", data=csv_bytes, file_name=f"{table_id}.csv", mime="text/csv")

                cols = list(df_show.columns)

                # mark which columns are month columns (everything after Nama Stasiun and WMO ID)
                month_start_idx = 2 if (len(cols) >= 2 and cols[0] == 'Nama Stasiun' and cols[1] == 'WMO ID') else 2
                month_cols_set = set(cols[month_start_idx:])  # treat these as month columns

                thead_cells = ""
                for i, c in enumerate(cols):
                    cls = "month" if c in month_cols_set else "sticky"
                    thead_cells += f'<th class="{cls}">{html.escape(str(c))}</th>'

                tbody_rows = []
                for _, row in df_show.iterrows():
                    cells = []
                    for c in cols:
                        # safe access: row[c] can be scalar OR Series if duplicate column names exist.
                        val = row[c]
                        # If val is Series / ndarray / list -> pick first non-null element (robust)
                        if isinstance(val, pd.Series):
                            if not val.empty:
                                # prefer first non-null
                                nonnull = val.dropna()
                                val = nonnull.iloc[0] if not nonnull.empty else val.iloc[0]
                            else:
                                val = np.nan
                        elif isinstance(val, (list, tuple, np.ndarray)):
                            try:
                                # convert to iterable and pick first non-null
                                arr = [x for x in val if not (x is None or (isinstance(x, float) and np.isnan(x)) or (isinstance(x, str) and x.strip()==''))]
                                val = arr[0] if arr else (val[0] if len(val)>0 else np.nan)
                            except Exception:
                                val = np.nan

                        # now val should be scalar (or np.nan)
                        if pd.isna(val):
                            cell_text = ""
                        else:
                            # format numeric month values with 1 decimal if float
                            if c in month_cols_set and isinstance(val, (float, int, np.floating, np.integer)):
                                try:
                                    cell_text = f"{float(val):.1f}"
                                except Exception:
                                    cell_text = str(val)
                            else:
                                cell_text = str(val)

                        cell_cls = "month" if c in month_cols_set else ""
                        cells.append(f'<td class="{cell_cls}">{html.escape(cell_text)}</td>')
                    tbody_rows.append("<tr>" + "".join(cells) + "</tr>")

                tbody_html = "\n".join(tbody_rows)

                # colgroup: Nama Stasiun wide, WMO ID medium, months flexible
                colgroup = "<colgroup>"
                for i, c in enumerate(cols):
                    if c == 'Nama Stasiun':
                        colgroup += '<col style="width:38%">'
                    elif c == 'WMO ID':
                        colgroup += '<col style="width:8%">'
                    else:
                        colgroup += '<col style="width:auto">'
                colgroup += "</colgroup>"

                css = f"""
                <style>
                .table-wrap-{table_id} {{
                    width:100%; max-width:100%; height:{height}px; overflow:auto;
                    border:1px solid #e6eef3; border-radius:6px; background:#fff;
                }}
                table#{table_id} {{ width:100%; border-collapse:collapse; table-layout:auto; font-family:Segoe UI, Roboto, Arial; }}
                table#{table_id} thead th {{
                    position:sticky; top:0; background:#fff; z-index:5; text-align:left; padding:10px 12px;
                    border-bottom:1px solid #e6eef3; font-weight:600; color:#243447; white-space:normal;
                }}
                table#{table_id} thead th.month {{ text-align:center; }}
                table#{table_id} tbody td {{
                    padding:8px 12px; border-bottom:1px solid #f2f7fa; white-space:normal; word-wrap:break-word; overflow-wrap:anywhere; vertical-align:top;
                }}
                table#{table_id} tbody td.month {{ text-align:center; min-width:90px; }} /* make month columns wider */
                table#{table_id} tbody tr:nth-child(odd) {{ background:#fbfeff; }}
                table#{table_id} tbody tr:hover {{ background:#e8f6fb; }}
                table#{table_id} td:first-child, table#{table_id} thead th:first-child {{ min-width:320px; max-width:48%; }}
                table#{table_id} td:nth-child(2), table#{table_id} thead th:nth-child(2) {{ min-width:100px; text-align:center; }}
                @media (max-width:900px) {{
                    .table-wrap-{table_id} {{ height:{max(240, height//2)}px; }}
                    table#{table_id} thead th, table#{table_id} tbody td {{ padding:6px; font-size:13px; }}
                    table#{table_id} tbody td.month {{ min-width:60px; }}
                }}
                </style>
                """

                table_html = f"""{css}<div class="table-wrap-{table_id}">{colgroup}<table id="{table_id}"><thead><tr>{thead_cells}</tr></thead><tbody>{tbody_html}</tbody></table></div>"""
                components.html(table_html, height=height+18, scrolling=True)


            # ---- run prepare + render ----
            if not df_delta.empty:
                df_prepared = _prepare_delta_display_local(df_delta)
                _render_table_html_delta(df_prepared, height=420, table_id="delta_yearly_local")
            else:
                st.info("DeltaHours sheet belum tersedia.")

            # fallback raw download
            st.download_button("📥 Download DeltaHours CSV (raw)", data=df_delta.to_csv(index=False), file_name="DeltaHours.csv")


        with tab2:
            st.markdown(" ")

            def _prepare_status_display_local(df_src: pd.DataFrame) -> pd.DataFrame:
                """
                Prepare status table:
                - Clean headers
                - Detect month-like columns and choose one column per month (Jan..Des)
                - Keep only station_name, wmoid, and months Jan..Des (present)
                - Rename station_name -> Nama Stasiun, wmoid -> WMO ID
                - Remove duplicate columns (keep first)
                """
                if df_src is None or df_src.empty:
                    return pd.DataFrame()

                df = clean_columns(df_src.copy())

                # drop coords to reduce width
                for c in ['LAT', 'LON']:
                    if c in df.columns:
                        df = df.drop(columns=c)

                # detect month-like columns (preserve original names)
                month_like_cols = [c for c in df.columns if str(c).strip()[:3].title() in MONTH_ABBR]
                if not month_like_cols:
                    month_like_cols = [c for c in df.columns if any(m[:3].lower() in str(c).lower() for m in MONTH_ABBR)]

                # map month base -> first matching original col (stable)
                month_map = {}
                for orig in month_like_cols:
                    base = str(orig).strip()[:3].title()
                    if base not in month_map:
                        month_map[base] = orig

                # build result dataframe selecting only needed cols
                df_res = df.copy()
                cols_keep = []
                if 'station_name' in df_res.columns:
                    cols_keep.append('station_name')
                if 'wmoid' in df_res.columns:
                    cols_keep.append('wmoid')
                for m in MONTH_ABBR:
                    if m in month_map and month_map[m] in df_res.columns:
                        cols_keep.append(month_map[m])

                # if no months found, still return station + wmoid
                df_res = df_res[cols_keep].copy()

                # rename station/wmoid and month source cols to canonical names
                rename_map = {}
                if 'station_name' in df_res.columns:
                    rename_map['station_name'] = 'Nama Stasiun'
                if 'wmoid' in df_res.columns:
                    rename_map['wmoid'] = 'WMO ID'
                for m in MONTH_ABBR:
                    if m in month_map:
                        src = month_map[m]
                        if src in df_res.columns:
                            rename_map[src] = m

                df_res = df_res.rename(columns=rename_map)

                # Reorder: Nama Stasiun, WMO ID, Jan..Des (present), then anything else
                cols_order = []
                if 'Nama Stasiun' in df_res.columns:
                    cols_order.append('Nama Stasiun')
                if 'WMO ID' in df_res.columns:
                    cols_order.append('WMO ID')
                for m in MONTH_ABBR:
                    if m in df_res.columns:
                        cols_order.append(m)
                remaining = [c for c in df_res.columns if c not in cols_order]
                df_res = df_res[cols_order + remaining].copy()

                # Remove duplicate column names keeping first
                df_res = df_res.loc[:, ~df_res.columns.duplicated(keep='first')]

                # # Debug minimal (tampil di sidebar)
                # try:
                #     st.sidebar.write("DEBUG: df_status_prepared columns:", list(df_res.columns))
                #     st.sidebar.write(df_res.head(2))
                # except Exception:
                #     pass

                return df_res

            def _render_table_html_status(df_show: pd.DataFrame, height: int = 420, table_id: str = "status_tbl_local"):
                """
                Render Status table: sticky header, Nama Stasiun wide, WMO ID center, months center with larger min-width.
                Handles Series/list cell values by picking first non-null element.
                """
                if df_show is None or df_show.empty:
                    st.info("Tidak ada data untuk ditampilkan.")
                    return

                # download button for displayed table
                csv_bytes = df_show.to_csv(index=False).encode("utf-8")
                st.download_button("📥 Unduh Status CSV (tabel tampil)", data=csv_bytes, file_name=f"{table_id}.csv", mime="text/csv")

                cols = list(df_show.columns)
                # treat everything after Nama Stasiun & WMO ID as month columns
                month_start_idx = 2 if (len(cols) >= 2 and cols[0] == 'Nama Stasiun' and cols[1] == 'WMO ID') else 2
                month_cols_set = set(cols[month_start_idx:])

                thead_cells = ""
                for i, c in enumerate(cols):
                    cls = "month" if c in month_cols_set else "sticky"
                    thead_cells += f'<th class="{cls}">{html.escape(str(c))}</th>'

                tbody_rows = []
                for _, row in df_show.iterrows():
                    cells = []
                    for c in cols:
                        val = row[c]
                        # safe handling if val is Series / list / ndarray
                        if isinstance(val, pd.Series):
                            if not val.empty:
                                nonnull = val.dropna()
                                val = nonnull.iloc[0] if not nonnull.empty else val.iloc[0]
                            else:
                                val = np.nan
                        elif isinstance(val, (list, tuple, np.ndarray)):
                            try:
                                arr = [x for x in val if not (x is None or (isinstance(x, float) and np.isnan(x)) or (isinstance(x, str) and x.strip() == ''))]
                                val = arr[0] if arr else (val[0] if len(val) > 0 else np.nan)
                            except Exception:
                                val = np.nan

                        if pd.isna(val):
                            cell_text = ""
                        else:
                            # keep textual status as-is, short if very long
                            s = str(val).strip()
                            # optional: collapse multi-line into single line
                            cell_text = " ".join(s.split())
                        cell_cls = "month" if c in month_cols_set else ""
                        cells.append(f'<td class="{cell_cls}">{html.escape(cell_text)}</td>')
                    tbody_rows.append("<tr>" + "".join(cells) + "</tr>")

                tbody_html = "\n".join(tbody_rows)

                # colgroup: Nama Stasiun wide, WMO ID medium, months flexible
                colgroup = "<colgroup>"
                for i, c in enumerate(cols):
                    if c == 'Nama Stasiun':
                        colgroup += '<col style="width:38%">'
                    elif c == 'WMO ID':
                        colgroup += '<col style="width:8%">'
                    else:
                        colgroup += '<col style="width:auto">'
                colgroup += "</colgroup>"

                css = f"""
                <style>
                .table-wrap-{table_id} {{
                    width:100%; max-width:100%; height:{height}px; overflow:auto;
                    border:1px solid #e6eef3; border-radius:6px; background:#fff;
                }}
                table#{table_id} {{ width:100%; border-collapse:collapse; table-layout:auto; font-family:Segoe UI, Roboto, Arial; }}
                table#{table_id} thead th {{
                    position:sticky; top:0; background:#fff; z-index:5; text-align:left; padding:10px 12px;
                    border-bottom:1px solid #e6eef3; font-weight:600; color:#243447; white-space:normal;
                }}
                table#{table_id} thead th.month {{ text-align:center; }}
                table#{table_id} tbody td {{
                    padding:8px 12px; border-bottom:1px solid #f2f7fa; white-space:normal; word-wrap:break-word; overflow-wrap:anywhere; vertical-align:top;
                }}
                table#{table_id} tbody td.month {{ text-align:center; min-width:120px; }} /* month columns slightly wider for readability */
                table#{table_id} tbody tr:nth-child(odd) {{ background:#fbfeff; }}
                table#{table_id} tbody tr:hover {{ background:#e8f6fb; }}
                table#{table_id} td:first-child, table#{table_id} thead th:first-child {{ min-width:320px; max-width:48%; }}
                table#{table_id} td:nth-child(2), table#{table_id} thead th:nth-child(2) {{ min-width:100px; text-align:center; }}
                @media (max-width:900px) {{
                    .table-wrap-{table_id} {{ height:{max(240, height//2)}px; }}
                    table#{table_id} thead th, table#{table_id} tbody td {{ padding:6px; font-size:13px; }}
                    table#{table_id} tbody td.month {{ min-width:80px; }}
                }}
                </style>
                """

                table_html = f"""{css}<div class="table-wrap-{table_id}">{colgroup}<table id="{table_id}"><thead><tr>{thead_cells}</tr></thead><tbody>{tbody_html}</tbody></table></div>"""
                components.html(table_html, height=height+18, scrolling=True)

            # ---- run prepare + render for Status tab ----
            if not df_status.empty:
                df_status_prepared = _prepare_status_display_local(df_status)
                _render_table_html_status(df_status_prepared, height=420, table_id="status_yearly_local")
            else:
                st.info("Status sheet belum tersedia.")

            # raw download fallback
            st.download_button("📥 Download Status CSV (raw)", data=df_status.to_csv(index=False), file_name="Status.csv")

# Footer
st.markdown("---")
st.caption("Dashboard Monitoring CLIMAT — BMKG")
st.caption("Aktualisasi Muhammad Iqbal Rahadzani")
