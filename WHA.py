import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import io

# ==========================================================
# 1. KONFIGURASI HALAMAN
# ==========================================================
st.set_page_config(
    page_title="WMS - Warehouse & Accounting",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================================
# 2. INIT FIREBASE (DATABASE PRODUKSI)
# ==========================================================
NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ==========================================================
# 3. FUNGSI PENGAMBILAN DATA (CACHE)
# ==========================================================
@st.cache_data(ttl=300) # Update tiap 5 menit kecuali direfresh manual
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";")
        except: df = pd.read_csv("Master_Produk.csv", sep=",")
        df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception: return pd.DataFrame()

@st.cache_data(ttl=120)
def load_stock_data():
    docs = db.collection(NAMA_DATABASE).stream()
    semua_data = []
    for doc in docs:
        doc_data = doc.to_dict()
        if 'data' in doc_data:
            semua_data.extend(doc_data['data'])
            
    if not semua_data: return pd.DataFrame()
    
    df = pd.DataFrame(semua_data)
    # Bersihkan data kosong
    valid_mask = ~df['Part Number'].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])
    return df[valid_mask]

@st.cache_data(ttl=60)
def load_log_mutasi():
    docs = db.collection(DB_LOG_MUTASI).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).limit(2000).stream()
    logs = [doc.to_dict() for doc in docs]
    if not logs: return pd.DataFrame()
    
    df = pd.DataFrame(logs)
    # Buang kolom Timestamp_System karena tidak bisa di-serialize JSON dengan mudah, kita pakai 'Waktu Transaksi'
    if 'Timestamp_System' in df.columns:
        df = df.drop(columns=['Timestamp_System'])
    return df

# Fungsi untuk export CSV rapi (Pemisah titik koma agar Excel Indonesia langsung baca)
def convert_df_to_csv(df):
    return df.to_csv(index=False, sep=";").encode('utf-8')

# ==========================================================
# 4. SIDEBAR NAVIGASI
# ==========================================================
st.sidebar.title("📊 Panel Laporan")
st.sidebar.caption("Modul Read-Only Accounting & WH")
menu = st.sidebar.radio("Pilih Laporan:", ["📦 Rekap Stok Aktual", "📖 Log Mutasi & Transaksi"])

st.sidebar.divider()
if st.sidebar.button("🔄 Segarkan Data Sekarang", use_container_width=True, type="primary"):
    st.cache_data.clear()
    st.rerun()

# ==========================================================
# 5. HALAMAN 1: REKAP STOK AKTUAL
# ==========================================================
if menu == "📦 Rekap Stok Aktual":
    st.title("📦 Rekapitulasi Stok Gudang Aktual")
    
    df_stock = load_stock_data()
    df_master = load_master_produk()
    
    if df_stock.empty:
        st.info("Gudang saat ini terdata kosong.")
    else:
        # Proses Agregasi / Pivot Data
        st.write("Memproses kalkulasi konversi berdasarkan Master Produk...")
        
        # Kelompokkan berdasarkan Part Number, Nama Produk, dan Lot
        df_group = df_stock.groupby(['Part Number', 'Nama Produk', 'Lot Number']).size().reset_index(name='Jumlah Pallet')
        
        rekap_list = []
        total_all_pallet = 0
        total_all_pcs = 0
        
        for _, row in df_group.iterrows():
            pnc = str(row['Part Number']).strip().upper()
            jml_pallet = int(row['Jumlah Pallet'])
            total_all_pallet += jml_pallet
            
            # Kalkulasi Konversi
            ip = 0
            if not df_master.empty and 'Part Number Clean' in df_master.columns and pnc in df_master['Part Number Clean'].values:
                mr = df_master[df_master['Part Number Clean'] == pnc].iloc[0]
                ip = pd.to_numeric(mr.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                ip = 0 if pd.isna(ip) else float(ip)
                
            total_pcs = jml_pallet * ip
            total_all_pcs += total_pcs
            
            rekap_list.append({
                'Part Number': pnc,
                'Nama Produk': row['Nama Produk'],
                'No. Lot': str(row['Lot Number']).strip(),
                'Total Pallet': jml_pallet,
                'Isi per Pallet (Pcs)': ip if ip > 0 else "Master Data Kosong",
                'Total Pcs': total_pcs
            })
            
        df_final_rekap = pd.DataFrame(rekap_list)
        
        # Metrik Atas
        c1, c2 = st.columns(2)
        c1.metric("Total Keseluruhan Pallet di Rak", f"{total_all_pallet:,.0f} Pallet")
        c2.metric("Estimasi Total Konversi", f"{total_all_pcs:,.0f} Pcs")
        
        st.divider()
        st.dataframe(df_final_rekap, use_container_width=True, hide_index=True)
        
        # Tombol Download
        csv_data = convert_df_to_csv(df_final_rekap)
        st.download_button(
            label="📥 Unduh Laporan Stok (CSV)",
            data=csv_data,
            file_name=f"Rekap_Stok_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

# ==========================================================
# 6. HALAMAN 2: LOG MUTASI & TRANSAKSI
# ==========================================================
elif menu == "📖 Log Mutasi & Transaksi":
    st.title("📖 Riwayat Mutasi Operator")
    
    df_log = load_log_mutasi()
    
    if df_log.empty:
        st.info("Belum ada log transaksi yang tercatat.")
    else:
        # Filter interaktif
        st.markdown("### Filter Data")
        col_f1, col_f2 = st.columns(2)
        
        with col_f1:
            jenis_list = ["Semua"] + sorted(df_log['Jenis Transaksi'].astype(str).unique().tolist())
            filter_jenis = st.selectbox("Filter Jenis Transaksi:", jenis_list)
            
        with col_f2:
            lot_list = ["Semua"] + sorted(df_log['No Lot'].astype(str).unique().tolist())
            filter_lot = st.selectbox("Filter No Lot:", lot_list)
            
        # Terapkan Filter
        df_tampil = df_log.copy()
        if filter_jenis != "Semua":
            df_tampil = df_tampil[df_tampil['Jenis Transaksi'] == filter_jenis]
        if filter_lot != "Semua":
            df_tampil = df_tampil[df_tampil['No Lot'] == filter_lot]
            
        # Urutkan berdasarkan waktu
        df_tampil = df_tampil.sort_values('Waktu Transaksi', ascending=False)
        
        st.divider()
        st.markdown(f"**Menampilkan {len(df_tampil)} riwayat transaksi:**")
        
        # Atur urutan kolom agar enak dibaca
        kolom_penting = ['Waktu Transaksi', 'Jenis Transaksi', 'Lokasi Rak', 'Nama Produk', 'No Lot', 'Mutasi Pallet', 'Keterangan']
        kolom_tersedia = [c for c in kolom_penting if c in df_tampil.columns]
        
        st.dataframe(df_tampil[kolom_tersedia], use_container_width=True, hide_index=True)
        
        # Tombol Download
        csv_log = convert_df_to_csv(df_tampil[kolom_tersedia])
        st.download_button(
            label="📥 Unduh Log Transaksi (CSV)",
            data=csv_log,
            file_name=f"Log_Mutasi_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
