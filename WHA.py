import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import json
import os
import io

st.set_page_config(page_title="WMS - Warehouse & Accounting", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

# Pengaman Layar Putih (Blank Screen)
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(dict(st.secrets["firebase"]))
        firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    st.error("Gagal terhubung ke Firebase.")
    st.stop()

# Fungsi Simpan & Baca Lokal (Hemat Kuota)
def sync_firebase_to_local():
    # Tarik Data Rak
    docs_rak = db.collection(NAMA_DATABASE).stream()
    data_rak = []
    for doc in docs_rak:
        doc_dict = doc.to_dict()
        if 'data' in doc_dict: data_rak.extend(doc_dict['data'])
    with open("offline_stok.json", "w") as f: json.dump(data_rak, f)
        
    # Tarik Data Log (Dibatasi 2000 untuk kecepatan)
    docs_log = db.collection(DB_LOG_MUTASI).order_by("Waktu Transaksi", direction=firestore.Query.DESCENDING).limit(2000).stream()
    data_log = [doc.to_dict() for doc in docs_log]
    # Bersihkan Timestamp_System agar bisa di-serialize JSON
    for d in data_log: d.pop('Timestamp_System', None)
    with open("offline_log.json", "w") as f: json.dump(data_log, f)
        
    # Catat Waktu Sinkronisasi Terakhir
    with open("offline_timestamp.txt", "w") as f: 
        f.write((datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S"))

def load_local_stok():
    if not os.path.exists("offline_stok.json"): return pd.DataFrame()
    with open("offline_stok.json", "r") as f:
        data = json.load(f)
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        valid_mask = ~df['Part Number'].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])
        return df[valid_mask]

def load_local_log():
    if not os.path.exists("offline_log.json"): return pd.DataFrame()
    with open("offline_log.json", "r") as f:
        return pd.DataFrame(json.load(f))

def get_last_sync_time():
    if os.path.exists("offline_timestamp.txt"):
        with open("offline_timestamp.txt", "r") as f: return f.read()
    return "Belum Pernah"

@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";")
        except: df = pd.read_csv("Master_Produk.csv", sep=",")
        df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception: return pd.DataFrame()

# SIDEBAR NAVIGASI
st.sidebar.title("📊 Panel Laporan")
menu = st.sidebar.radio("Pilih Laporan:", ["📦 Rekap Stok Aktual", "📖 Log Mutasi & Transaksi"])

st.sidebar.divider()
st.sidebar.caption(f"🕒 Sinkronisasi Terakhir:\n**{get_last_sync_time()}**")

if st.sidebar.button("🔄 Tarik Data Server (Gunakan Kuota)", type="primary"):
    with st.spinner("Mengunduh data terbaru dari Firebase ke memori lokal..."):
        sync_firebase_to_local()
        st.sidebar.success("Selesai! Aplikasi kini membaca data offline terbaru.")
        time.sleep(1)
        st.rerun()

st.sidebar.info("Aplikasi membaca file cache lokal untuk menghemat kuota pembacaan (reads) Firebase Anda.")

# HALAMAN 1: REKAP STOK AKTUAL (BACA LOKAL)
if menu == "📦 Rekap Stok Aktual":
    st.title("📦 Rekapitulasi Stok Gudang (Mode Offline)")
    
    df_stock = load_local_stok()
    df_master = load_master_produk()
    
    if df_stock.empty:
        st.warning("Data lokal kosong. Silakan klik tombol 'Tarik Data Server' di sidebar.")
    else:
        df_group = df_stock.groupby(['Part Number', 'Nama Produk', 'Lot Number']).size().reset_index(name='Jumlah Pallet')
        rekap_list = []
        total_all_pallet = 0
        total_all_pcs = 0
        
        for _, row in df_group.iterrows():
            pnc = str(row['Part Number']).strip().upper()
            jml_pallet = int(row['Jumlah Pallet'])
            total_all_pallet += jml_pallet
            
            ip = 0
            if not df_master.empty and 'Part Number Clean' in df_master.columns and pnc in df_master['Part Number Clean'].values:
                mr = df_master[df_master['Part Number Clean'] == pnc].iloc[0]
                ip = pd.to_numeric(mr.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                ip = 0 if pd.isna(ip) else float(ip)
                
            total_pcs = jml_pallet * ip
            total_all_pcs += total_pcs
            
            rekap_list.append({
                'Part Number': pnc, 'Nama Produk': row['Nama Produk'], 'No. Lot': str(row['Lot Number']).strip(),
                'Total Pallet': jml_pallet, 'Isi per Pallet (Pcs)': ip if ip > 0 else "Master Data Kosong", 'Total Pcs': total_pcs
            })
            
        df_final_rekap = pd.DataFrame(rekap_list)
        
        c1, c2 = st.columns(2)
        c1.metric("Total Pallet Tersimpan", f"{total_all_pallet:,.0f} Pallet")
        c2.metric("Estimasi Total Konversi", f"{total_all_pcs:,.0f} Pcs")
        
        st.divider()
        st.dataframe(df_final_rekap, use_container_width=True, hide_index=True)
        
        csv_data = df_final_rekap.to_csv(index=False, sep=";").encode('utf-8')
        st.download_button(label="📥 Unduh Laporan Stok (CSV)", data=csv_data, file_name=f"Rekap_Stok_{get_last_sync_time()[:10]}.csv", mime="text/csv")

# HALAMAN 2: LOG MUTASI (BACA LOKAL)
elif menu == "📖 Log Mutasi & Transaksi":
    st.title("📖 Riwayat Mutasi (Mode Offline)")
    
    df_log = load_local_log()
    
    if df_log.empty:
        st.warning("Data log lokal kosong. Silakan klik tombol 'Tarik Data Server' di sidebar.")
    else:
        col_f1, col_f2 = st.columns(2)
        with col_f1: filter_jenis = st.selectbox("Filter Transaksi:", ["Semua"] + sorted(df_log['Jenis Transaksi'].astype(str).unique().tolist()))
        with col_f2: filter_lot = st.selectbox("Filter No Lot:", ["Semua"] + sorted(df_log['No Lot'].astype(str).unique().tolist()))
            
        df_tampil = df_log.copy()
        if filter_jenis != "Semua": df_tampil = df_tampil[df_tampil['Jenis Transaksi'] == filter_jenis]
        if filter_lot != "Semua": df_tampil = df_tampil[df_tampil['No Lot'] == filter_lot]
            
        st.divider()
        kolom_tersedia = [c for c in ['Waktu Transaksi', 'Jenis Transaksi', 'Lokasi Rak', 'Nama Produk', 'No Lot', 'Mutasi Pallet', 'Keterangan'] if c in df_tampil.columns]
        st.dataframe(df_tampil[kolom_tersedia], use_container_width=True, hide_index=True)
        
        csv_log = df_tampil[kolom_tersedia].to_csv(index=False, sep=";").encode('utf-8')
        st.download_button(label="📥 Unduh Log Transaksi (CSV)", data=csv_log, file_name=f"Log_Mutasi_{get_last_sync_time()[:10]}.csv", mime="text/csv")
