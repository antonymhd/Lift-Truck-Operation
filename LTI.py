import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="LTI - Inbound", page_icon="📥", layout="centered")

st.markdown('''
<style>
    div.stButton > button { min-height: 60px !important; font-size: clamp(14px, 2vw, 16px) !important; font-weight: bold !important; border-radius: 8px !important; }
    .btn-tambah > div > button { background-color: #dcfce7 !important; border: 2px solid #22c55e !important; color: #166534 !important; }
    .btn-batal > div > button { background-color: #f1f5f9 !important; border: 2px solid #cbd5e1 !important; color: #475569 !important; }
</style>
''', unsafe_allow_html=True)

if 'step' not in st.session_state:
    st.session_state.update({
        'step': 1, 'produk': '', 'part_num': '', 'lot': '', 
        'pallets': 1, 'box_sak': 0, 'eceran': 0, 
        'mode_pilih': 'Rekomendasi Rak Otomatis', 'arah': 'Belakang', 'kolom': 'A', 'baris': 1
    })

def reset_state():
    st.session_state.update({'step': 1, 'produk': '', 'part_num': '', 'lot': '', 'pallets': 1, 'box_sak': 0, 'eceran': 0})

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
    
@st.cache_data(ttl=30)
def get_all_rack_data():
    docs = db.collection(NAMA_DATABASE).stream()
    semua_data = []
    for doc in docs:
        d = doc.to_dict()
        if 'data' in d: semua_data.extend(d['data'])
    return pd.DataFrame(semua_data) if semua_data else pd.DataFrame()

def get_recommendations(df, part_num, qty_pallets):
    rekomendasi = []
    kolom_list = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
    baris_list = list(range(1, 29))
    is_all_box = str(part_num).endswith(('D', 'G'))
    
    for k in kolom_list:
        for b in baris_list:
            if k == 'F' and b < 5: continue
            if k == 'G' and b < 17: continue
            
            if df.empty or 'Kolom' not in df.columns:
                rekomendasi.append({'kolom': k, 'baris': b, 'skor': 2, 'alasan': "Rak Kosong Bersih", 'sisa': 31})
                continue
                
            sub = df[(df['Kolom'] == k) & (df['Baris'] == b)]
            if sub.empty:
                rekomendasi.append({'kolom': k, 'baris': b, 'skor': 2, 'alasan': "Rak Kosong Bersih", 'sisa': 32 if is_all_box and b<=16 else 31})
                continue
                
            valid_items = sub[~sub['Part Number'].astype(str).str.strip().isin(['', 'nan', 'None'])]
            filled = len(valid_items)
            max_slot = 32 if (is_all_box and b <= 16) else (46 if is_all_box else (31 if b <= 16 else 44))
            sisa = max(0, max_slot - filled)
            
            if sisa >= qty_pallets:
                sku_list = valid_items['Part Number'].astype(str).str.strip().unique()
                if len(sku_list) == 1 and sku_list[0] == part_num:
                    rekomendasi.append({'kolom': k, 'baris': b, 'skor': 3, 'alasan': f"Rak Ideal (Sejenis). Sisa {sisa} slot.", 'sisa': sisa})
                elif len(sku_list) == 0:
                    rekomendasi.append({'kolom': k, 'baris': b, 'skor': 2, 'alasan': f"Rak Kosong. Sisa {sisa} slot.", 'sisa': sisa})
                
    rekomendasi = sorted(rekomendasi, key=lambda x: x['skor'], reverse=True)
    return rekomendasi[:3]

@firestore.transactional
def tx_simpan_inbound(transaction, doc_ref, b_pilih, arah, in_produk, in_part, in_lot, pallets, box_sak, eceran, log_data):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    is_all_box = in_part.endswith(('D', 'G'))
    for d in baris_actual:
        if not str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')):
            is_all_box = False; break
            
    max_slot = 32 if (is_all_box and b_pilih <= 16) else (46 if is_all_box else (31 if b_pilih <= 16 else 44))
    
    total_inbound_slot = pallets + (1 if (box_sak > 0 or eceran > 0) else 0)
    
    new_records = []
    satuan_str = "Dus" if in_part.endswith('D') else "Sak"
    ket_str = f"Pecahan: {box_sak} {satuan_str}, Eceran: {eceran} Pcs" if (box_sak > 0 or eceran > 0) else ""

    if arah == "Depan":
        for d in baris_actual: d['Pallet Ke'] = float(d['Pallet Ke']) + total_inbound_slot
        for i in range(1, total_inbound_slot + 1):
            catatan = 'BAYANGAN' if (len(baris_actual) + i > max_slot) else ''
            new_records.append({'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(i), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk, 'Status': 'Terisi', 'Keterangan': ket_str, 'Catatan': catatan})
    else:
        filled_pallets = [int(float(d.get('Pallet Ke', 0))) for d in baris_actual if pd.notna(d.get('Pallet Ke'))]
        next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
        for _ in range(total_inbound_slot):
            while next_pallet in filled_pallets: next_pallet += 1
            catatan = 'BAYANGAN' if (len(baris_actual) + len(new_records) + 1 > max_slot) else ''
            new_records.append({'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk, 'Status': 'Terisi', 'Keterangan': ket_str, 'Catatan': catatan})
            filled_pallets.append(next_pallet); next_pallet += 1
            
    transaction.set(doc_ref, {'data': data_luar + baris_actual + new_records})
    db.collection(DB_LOG_MUTASI).add(log_data)
    return True, "Sukses disimpan!"

# ==========================================================
# UI UTAMA LTI
# ==========================================================
st.markdown("<h2 style='text-align: center;'>📥 LTI - Putaway (Inbound)</h2>", unsafe_allow_html=True)
st.caption(f"<p style='text-align: center;'>Terhubung ke database: <b>{NAMA_DATABASE}</b></p>", unsafe_allow_html=True)

progress_val = min(st.session_state.step / 2.0, 1.0)
st.progress(progress_val, text=f"Langkah {st.session_state.step} dari 2")

if st.session_state.step == 1:
    st.header("1. Input Data Produksi & Kuantitas")
    df_master = load_master_produk()
    pilihan = st.selectbox("Pilih Produk:", ["-- Pilih Produk --"] + sorted(df_master['Nama Barang'].dropna().unique().tolist()) if not df_master.empty else ["-- Pilih Produk --"], key='sel_prod')
    lot_input = st.text_input("No. Lot (Opsional):")
    
    st.divider()
    c_p1, c_p2, c_p3 = st.columns(3)
    with c_p1:
        st.write("**Pallet Utuh**")
        p_val = st.number_input("Pallet", min_value=0, value=st.session_state.pallets, key='num_pal')
    with c_p2:
        st.write("**Box / Sak Pecahan**")
        b_val = st.number_input("Box/Sak", min_value=0, value=st.session_state.box_sak, key='num_box')
    with c_p3:
        st.write("**Eceran (Pcs)**")
        e_val = st.number_input("Pcs", min_value=0, value=st.session_state.eceran, key='num_pcs')
        
    st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
    if st.button("LANJUT KE PENEMPATAN ➡️", use_container_width=True):
        if pilihan == "-- Pilih Produk --": 
            st.error("Pilih produk terlebih dahulu!")
        elif p_val == 0 and b_val == 0 and e_val == 0:
            st.error("Masukkan jumlah kuantitas barang (Pallet / Box / Eceran)!")
        else:
            st.session_state.update({
                'produk': pilihan,
                'part_num': df_master[df_master['Nama Barang'] == pilihan]['Part Number'].values[0],
                'lot': lot_input,
                'pallets': p_val, 'box_sak': b_val, 'eceran': e_val,
                'step': 2
            })
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.step == 2:
    st.header("2. Metode Penempatan Rak")
    
    st.session_state.mode_pilih = st.radio("Pilih Cara Penentuan Rak:", ["Rekomendasi Rak Otomatis", "Cari Rak Manual (Bebas)"], horizontal=True)
    
    kolom, baris, arah = 'A', 1, 'Belakang'
    
    if st.session_state.mode_pilih == "Rekomendasi Rak Otomatis":
        df_rak = get_all_rack_data()
        simulasi_qty = st.session_state.pallets + (1 if (st.session_state.box_sak > 0 or st.session_state.eceran > 0) else 0)
        rek_list = get_recommendations(df_rak, st.session_state.part_num, max(1, simulasi_qty))
        
        if rek_list:
            top_rek = rek_list[0]
            st.success(f"💡 Rekomendasi Utama: **Blok {top_rek['kolom']} - Rak {top_rek['baris']}**")
            st.caption(f"Alasan sistem: {top_rek['alasan']}")
            kolom, baris = top_rek['kolom'], top_rek['baris']
        else:
            st.warning("⚠️ Gudang padat. Silakan gunakan mode manual untuk memilih rak.")
    else:
        st.info("✍️ Silakan tentukan lokasi rak tujuan secara manual di bawah ini:")
        c1, c2, c3 = st.columns(3)
        with c1: 
            blok_list = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
            safe_idx = blok_list.index(st.session_state.kolom) if st.session_state.kolom in blok_list else 0
            kolom = st.selectbox("Blok", blok_list, index=safe_idx)
        with c2: baris = st.number_input("Nomor Rak", min_value=1, max_value=28, value=1)
        with c3: arah = st.radio("Jalur", ["Belakang", "Depan"])

    st.divider()
    st.markdown('<div class="btn-tambah">', unsafe_allow_html=True)
    if st.button("🚀 SIMPAN INBOUND SEKARANG", use_container_width=True):
        with st.spinner("Menyimpan transaksi ke database..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kolom}")
            ts = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
            
            simulasi_qty = st.session_state.pallets + (1 if (st.session_state.box_sak > 0 or st.session_state.eceran > 0) else 0)
            
            log_mutasi_data = {
                "Waktu Transaksi": ts, 
                "Jenis Transaksi": "INBOUND - Hasil Produksi", 
                "Lokasi Rak": f"{kolom}-{baris}", 
                "Nama Produk": st.session_state.produk, 
                "Part Number": st.session_state.part_num, 
                "No Lot": st.session_state.lot if st.session_state.lot else "-", 
                "Mutasi Pallet": float(st.session_state.pallets), 
                "Mutasi Pecahan (Dus/Sak)": float(st.session_state.box_sak), 
                "Mutasi Eceran (Pcs)": float(st.session_state.eceran), 
                "Keterangan": f"Mode: {st.session_state.mode_pilih}", 
                "Kolom": kolom, 
                "Timestamp_System": firestore.SERVER_TIMESTAMP
            }
            
            sukses, pesan = tx_simpan_inbound(
                db.transaction(), doc_ref, baris, arah, 
                st.session_state.produk, st.session_state.part_num, st.session_state.lot, 
                st.session_state.pallets, st.session_state.box_sak, st.session_state.eceran, 
                log_mutasi_data
            )
            
            if sukses: 
                st.toast('Inbound Berhasil Disimpan!', icon='✅')
                time.sleep(1.5)
                reset_state()
                st.rerun()
            else: 
                st.error(pesan)
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('<div class="btn-batal">', unsafe_allow_html=True)
    if st.button("🔙 Kembali ke Langkah 1", use_container_width=True):
        st.session_state.step = 1
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
