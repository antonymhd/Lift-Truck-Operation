import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="LTO - Outbound", page_icon="📤", layout="centered")

st.markdown("""
<style>
    div.stButton > button { min-height: 80px !important; font-size: clamp(14px, 2vw, 20px) !important; font-weight: 900 !important; border-radius: 12px !important; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); white-space: normal !important; line-height: 1.2 !important; }
    .btn-kurang > div > button { background-color: #ffedd5 !important; border: 2px solid #f97316 !important; color: #9a3412 !important; }
    .btn-batal > div > button { background-color: #f1f5f9 !important; border: 2px solid #cbd5e1 !important; color: #475569 !important; }
    .rak-info > div > button { background-color: #f8fafc !important; border-color: #94a3b8 !important; color: #334155 !important; }
</style>
""", unsafe_allow_html=True)

# 1. INISIALISASI STATE
if 'init_done' not in st.session_state:
    st.session_state.update({
        'init_done': True, 'mode': 'HOME', 'jenis_tugas': '', 'kolom': '', 
        'baris': 1, 'lot_pilih': '', 'sisa_aktual': '', 'status_sisa': '', 
        'qty': 1, 'step': 1
    })

def reset_state():
    st.session_state.update({'mode': 'HOME', 'jenis_tugas': '', 'kolom': '', 'baris': 1, 'lot_pilih': '', 'sisa_aktual': '', 'status_sisa': '', 'qty': 1, 'step': 1})

# 2. SAFE BOOT FIREBASE
NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

@st.cache_resource
def init_firebase():
    try:
        if not firebase_admin._apps:
            cred = credentials.Certificate(dict(st.secrets["firebase"]))
            firebase_admin.initialize_app(cred)
        return firestore.client(), "OK"
    except Exception as e:
        return None, str(e)

db, db_status = init_firebase()
if db is None:
    st.error("🚨 Gagal terhubung ke Firebase.")
    st.code(db_status)
    st.stop()

@firestore.transactional
def tx_tarik_outbound(transaction, doc_ref, b_pilih, out_lot, out_qty, sisa_aktual, status_sisa, log_data, log_audit):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    lot_matches = sorted([d for d in baris_actual if str(d.get('Lot Number', '')).strip() == str(out_lot).strip()], key=lambda x: float(x.get('Pallet Ke', 0)))
    if len(lot_matches) < out_qty: return False, f"Stok Lot {out_lot} tidak cukup! Di sistem hanya ada {len(lot_matches)} pallet."
    
    pallets_to_remove = lot_matches[:out_qty]
    baris_remaining = [d for d in baris_actual if d not in pallets_to_remove]
    
    transaction.set(doc_ref, {'data': data_luar + baris_remaining})
    db.collection(DB_LOG_MUTASI).add(log_data)
    
    sistem_sisa = len(lot_matches) - out_qty
    if sisa_aktual != "" and int(sisa_aktual) != sistem_sisa:
        log_audit['Selisih'] = int(sisa_aktual) - sistem_sisa
        db.collection(DB_LOG_MUTASI).add(log_audit)
        
    return True, "Barang berhasil ditarik!"

# 3. UI LTO
if st.session_state.mode == 'HOME':
    st.markdown("<h2 style='text-align: center;'>📤 LTO (Lift Truck Outbound)</h2>", unsafe_allow_html=True)
    st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
    if st.button("📤 AMBIL BARANG DELIVERY", use_container_width=True):
        st.session_state.update({'mode': 'OUTBOUND', 'jenis_tugas': 'OUTBOUND - Delivery', 'step': 1}); st.rerun()
    st.write("")
    if st.button("📤 AMBIL WIP / BAHAN", use_container_width=True):
        st.session_state.update({'mode': 'OUTBOUND', 'jenis_tugas': 'OUTBOUND - Ambil WIP', 'step': 1}); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.mode == 'OUTBOUND':
    st.progress(st.session_state.step / 4, text=f"{st.session_state.jenis_tugas} (Langkah {st.session_state.step}/4)")
    
    if st.session_state.step == 1:
        st.header("1. Posisi Rak Asal")
        c1, c2 = st.columns([1, 2])
        with c1: kolom = st.selectbox("Blok", ['A', 'B', 'C', 'D', 'E', 'F', 'G'])
        with c2: baris = st.number_input("Nomor Rak", min_value=1, max_value=28, value=1)
            
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("CARI BARANG", use_container_width=True):
            st.session_state.update({'kolom': kolom, 'baris': baris, 'step': 2}); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Batal", on_click=reset_state, use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 2:
        st.header(f"2. Pilih Lot di Rak {st.session_state.kolom}-{st.session_state.baris}")
        doc = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}").get()
        all_k_data = doc.to_dict().get('data', []) if doc.exists else []
        baris_data = [d for d in all_k_data if float(d.get('Baris', 0)) == float(st.session_state.baris) and str(d.get('Part Number', '')).strip() != '']
        
        lot_counts = {}
        for d in baris_data:
            l = str(d.get('Lot Number', '-')).strip(); p = str(d.get('Nama Produk', '-')).strip()
            lot_counts[(l, p)] = lot_counts.get((l, p), 0) + 1
            
        if not lot_counts: st.warning("Rak ini KOSONG di sistem!")
        else:
            st.markdown('<div class="rak-info">', unsafe_allow_html=True)
            for (lot, prod), count in lot_counts.items():
                if st.button(f"Lot: {lot}\n{prod}\n(Ada {count} Pallet)", use_container_width=True):
                    st.session_state.update({'lot_pilih': lot, 'step': 3}); st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 1}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 3:
        st.header("3. Tarik & Audit Fisik")
        st.info(f"Lot Terpilih: {st.session_state.lot_pilih}")
        n1, n2, n3 = st.columns([1, 1.5, 1])
        with n1:
            if st.button("➖", use_container_width=True) and st.session_state.qty > 1: st.session_state.qty -= 1; st.rerun()
        with n2: st.markdown(f"<h1 style='text-align: center; font-size: 50px; margin:0;'>{st.session_state.qty}</h1>", unsafe_allow_html=True)
        with n3:
            if st.button("➕", use_container_width=True): st.session_state.qty += 1; st.rerun()
            
        st.divider()
        sisa_input = st.text_input("📝 (Opsional) Sisa Aktual Fisik Lot ini di Rak:", value=st.session_state.sisa_aktual)
        
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("Lanjut ➡️", use_container_width=True): st.session_state.update({'sisa_aktual': sisa_input, 'step': 4}); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 2}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 4:
        st.header("4. Status Barang")
        status_input = st.radio("Bagaimana status sisa tarikan ini?", ["Semua Muat ke Truk", "Sisa dikembalikan ke rak ini", "Sisa ditaruh di luar rak"])
        
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("🚀 EKSEKUSI OUTBOUND", use_container_width=True):
            st.session_state.status_sisa = status_input
            with st.spinner("Memotong stok..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}")
                ts = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                log_data = {"Waktu Transaksi": ts, "Jenis Transaksi": st.session_state.jenis_tugas, "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "No Lot": st.session_state.lot_pilih, "Mutasi Pallet": -float(st.session_state.qty), "Keterangan": st.session_state.status_sisa, "Kolom": st.session_state.kolom, "Timestamp_System": firestore.SERVER_TIMESTAMP}
                log_audit = {"Waktu Transaksi": ts, "Jenis Transaksi": "AUDIT SELISIH (OUTBOUND)", "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "No Lot": st.session_state.lot_pilih, "Mutasi Pallet": 0.0, "Keterangan": "Terjadi selisih pelaporan fisik.", "Timestamp_System": firestore.SERVER_TIMESTAMP}
                
                sukses, pesan = tx_tarik_outbound(db.transaction(), doc_ref, st.session_state.baris, st.session_state.lot_pilih, st.session_state.qty, st.session_state.sisa_aktual, st.session_state.status_sisa, log_data, log_audit)
                
                if sukses: st.toast('Tarik Stok Berhasil!', icon='✅'); time.sleep(1.5); reset_state(); st.rerun()
                else: st.error(pesan)
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 3}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)
