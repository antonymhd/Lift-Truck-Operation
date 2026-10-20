import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import re

# ==========================================================
# 1. KONFIGURASI WEB & UI TABLET
# ==========================================================
st.set_page_config(page_title="WMS Operator - Hokkan Deltapack", page_icon="🚜", layout="centered")

# CSS Khusus Layar Sentuh (Tombol Raksasa & Anti-Bantet)
st.markdown("""
<style>
    div.stButton > button {
        min-height: 80px !important;
        font-size: clamp(16px, 2vw, 24px) !important;
        font-weight: 900 !important;
        border-radius: 12px !important;
        border: 2px solid #cbd5e1 !important;
        transition: all 0.2s;
        white-space: normal !important;
        line-height: 1.2 !important;
    }
    div.stButton > button:active { transform: scale(0.95); }
    .btn-nav > div > button { border-color: #ef4444 !important; color: #ef4444 !important; }
    .btn-action > div > button { background-color: #3b82f6 !important; color: white !important; border:none !important;}
    .btn-success > div > button { background-color: #10b981 !important; color: white !important; border:none !important;}
    
    /* Tombol Puzzle Rak */
    .rak-hijau > div > button { background-color: #dcfce7 !important; border-color: #22c55e !important; color: #166534 !important; }
    .rak-kuning > div > button { background-color: #fef08a !important; border-color: #eab308 !important; color: #854d0e !important; }
    .rak-merah > div > button { background-color: #fee2e2 !important; border-color: #ef4444 !important; color: #991b1b !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================================
# 2. INIT FIREBASE
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

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
    except Exception:
        return pd.DataFrame()

# ==========================================================
# 3. TRANSAKSI FIRESTORE (ANTI-TABRAKAN DATA)
# ==========================================================
@firestore.transactional
def tx_simpan_inbound(transaction, doc_ref, b_pilih, arah, in_produk, in_part, in_lot, in_qty, log_data):
    snapshot = doc_ref.get(transaction=transaction)
    all_data = snapshot.to_dict().get('data', []) if snapshot.exists else []
    
    # Pisahkan data baris ini dan baris lain
    data_luar = [d for d in all_data if float(d.get('Baris', 0)) != float(b_pilih) or str(d.get('Part Number', '')).strip() == '']
    baris_actual = [d for d in all_data if float(d.get('Baris', 0)) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
    
    # Logika Kapasitas
    is_all_box = in_part.endswith(('D', 'G'))
    for d in baris_actual:
        if not str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')):
            is_all_box = False; break
            
    max_slot = 32 if (is_all_box and b_pilih <= 16) else (46 if is_all_box else (31 if b_pilih <= 16 else 44))
    if len(baris_actual) + in_qty > max_slot:
        return False, f"Rak penuh! Maksimal sisa {max_slot - len(baris_actual)} slot."
        
    # LOGIKA ARAH (Skenario A & B)
    if arah == "Depan":
        # Geser Pallet Lama ke Belakang
        for d in baris_actual:
            d['Pallet Ke'] = float(d['Pallet Ke']) + in_qty
        
        # Masukkan Barang Baru di urutan 1 sampai in_qty
        new_records = []
        for i in range(1, in_qty + 1):
            new_records.append({
                'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(i),
                'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk,
                'Status': 'Terisi', 'Keterangan': '', 'Catatan': ''
            })
    else:
        # Masukkan Barang Baru di paling belakang
        filled_pallets = [int(float(d.get('Pallet Ke', 0))) for d in baris_actual if pd.notna(d.get('Pallet Ke'))]
        next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
        new_records = []
        for _ in range(in_qty):
            while next_pallet in filled_pallets: next_pallet += 1
            new_records.append({
                'Kolom': log_data['Kolom'], 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_produk,
                'Status': 'Terisi', 'Keterangan': '', 'Catatan': ''
            })
            filled_pallets.append(next_pallet)
            next_pallet += 1
            
    # Gabung dan Simpan (Lock Data)
    final_data = data_luar + baris_actual + new_records
    transaction.set(doc_ref, {'data': final_data})
    
    # Catat Log
    db.collection(DB_LOG_MUTASI).add(log_data)
    return True, "Inbound berhasil disimpan!"

# ==========================================================
# 4. MANAJEMEN STATE (NAVIGASI)
# ==========================================================
if 'mode' not in st.session_state: st.session_state.mode = 'HOME'
if 'step' not in st.session_state: st.session_state.step = 1
if 'arah' not in st.session_state: st.session_state.arah = ""
if 'kolom' not in st.session_state: st.session_state.kolom = ""
if 'baris' not in st.session_state: st.session_state.baris = 0
if 'produk' not in st.session_state: st.session_state.produk = ""
if 'part_num' not in st.session_state: st.session_state.part_num = ""
if 'qty' not in st.session_state: st.session_state.qty = 1
if 'lot' not in st.session_state: st.session_state.lot = ""

def reset_state():
    st.session_state.mode = 'HOME'
    st.session_state.step = 1
    st.session_state.qty = 1
    st.session_state.lot = ""

# ==========================================================
# 5. LAYAR UTAMA (HOME)
# ==========================================================
if st.session_state.mode == 'HOME':
    st.title("🚜 Panel Operator WMS")
    st.write("Pilih tugas Anda saat ini:")
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📥 INBOUND\n(Barang Masuk Rak)", key="btn_in_home", use_container_width=True):
            st.session_state.mode = 'INBOUND'
            st.session_state.step = 1
            st.rerun()
    with c2:
        if st.button("📤 OUTBOUND\n(Ambil Barang)", key="btn_out_home", use_container_width=True):
            st.session_state.mode = 'OUTBOUND' # Modul Outbound menyusul di iterasi berikutnya
            st.rerun()

# ==========================================================
# 6. WIZARD INBOUND (LANGKAH DEMI LANGKAH)
# ==========================================================
elif st.session_state.mode == 'INBOUND':
    st.progress(st.session_state.step / 5, text=f"Langkah {st.session_state.step} dari 5")
    
    # ---------------- LANGKAH 1 ----------------
    if st.session_state.step == 1:
        st.header("1. Posisi Forklift Anda?")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("⬆️\nMASUK DARI DEPAN\n(Dorong Manual)", use_container_width=True):
                st.session_state.arah = "Depan"
                st.session_state.step = 2; st.rerun()
        with c2:
            if st.button("⬇️\nMASUK DARI BELAKANG\n(Robot / Autosaat)", use_container_width=True):
                st.session_state.arah = "Belakang"
                st.session_state.step = 2; st.rerun()
        
        st.write(""); st.button("🔙 Batal (Ke Menu Awal)", on_click=reset_state)

    # ---------------- LANGKAH 2 ----------------
    elif st.session_state.step == 2:
        st.header(f"2. Pilih Blok Rak ({st.session_state.arah})")
        cols = st.columns(4)
        for i, blk in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G']):
            with cols[i % 4]:
                if st.button(f"BLOK {blk}", key=f"blk_{blk}", use_container_width=True):
                    st.session_state.kolom = blk
                    st.session_state.step = 3; st.rerun()
        st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=1))

    # ---------------- LANGKAH 3 ----------------
    elif st.session_state.step == 3:
        st.header(f"3. Rak Tujuan di Blok {st.session_state.kolom}")
        st.caption("Memuat kondisi fisik rak saat ini...")
        
        # Tarik data 1 kolom spesifik untuk pewarnaan tombol
        doc = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}").get()
        all_k_data = doc.to_dict().get('data', []) if doc.exists else []
        
        p_cols = st.columns(4)
        for i in range(1, 29): # Rak 1-28
            # Hitung isi rak
            isi_rak = len([d for d in all_k_data if float(d.get('Baris', 0)) == float(i) and str(d.get('Part Number', '')).strip() != ''])
            
            # CSS Class dinamis
            css_class = ""
            if isi_rak == 0: css_class = "rak-hijau"
            elif isi_rak >= 31: css_class = "rak-merah" # Asumsi penuh
            else: css_class = "rak-kuning"
            
            label_btn = f"Rak {i}\n({isi_rak} Terisi)" if isi_rak > 0 else f"Rak {i}\n(KOSONG)"
            
            with p_cols[(i-1) % 4]:
                st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
                if isi_rak >= 46: # Benar-benar mentok
                    st.button(f"🔒 {i} Penuh", key=f"rk_{i}", disabled=True, use_container_width=True)
                else:
                    if st.button(label_btn, key=f"rk_{i}", use_container_width=True):
                        st.session_state.baris = i
                        st.session_state.step = 4; st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
                
        st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=2))

    # ---------------- LANGKAH 4 ----------------
    elif st.session_state.step == 4:
        st.header("4. Barang yang dimasukkan?")
        st.info(f"📍 Tujuan: Rak {st.session_state.kolom}-{st.session_state.baris} (Lewat {st.session_state.arah})")
        
        df_master = load_master_produk()
        if not df_master.empty:
            # Gunakan dropdown besar untuk mencari produk agar tidak sesak di layar
            pilihan = st.selectbox("Pilih Produk", ["-- Pilih Produk --"] + sorted(df_master['Nama Barang'].dropna().unique().tolist()))
            st.session_state.lot = st.text_input("No. Lot Produksi", placeholder="Ketik No Lot (Opsional)")
            
            if pilihan != "-- Pilih Produk --":
                st.session_state.produk = pilihan
                st.session_state.part_num = df_master[df_master['Nama Barang'] == pilihan]['Part Number'].values[0]
                
                st.write("")
                if st.button("➡️ Lanjut ke Qty", use_container_width=True):
                    st.session_state.step = 5; st.rerun()
        else:
            st.warning("Master Produk tidak ada. Hubungi Admin PPIC.")
            
        st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=3))

    # ---------------- LANGKAH 5 ----------------
    elif st.session_state.step == 5:
        st.header("5. Kuantitas (Pallet Utuh)")
        st.subheader(f"{st.session_state.produk}")
        
        # Numpad Virtual
        n1, n2, n3 = st.columns([1, 1.5, 1])
        with n1:
            if st.button("➖ 1 Pallet", use_container_width=True) and st.session_state.qty > 1:
                st.session_state.qty -= 1; st.rerun()
        with n2:
            st.markdown(f"<h1 style='text-align: center; font-size: 60px;'>{st.session_state.qty}</h1>", unsafe_allow_html=True)
        with n3:
            if st.button("➕ 1 Pallet", use_container_width=True):
                st.session_state.qty += 1; st.rerun()

        st.divider()
        if st.button("🚀 SIMPAN KE RAK", type="primary", use_container_width=True):
            with st.spinner("Mengamankan transaksi ke database..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state.kolom}")
                
                # Menyiapkan log data untuk dicatat di dalam transaction jika sukses
                log_mutasi_data = {
                    "Waktu Transaksi": (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S"),
                    "Jenis Transaksi": f"INBOUND (Lewat {st.session_state.arah})",
                    "Lokasi Rak": f"{st.session_state.kolom}-{st.session_state.baris}",
                    "Nama Produk": st.session_state.produk,
                    "Part Number": st.session_state.part_num,
                    "No Lot": st.session_state.lot if st.session_state.lot else "-",
                    "Mutasi Pallet": float(st.session_state.qty),
                    "Mutasi Pecahan (Dus/Sak)": 0.0, "Mutasi Eceran (Pcs)": 0.0, "Keterangan": "",
                    "Kolom": st.session_state.kolom, # Temp key untuk transaction
                    "Timestamp_System": firestore.SERVER_TIMESTAMP
                }
                
                # EKSEKUSI TRANSACTION
                sukses, pesan = tx_simpan_inbound(db.transaction(), doc_ref, st.session_state.baris, st.session_state.arah, st.session_state.produk, st.session_state.part_num, st.session_state.lot, st.session_state.qty, log_mutasi_data)
                
                if sukses:
                    st.success(pesan)
                    st.balloons()
                    time.sleep(2)
                    reset_state()
                    st.rerun()
                else:
                    st.error(pesan)
                    
        st.write(""); st.button("🔙 Kembali", on_click=lambda: st.session_state.update(step=4))

# ==========================================================
# 7. WIZARD OUTBOUND (SEGERA HADIR)
# ==========================================================
elif st.session_state.mode == 'OUTBOUND':
    st.header("Modul Outbound")
    st.info("Modul pencarian lot dan input sisa sedang dalam tahap pengembangan (Fase Berikutnya).")
    st.button("🔙 Batal (Ke Menu Awal)", on_click=reset_state)