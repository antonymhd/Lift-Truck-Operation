import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

# ==========================================================
# 1. KONFIGURASI LTI
# ==========================================================
st.set_page_config(page_title="LTI - Inbound", page_icon="📥", layout="centered")

st.markdown("""
<style>
    div.stButton > button { min-height: 80px !important; font-size: clamp(14px, 2vw, 20px) !important; font-weight: 900 !important; border-radius: 12px !important; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); white-space: normal !important; line-height: 1.2 !important; }
    .btn-tambah > div > button { background-color: #dcfce7 !important; border: 2px solid #22c55e !important; color: #166534 !important; }
    .btn-pindah > div > button { background-color: #e0f2fe !important; border: 2px solid #0ea5e9 !important; color: #075985 !important; }
    .btn-batal > div > button { background-color: #f1f5f9 !important; border: 2px solid #cbd5e1 !important; color: #475569 !important; }
    .rak-hijau > div > button { background-color: #dcfce7 !important; border-color: #22c55e !important; color: #166534 !important; }
    .rak-kuning > div > button { background-color: #fef08a !important; border-color: #eab308 !important; color: #854d0e !important; }
    .rak-merah > div > button { background-color: #fee2e2 !important; border-color: #ef4444 !important; color: #991b1b !important; }
</style>
""", unsafe_allow_html=True)

NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";")
        except: df = pd.read_csv("Master_Produk.csv", sep=",")
        df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception: return pd.DataFrame()

@firestore.transactional
def tx_simpan_inbound(transaction, doc_ref, b_pilih, arah, in_produk, in_part, in_lot, in_qty, log_data):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    is_all_box = in_part.endswith(('D', 'G'))
    for d in baris_actual:
        if not str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')):
            is_all_box = False; break
            
    max_slot = 32 if (is_all_box and b_pilih <= 16) else (46 if is_all_box else (31 if b_pilih <= 16 else 44))
    if len(baris_actual) + in_qty > max_slot: return False, f"Rak penuh! Sisa {max_slot - len(baris_actual)} slot."
        
    if arah == "Depan":
        for d in baris_actual: d['Pallet Ke'] = float(d['Pallet Ke']) + in_qty
        new_records = [{'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(i), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk, 'Status': 'Terisi', 'Keterangan': '', 'Catatan': ''} for i in range(1, in_qty + 1)]
    else:
        filled_pallets = [int(float(d.get('Pallet Ke', 0))) for d in baris_actual if pd.notna(d.get('Pallet Ke'))]
        next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
        new_records = []
        for _ in range(in_qty):
            while next_pallet in filled_pallets: next_pallet += 1
            new_records.append({'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk, 'Status': 'Terisi', 'Keterangan': '', 'Catatan': ''})
            filled_pallets.append(next_pallet); next_pallet += 1
            
    transaction.set(doc_ref, {'data': data_luar + baris_actual + new_records})
    db.collection(DB_LOG_MUTASI).add(log_data)
    return True, "Sukses!"

# MANAJEMEN STATE
for key in ['mode', 'jenis_tugas', 'arah', 'kolom', 'produk', 'part_num', 'lot']:
    if key not in st.session_state: st.session_state[key] = ""
if 'step' not in st.session_state: st.session_state.step = 1
if 'baris' not in st.session_state: st.session_state.baris = 0
if 'qty' not in st.session_state: st.session_state.qty = 1

def reset_state():
    for key in ['mode', 'jenis_tugas', 'arah', 'kolom', 'produk', 'part_num', 'lot']: st.session_state[key] = ""
    st.session_state.step = 1; st.session_state.qty = 1; st.session_state.baris = 0; st.session_state.mode = 'HOME'

# HOME LTI
if st.session_state.mode == 'HOME':
    st.markdown("<h2 style='text-align: center;'>📥 LTI (Lift Truck Inbound)</h2>", unsafe_allow_html=True)
    st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
    if st.button("📥 TUMPUK HASIL PRODUKSI", use_container_width=True):
        st.session_state.mode = 'INBOUND'; st.session_state.jenis_tugas = 'INBOUND - Hasil Produksi'; st.rerun()
    st.write("")
    if st.button("📥 KEMBALIKAN SISA TRUK (Retur)", use_container_width=True):
        st.session_state.mode = 'INBOUND'; st.session_state.jenis_tugas = 'INBOUND - Retur Delivery'; st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    st.write("")
    st.markdown('<div class="btn-pindah">', unsafe_allow_html=True)
    if st.button("🔄 PINDAH LOKASI RAK\n(Modul segera hadir)", use_container_width=True):
        st.toast("Modul Pindah Rak disiapkan di update berikutnya!", icon="🛠️")
    st.markdown('</div>', unsafe_allow_html=True)

# WIZARD INBOUND
elif st.session_state.mode == 'INBOUND':
    st.progress(st.session_state.step / 5, text=f"{st.session_state.jenis_tugas} (Langkah {st.session_state.step}/5)")
    
    if st.session_state.step == 1:
        st.header("1. Masuk Lewat Mana?")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⬆️\nDEPAN", use_container_width=True): st.session_state.arah = "Depan"; st.session_state.step = 2; st.rerun()
        with c2:
            if st.button("⬇️\nBELAKANG", use_container_width=True): st.session_state.arah = "Belakang"; st.session_state.step = 2; st.rerun()
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Batal", on_click=reset_state, use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 2:
        st.header(f"2. Pilih Blok ({st.session_state.arah})")
        cols = st.columns(4)
        for i, blk in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G']):
            with cols[i % 4]:
                if st.button(f"BLOK {blk}", key=f"blk_{blk}", use_container_width=True): st.session_state.kolom = blk; st.session_state.step = 3; st.rerun()
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=1), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 3:
        st.header(f"3. Pilih Rak di Blok {st.session_state.kolom}")
        doc = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}").get()
        all_k_data = doc.to_dict().get('data', []) if doc.exists else []
        p_cols = st.columns(4)
        for i in range(1, 29):
            data_baris = [d for d in all_k_data if float(d.get('Baris', 0)) == float(i) and str(d.get('Part Number', '')).strip() != '']
            isi_rak = len(data_baris); sku_rak = len(set([str(d.get('Part Number', '')).strip().upper() for d in data_baris]))
            css_class = "rak-hijau" if (isi_rak == 0 or sku_rak == 1) else ("rak-kuning" if sku_rak == 2 else "rak-merah")
            with p_cols[(i-1) % 4]:
                st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
                if isi_rak >= 46: st.button(f"🔒 Penuh", key=f"rk_{i}", disabled=True, use_container_width=True)
                else:
                    if st.button(f"Rak {i}\n({isi_rak})" if isi_rak > 0 else f"Rak {i}\n(KOSONG)", key=f"rk_{i}", use_container_width=True): st.session_state.baris = i; st.session_state.step = 4; st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=2), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 4:
        st.header("4. Info Barang")
        df_master = load_master_produk()
        if not df_master.empty:
            pilihan = st.selectbox("Pilih Produk:", ["-- Pilih Produk --"] + sorted(df_master['Nama Barang'].dropna().unique().tolist()))
            st.session_state.lot = st.text_input("No. Lot (Opsional):", placeholder="Ketik No Lot...")
            if pilihan != "-- Pilih Produk --":
                st.session_state.produk = pilihan; st.session_state.part_num = df_master[df_master['Nama Barang'] == pilihan]['Part Number'].values[0]
                st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
                if st.button("➡️ Lanjut ke Jumlah", use_container_width=True): st.session_state.step = 5; st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=3), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 5:
        st.header("5. Jumlah Pallet")
        n1, n2, n3 = st.columns([1, 1.5, 1])
        with n1:
            if st.button("➖", use_container_width=True) and st.session_state.qty > 1: st.session_state.qty -= 1; st.rerun()
        with n2: st.markdown(f"<h1 style='text-align: center; font-size: 50px; margin:0;'>{st.session_state.qty}</h1>", unsafe_allow_html=True)
        with n3:
            if st.button("➕", use_container_width=True): st.session_state.qty += 1; st.rerun()
        st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
        if st.button("🚀 SIMPAN", use_container_width=True):
            with st.spinner("Menyimpan..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}")
                log_mutasi_data = {"Waktu Transaksi": (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S"), "Jenis Transaksi": st.session_state.jenis_tugas, "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "Nama Produk": st.session_state.produk, "Part Number": st.session_state.part_num, "No Lot": st.session_state.lot if st.session_state.lot else "-", "Mutasi Pallet": float(st.session_state.qty), "Mutasi Pecahan (Dus/Sak)": 0.0, "Mutasi Eceran (Pcs)": 0.0, "Keterangan": "", "Kolom": st.session_state.kolom, "Timestamp_System": firestore.SERVER_TIMESTAMP}
                sukses, pesan = tx_simpan_inbound(db.transaction(), doc_ref, st.session_state.baris, st.session_state.arah, st.session_state.produk, st.session_state.part_num, st.session_state.lot, st.session_state.qty, log_mutasi_data)
                if sukses: st.toast('Tersimpan!', icon='✅'); st.balloons(); time.sleep(1.5); reset_state(); st.rerun()
                else: st.error(pesan)
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=4), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)
