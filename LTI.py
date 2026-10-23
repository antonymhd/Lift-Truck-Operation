import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="LTI - Inbound", page_icon="📥", layout="centered")

st.markdown("""
<style>
    div.stButton > button { min-height: 80px !important; font-size: clamp(14px, 2vw, 20px) !important; font-weight: 900 !important; border-radius: 12px !important; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); white-space: normal !important; line-height: 1.2 !important; }
    .btn-tambah > div > button { background-color: #dcfce7 !important; border: 2px solid #22c55e !important; color: #166534 !important; }
    .btn-kurang > div > button { background-color: #ffedd5 !important; border: 2px solid #f97316 !important; color: #9a3412 !important; }
    .btn-batal > div > button { background-color: #f1f5f9 !important; border: 2px solid #cbd5e1 !important; color: #475569 !important; }
    .btn-pindah > div > button { background-color: #e0f2fe !important; border: 2px solid #0ea5e9 !important; color: #075985 !important; }
    .rak-info > div > button { background-color: #f8fafc !important; border-color: #94a3b8 !important; color: #334155 !important; }
    .rak-hijau > div > button { background-color: #dcfce7 !important; border-color: #22c55e !important; color: #166534 !important; }
    .rak-kuning > div > button { background-color: #fef08a !important; border-color: #eab308 !important; color: #854d0e !important; }
    .rak-merah > div > button { background-color: #fee2e2 !important; border-color: #ef4444 !important; color: #991b1b !important; }
</style>
""", unsafe_allow_html=True)

# 1. INISIALISASI STATE
if 'init_done' not in st.session_state:
    st.session_state.update({
        'init_done': True, 'mode': 'HOME', 'jenis_tugas': '', 'arah': '', 'kolom': '', 
        'baris': 1, 'produk': '', 'part_num': '', 'lot': '', 'lot_pilih': '', 
        'sisa_aktual': '', 'status_sisa': '', 'qty': 1, 'step': 1
    })

def reset_state():
    st.session_state.update({'mode': 'HOME', 'jenis_tugas': '', 'arah': '', 'kolom': '', 'baris': 1, 'produk': '', 'part_num': '', 'lot': '', 'lot_pilih': '', 'sisa_aktual': '', 'status_sisa': '', 'qty': 1, 'step': 1})

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
    return True, "Sukses disimpan!"

@firestore.transactional
def tx_tarik_outbound(transaction, doc_ref, b_pilih, out_lot, out_qty, sisa_aktual, status_sisa, log_data, log_audit):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    lot_matches = sorted([d for d in baris_actual if str(d.get('Lot Number', '')).strip() == str(out_lot).strip()], key=lambda x: float(x.get('Pallet Ke', 0)))
    if len(lot_matches) < out_qty: return False, f"Stok Lot {out_lot} tidak cukup! Hanya ada {len(lot_matches)} pallet."
    
    pallets_to_remove = lot_matches[:out_qty]
    baris_remaining = [d for d in baris_actual if d not in pallets_to_remove]
    
    transaction.set(doc_ref, {'data': data_luar + baris_remaining})
    db.collection(DB_LOG_MUTASI).add(log_data)
    
    sistem_sisa = len(lot_matches) - out_qty
    if sisa_aktual != "" and int(sisa_aktual) != sistem_sisa:
        log_audit['Selisih'] = int(sisa_aktual) - sistem_sisa
        db.collection(DB_LOG_MUTASI).add(log_audit)
        
    return True, "Barang berhasil ditarik!"

# 3. UI LTI HOME
if st.session_state.mode == 'HOME':
    st.markdown("<h2 style='text-align: center;'>📥 LTI (Lift Truck Inbound)</h2>", unsafe_allow_html=True)
    st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
    if st.button("📥 TUMPUK HASIL PRODUKSI", use_container_width=True):
        st.session_state.update({'mode': 'TAMBAH', 'jenis_tugas': 'INBOUND - Hasil Produksi', 'step': 1}); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
    if st.button("📤 AMBIL WIP / BAHAN", use_container_width=True):
        st.session_state.update({'mode': 'KURANG', 'jenis_tugas': 'OUTBOUND - Ambil WIP', 'step': 1}); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    st.markdown('<div class="btn-pindah">', unsafe_allow_html=True)
    if st.button("🔄 PINDAH LOKASI RAK", use_container_width=True):
        st.toast("Modul Pindah Rak disiapkan di update berikutnya!", icon="🛠️")
    st.markdown('</div>', unsafe_allow_html=True)

# 4. WIZARD TAMBAH (INBOUND)
elif st.session_state.mode == 'TAMBAH':
    st.progress(st.session_state.step / 5, text=f"{st.session_state.jenis_tugas} (Langkah {st.session_state.step}/5)")
    
    if st.session_state.step == 1:
        st.header("1. Masuk Lewat Mana?")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⬆️\nDEPAN", use_container_width=True): st.session_state.update({'arah': 'Depan', 'step': 2}); st.rerun()
        with c2:
            if st.button("⬇️\nBELAKANG", use_container_width=True): st.session_state.update({'arah': 'Belakang', 'step': 2}); st.rerun()
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Batal", on_click=reset_state, use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 2:
        st.header(f"2. Pilih Blok ({st.session_state.arah})")
        cols = st.columns(4)
        for i, blk in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G']):
            with cols[i % 4]:
                if st.button(f"BLOK {blk}", key=f"blk_{blk}", use_container_width=True): st.session_state.update({'kolom': blk, 'step': 3}); st.rerun()
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 1}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

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
                    if st.button(f"Rak {i}\n({isi_rak})" if isi_rak > 0 else f"Rak {i}\n(KOSONG)", key=f"rk_{i}", use_container_width=True): st.session_state.update({'baris': i, 'step': 4}); st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 2}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.step == 4:
        st.header("4. Info Barang")
        df_master = load_master_produk()
        if not df_master.empty:
            pilihan = st.selectbox("Pilih Produk:", ["-- Pilih Produk --"] + sorted(df_master['Nama Barang'].dropna().unique().tolist()))
            lot_input = st.text_input("No. Lot (Opsional):", value=st.session_state.lot)
            if pilihan != "-- Pilih Produk --":
                st.session_state.produk = pilihan; st.session_state.part_num = df_master[df_master['Nama Barang'] == pilihan]['Part Number'].values[0]; st.session_state.lot = lot_input
                st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
                if st.button("➡️ Lanjut ke Jumlah", use_container_width=True): st.session_state.update({'step': 5}); st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 3}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

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
                ts = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                log_mutasi_data = {"Waktu Transaksi": ts, "Jenis Transaksi": st.session_state.jenis_tugas, "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}", "Nama Produk": st.session_state.produk, "Part Number": st.session_state.part_num, "No Lot": st.session_state.lot if st.session_state.lot else "-", "Mutasi Pallet": float(st.session_state.qty), "Mutasi Pecahan (Dus/Sak)": 0.0, "Mutasi Eceran (Pcs)": 0.0, "Keterangan": "", "Kolom": st.session_state.kolom, "Timestamp_System": firestore.SERVER_TIMESTAMP}
                sukses, pesan = tx_simpan_inbound(db.transaction(), doc_ref, st.session_state.baris, st.session_state.arah, st.session_state.produk, st.session_state.part_num, st.session_state.lot, st.session_state.qty, log_mutasi_data)
                if sukses: st.toast('Tersimpan!', icon='✅'); time.sleep(1.5); reset_state(); st.rerun()
                else: st.error(pesan)
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="btn-batal">', unsafe_allow_html=True); st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update({'step': 4}), use_container_width=True); st.markdown('</div>', unsafe_allow_html=True)

# 5. WIZARD KURANG (OUTBOUND)
elif st.session_state.mode == 'KURANG':
    st.progress(st.session_state.step / 4, text=f"{st.session_state.jenis_tugas} (Langkah {st.session_state.step}/4)")
    
    if st.session_state.step == 1:
        st.header("1. Posisi Rak Asal")
        c1, c2 = st.columns([1, 2])
        with c1: kolom = st.selectbox("Blok", ['A', 'B', 'C', 'D', 'E', 'F', 'G'])
        with c2: baris = st.number_input("Nomor Rak", min_value=1, max_value=28, value=1)
            
        st.markdown('<div class="btn-kurang">', unsafe_allow_html=True)
        if st.button("CARI BARANG", use_container_width=True): st.session_state.update({'kolom': kolom, 'baris': baris, 'step': 2}); st.rerun()
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
