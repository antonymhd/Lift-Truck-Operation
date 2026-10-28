import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="LTO - Outbound", page_icon="📤", layout="centered")

st.markdown('''
<style>
    div.stButton > button { min-height: 60px !important; font-size: clamp(14px, 2vw, 16px) !important; font-weight: bold !important; border-radius: 8px !important; }
    .btn-kurang > div > button { background-color: #ffedd5 !important; border: 2px solid #f97316 !important; color: #9a3412 !important; }
    .btn-batal > div > button { background-color: #f1f5f9 !important; border: 2px solid #cbd5e1 !important; color: #475569 !important; }
</style>
''', unsafe_allow_html=True)

if 'step' not in st.session_state:
    st.session_state.update({'step': 1, 'kolom': 'A', 'baris': 1, 'qty': 1})

def reset_state():
    st.session_state.update({'step': 1, 'qty': 1})

NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        cred = credentials.Certificate(dict(st.secrets["firebase"]))
        firebase_admin.initialize_app(cred)
    return firestore.client()
db = init_firebase()

@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";")
        except: df = pd.read_csv("Master_Produk.csv", sep=",")
        df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except: return pd.DataFrame()

@firestore.transactional
def tx_tarik_outbound_audit(transaction, doc_ref, b_pilih, out_lot, qty_keluar, is_aktual, qty_penyesuaian, status_sisa, log_data, log_audit):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    lot_matches = sorted([d for d in baris_actual if str(d.get('Lot Number', '')).strip() == str(out_lot).strip()], key=lambda x: float(x.get('Pallet Ke', 0)))
    
    # Jika stok di lapangan tidak aktual, gunakan qty penyesuaian dari operator
    effective_qty_to_remove = qty_penyesuaian if not is_aktual else qty_keluar
    
    if len(lot_matches) < effective_qty_to_remove: 
        return False, f"Stok di sistem tidak cukup untuk jumlah penyesuaian tersebut (Sistem: {len(lot_matches)} pallet)."
    
    # Ambil pallet yang akan dikeluarkan
    pallets_to_remove = lot_matches[:effective_qty_to_remove]
    baris_remaining = [d for d in baris_actual if d not in pallets_to_remove]
    
    transaction.set(doc_ref, {'data': data_luar + baris_remaining})
    db.collection(DB_LOG_MUTASI).add(log_data)
    
    # Jika tidak aktual, catat log audit selisih ke backend
    if not is_aktual:
        selisih = qty_penyesuaian - qty_keluar
        log_audit['Keterangan'] = f"Audit Selisih Lapangan. Sistem mencatat keluar {qty_keluar}, aktual disesuaikan {qty_penyesuaian} (Selisih: {selisih})"
        db.collection(DB_LOG_MUTASI).add(log_audit)
        
    return True, "Barang berhasil ditarik dan disesuaikan!"

# ==========================================================
# UI UTAMA LTO
# ==========================================================
st.markdown("<h2 style='text-align: center;'>📤 LTO - Outbound & Audit Lapangan</h2>", unsafe_allow_html=True)
st.caption(f"<p style='text-align: center;'>Terhubung ke database: <b>{NAMA_DATABASE}</b></p>", unsafe_allow_html=True)

progress_val = min(st.session_state.step / 2.0, 1.0)
st.progress(progress_val, text=f"Langkah {st.session_state.step} dari 2")

if st.session_state.step == 1:
    st.header("1. Input Lokasi Rak Tujuan")
    c1, c2 = st.columns(2)
    with c1: 
        blok_list = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        safe_idx = blok_list.index(st.session_state.kolom) if st.session_state.kolom in blok_list else 0
        kolom = st.selectbox("Blok", blok_list, index=safe_idx)
    with c2: 
        baris = st.number_input("Nomor Rak", min_value=1, max_value=28, value=st.session_state.baris)
        
    st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
    if st.button("CARI STOK DI RAK INI ➡️", use_container_width=True): 
        st.session_state.update({'kolom': kolom, 'baris': baris, 'step': 2})
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.step == 2:
    st.header(f"2. Pilih Item, Lot & Audit Fisik ➔ {st.session_state.kolom}-{st.session_state.baris}")
    
    doc = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}").get()
    all_k_data = doc.to_dict().get('data', []) if doc.exists else []
    baris_data = [d for d in all_k_data if float(d.get('Baris', 0)) == float(st.session_state.baris) and str(d.get('Part Number', '')).strip() != '']
    
    lot_counts = {}
    for d in baris_data:
        l = str(d.get('Lot Number', '-')).strip()
        p = str(d.get('Nama Produk', '-')).strip()
        lot_counts[(l, p)] = lot_counts.get((l, p), 0) + 1
        
    if not lot_counts: 
        st.warning("⚠️ Rak ini KOSONG secara sistem! Silakan kembali dan pastikan lokasi benar.")
        if st.button("🔙 Kembali", use_container_width=True):
            st.session_state.step = 1; st.rerun()
    else:
        opsi_lot = [f"Lot: {l} | {p} (Sistem: {c} Pallet)" for (l, p), c in lot_counts.items()]
        pilihan_lot_teks = st.selectbox("Pilih Item & Lot yang akan ditarik:", opsi_lot)
        
        # Ekstrak data lot terpilih
        lot_terpilih = pilihan_lot_teks.split(" | ")[0].replace("Lot: ", "")
        max_stok_sistem = int(pilihan_lot_teks.split("Sistem: ")[1].split(" Pallet")[0])
        
        st.divider()
        st.write("Jumlah Rencana Pengeluaran (Delivery):")
        n1, n2, n3 = st.columns([1, 1.5, 1])
        with n1:
            if st.button("➖", use_container_width=True) and st.session_state.qty > 1: st.session_state.qty -= 1; st.rerun()
        with n2: st.markdown(f"<h2 style='text-align: center; margin:0;'>{st.session_state.qty} Pallet</h2>", unsafe_allow_html=True)
        with n3:
            if st.button("➕", use_container_width=True): st.session_state.qty += 1; st.rerun()
            
        st.divider()
        st.subheader("🔍 Audit Kecocokan Stok Lapangan")
        is_aktual = st.checkbox("✅ Stok fisik di lapangan sesuai dengan data sistem", value=True)
        
        qty_penyesuaian = st.session_state.qty
        if not is_aktual:
            st.warning("⚠️ Karena stok tidak aktual, masukkan jumlah riil yang ditarik/disesuaikan dari lapangan:")
            qty_penyesuaian = st.number_input("Jumlah Riil Aktual yang Ditarik:", min_value=1, value=st.session_state.qty)
            
        st.divider()
        status_input = st.radio("Status Sisa di Rak:", ["Semua Muat ke Truk", "Sisa dikembalikan ke rak ini", "Sisa ditaruh di luar rak"], horizontal=True)
        
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("🚀 EKSEKUSI OUTBOUND & AUDIT", use_container_width=True):
            with st.spinner("Memproses pemotongan stok & pencatatan backend..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}")
                ts = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                
                log_data = {
                    "Waktu Transaksi": ts, 
                    "Jenis Transaksi": "OUTBOUND - Delivery", 
                    "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", 
                    "No Lot": lot_terpilih, 
                    "Mutasi Pallet": -float(qty_penyesuaian), 
                    "Keterangan": f"{status_input} | Aktual: {is_aktual}", 
                    "Kolom": st.session_state.kolom, 
                    "Timestamp_System": firestore.SERVER_TIMESTAMP
                }
                
                log_audit = {
                    "Waktu Transaksi": ts, 
                    "Jenis Transaksi": "AUDIT SELISIH (OUTBOUND)", 
                    "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", 
                    "No Lot": lot_terpilih, 
                    "Mutasi Pallet": 0.0, 
                    "Keterangan": "", 
                    "Timestamp_System": firestore.SERVER_TIMESTAMP
                }
                
                sukses, pesan = tx_tarik_outbound_audit(
                    db.transaction(), doc_ref, st.session_state.baris, 
                    lot_terpilih, st.session_state.qty, is_aktual, qty_penyesuaian, 
                    status_input, log_data, log_audit
                )
                
                if sukses: 
                    st.toast('Outbound Berhasil & Tercatat di Backend!', icon='✅')
                    time.sleep(1.5)
                    reset_state()
                    st.rerun()
                else: 
                    st.error(pesan)
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.write("")
        st.button("🔙 Batal / Ganti Rak", on_click=lambda: st.session_state.update({'step': 1}), use_container_width=True)
