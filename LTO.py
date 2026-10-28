import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="LTO - Outbound", page_icon="📤", layout="centered")

st.markdown('''
<style>
    div.stButton > button { min-height: 70px !important; font-size: clamp(14px, 2vw, 18px) !important; font-weight: bold !important; border-radius: 8px !important; }
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

@firestore.transactional
def tx_tarik_outbound(transaction, doc_ref, b_pilih, out_lot, out_qty, sisa_aktual, status_sisa, log_data, log_audit):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    lot_matches = sorted([d for d in baris_actual if str(d.get('Lot Number', '')).strip() == str(out_lot).strip()], key=lambda x: float(x.get('Pallet Ke', 0)))
    if len(lot_matches) < out_qty: return False, f"Stok Lot {out_lot} di sistem hanya ada {len(lot_matches)} pallet. Tarikan ditolak agar tidak minus."
    
    pallets_to_remove = lot_matches[:out_qty]
    baris_remaining = [d for d in baris_actual if d not in pallets_to_remove]
    
    transaction.set(doc_ref, {'data': data_luar + baris_remaining})
    db.collection(DB_LOG_MUTASI).add(log_data)
    
    sistem_sisa = len(lot_matches) - out_qty
    if sisa_aktual != "" and int(sisa_aktual) != sistem_sisa:
        log_audit['Selisih'] = int(sisa_aktual) - sistem_sisa
        db.collection(DB_LOG_MUTASI).add(log_audit)
        
    return True, "Barang berhasil ditarik!"

st.markdown("<h2 style='text-align: center;'>📤 LTO - Ambil Barang (Outbound)</h2>", unsafe_allow_html=True)
st.progress(st.session_state.step / 2, text=f"Langkah {st.session_state.step} dari 2")

if st.session_state.step == 1:
    st.header("1. Titik Jemput Barang")
    c1, c2 = st.columns(2)
    with c1: kolom = st.selectbox("Blok", ['A', 'B', 'C', 'D', 'E', 'F', 'G'], index=['A', 'B', 'C', 'D', 'E', 'F', 'G'].index(st.session_state.kolom))
    with c2: baris = st.number_input("Nomor Rak", min_value=1, max_value=28, value=st.session_state.baris)
        
    st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
    if st.button("CARI STOK DI RAK INI ➡️", use_container_width=True): 
        st.session_state.update({'kolom': kolom, 'baris': baris, 'step': 2})
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.step == 2:
    st.header(f"2. Eksekusi Tarik ➔ {st.session_state.kolom}-{st.session_state.baris}")
    
    doc = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}").get()
    all_k_data = doc.to_dict().get('data', []) if doc.exists else []
    baris_data = [d for d in all_k_data if float(d.get('Baris', 0)) == float(st.session_state.baris) and str(d.get('Part Number', '')).strip() != '']
    
    lot_counts = {}
    for d in baris_data:
        l = str(d.get('Lot Number', '-')).strip()
        p = str(d.get('Nama Produk', '-')).strip()
        lot_counts[(l, p)] = lot_counts.get((l, p), 0) + 1
        
    if not lot_counts: 
        st.warning("⚠️ Rak ini KOSONG secara sistem! Jika di lapangan ada barang, berarti terjadi Inbound tanpa input sistem sebelumnya.")
    else:
        opsi_lot = [f"Lot: {l} | {p} (Tersedia: {c} Pallet)" for (l, p), c in lot_counts.items()]
        pilihan_lot_teks = st.radio("Pilih Lot yang akan ditarik:", opsi_lot)
        lot_terpilih = pilihan_lot_teks.split(" | ")[0].replace("Lot: ", "")
        
        st.divider()
        st.write("Jumlah Tarikan (Pallet):")
        n1, n2, n3 = st.columns([1, 1.5, 1])
        with n1:
            if st.button("➖", use_container_width=True) and st.session_state.qty > 1: st.session_state.qty -= 1; st.rerun()
        with n2: st.markdown(f"<h2 style='text-align: center; margin:0;'>{st.session_state.qty}</h2>", unsafe_allow_html=True)
        with n3:
            if st.button("➕", use_container_width=True): st.session_state.qty += 1; st.rerun()
        
        st.divider()
        status_input = st.radio("Status Sisa:", ["Semua Muat ke Truk", "Sisa dikembalikan ke rak ini", "Sisa ditaruh di luar rak"], horizontal=True)
        sisa_input = st.text_input("📝 Audit Fisik (Opsional - Input sisa fisik pallet di rak):")
        
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("🚀 EKSEKUSI OUTBOUND", use_container_width=True):
            with st.spinner("Memotong stok..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}")
                ts = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                log_data = {"Waktu Transaksi": ts, "Jenis Transaksi": "OUTBOUND - Delivery", "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "No Lot": lot_terpilih, "Mutasi Pallet": -float(st.session_state.qty), "Keterangan": status_input, "Kolom": st.session_state.kolom, "Timestamp_System": firestore.SERVER_TIMESTAMP}
                log_audit = {"Waktu Transaksi": ts, "Jenis Transaksi": "AUDIT SELISIH (OUTBOUND)", "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "No Lot": lot_terpilih, "Mutasi Pallet": 0.0, "Keterangan": "Terjadi selisih pelaporan fisik.", "Timestamp_System": firestore.SERVER_TIMESTAMP}
                
                sukses, pesan = tx_tarik_outbound(db.transaction(), doc_ref, st.session_state.baris, lot_terpilih, st.session_state.qty, sisa_input, status_input, log_data, log_audit)
                
                if sukses: 
                    st.toast('Tarik Stok Berhasil!', icon='✅')
                    time.sleep(1.5)
                    reset_state()
                    st.rerun()
                else: st.error(pesan)
        st.markdown('</div>', unsafe_allow_html=True)

    st.write(""); st.button("🔙 Batal & Kembali", on_click=lambda: st.session_state.update({'step': 1}), use_container_width=True)
