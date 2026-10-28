import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy
import re
from datetime import datetime, timedelta

# ==========================================================
# 1. KONFIGURASI WEB & DATABASE PRODUKSI
# ==========================================================
st.set_page_config(page_title="WMS Master Control - PPIC", page_icon="⚙️", layout="wide")

NAMA_DATABASE = 'gudang_hdi_utama'
DB_LOG_MUTASI = 'log_mutasi_hdi_utama'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("⚙️ WMS Master Control & Override (PPIC)")
st.caption(f"Terhubung ke database produksi: **{NAMA_DATABASE}**")

# --- CACHE: MASTER PRODUK (1 JAM) ---
@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except: df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.toast(f"Gagal memuat master produk: {e}", icon="🚨")
        return pd.DataFrame()

# --- FUNGSI PENCATAT LOG MUTASI OTOMATIS ---
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    no_lot_final = no_lot if str(no_lot).strip() != "" else "-"
    
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": no_lot_final,
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception as e: st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 2. FITUR UNDO & REDO
# ==========================================================
if 'undo_data' not in st.session_state:
    st.session_state.update({'undo_data': None, 'redo_data': None, 'action_msg': "", 'action_kolom': "", 'action_status': None})

if st.session_state['action_status'] == 'DONE':
    st.info(f"🔄 **Aksi Terakhir:** {st.session_state['action_msg']}")
    if st.button("BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data seperti semula..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            catat_log_mutasi("PEMBATALAN (UNDO)", st.session_state['action_kolom'], "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Membatalkan aksi: {st.session_state['action_msg']}")
            st.success("✅ Aksi dibatalkan (Undo)! Data kembali seperti semula.")
            time.sleep(1.5)
            st.rerun()
if st.session_state['action_status'] is not None: st.divider()

# ==========================================================
# 3. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📥 1. Inbound Manual", "📤 2. Outbound Koreksi", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🚀 5. Sinkronisasi CSV", "📅 6. Jadwal Kirim"])

# TAB 1: INBOUND MANUAL
with tab1:
    st.header("Suntik Data Masuk Manual (By-pass Operator)")
    st.caption("Gunakan jika ada tumpukan barang masuk yang belum/gagal diinput operator LTI.")
    
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state.update({'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1})

    col_in1, col_in2 = st.columns([1, 2])
    
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="in_kolom")
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            
    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_in = doc_ref_in.get()
            all_data_raw = doc_in.to_dict().get('data', []) if doc_in.exists else []
            
            baris_data_preview = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            is_all_box_existing = True
            
            if not baris_data_preview: is_all_box_existing = False
            for d in baris_data_preview:
                if not str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')):
                    is_all_box_existing = False; break
                    
            max_slot_display = 32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44))
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty
            
            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            
            df_baris = pd.DataFrame(baris_data_preview)
            if not df_baris.empty:
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan'] if c in df_baris.columns]
                st.dataframe(df_baris[kolom_tersedia], hide_index=True, height=150)
            else:
                st.info("Rak ini masih kosong. Siap diisi penuh!")
                
            st.divider()
            st.markdown(f"**2. Eksekusi Inbound (Tujuan: {k_pilih}-{b_pilih})**")
            
            df_master = load_master_produk()
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist() if not df_master.empty else []
            if df_master.empty: st.warning("⚠️ File 'Master_Produk.csv' tidak ditemukan. Mode manual.")
                
            pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)
            
            if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
                in_produk = pilihan_barang
                match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
                in_part = st.text_input("Part Number (Auto-fill)", value=str(match_part[0]) if len(match_part) > 0 else "", disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")
                
            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="Kosongkan jika tidak ada, sistem akan otomatis mengisi '-'")
            
            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1: in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1)
            with col_qty2: in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0)
            with col_qty3: in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0)
                
            if st.button("🚀 Simpan Inbound Manual", type="primary", use_container_width=True):
                in_part_clean = in_part.strip().upper() if in_part else ""
                in_produk_clean = in_produk.strip() if in_produk else ""
                in_lot_final = in_lot.strip() if in_lot and in_lot.strip() != "" else "-"

                if not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty (Pallet/Pecahan/Eceran)!")
                else:
                    with st.spinner("Menyusun data ke dalam rak..."):
                        doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                        doc = doc_ref.get()
                        all_data_raw2 = doc.to_dict().get('data', []) if doc.exists else []
                        data_sebelum = copy.deepcopy(all_data_raw2)
                        
                        all_data = [d for d in all_data_raw2 if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                        baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        is_all_box_actual = in_part_clean.endswith(('D', 'G'))
                        for d in baris_data_actual:
                            pn = str(d.get('Part Number', '')).strip().upper()
                            if pn and not pn.endswith(('D', 'G')):
                                is_all_box_actual = False; break
                                
                        max_slot_actual = 32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44))
                        filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke')) and str(d.get('Pallet Ke')).strip() != '']
                        current_qty_actual = len(filled_pallets)
                        
                        slot_tambahan_in = 1 if (in_pecahan > 0 or in_eceran > 0) else 0
                        total_inbound_slots = in_qty + slot_tambahan_in
                        
                        next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                        new_records = []
                        
                        for _ in range(int(in_qty)):
                            while next_pallet in filled_pallets: next_pallet += 1
                            is_bayangan = next_pallet > max_slot_actual
                            catatan_bayangan = "[BAYANGAN - OVERCAPACITY]" if is_bayangan else ""
                                
                            new_records.append({
                                'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': catatan_bayangan
                            })
                            filled_pallets.append(next_pallet)
                            next_pallet += 1
                            
                        if in_pecahan > 0 or in_eceran > 0:
                            while next_pallet in filled_pallets: next_pallet += 1
                            is_bayangan = next_pallet > max_slot_actual
                            catatan_bayangan = "[BAYANGAN - OVERCAPACITY]" if is_bayangan else ""
                                
                            ket_parts = []
                            if in_pecahan > 0: ket_parts.append(f"Pecahan: {in_pecahan} qty")
                            if in_eceran > 0: ket_parts.append(f"Eceran: {in_eceran} pcs")
                            
                            new_records.append({
                                'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                'Status': 'Terisi', 'Catatan': catatan_bayangan,
                                'Keterangan': " | ".join(ket_parts)
                            })
                            filled_pallets.append(next_pallet)
                            
                        all_data.extend(new_records)
                        
                        st.session_state.update({'undo_data': data_sebelum, 'redo_data': copy.deepcopy(all_data), 'action_kolom': k_pilih, 'action_msg': f"Inbound {total_inbound_slots} Slot Fisik '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih}", 'action_status': 'DONE'})
                        
                        doc_ref.set({'data': all_data})
                        catat_log_mutasi("INBOUND (PPIC)", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final, in_qty, in_pecahan, in_eceran)
                        
                        if current_qty_actual + total_inbound_slots > max_slot_actual:
                            st.warning(f"⚠️ Data tersimpan dengan SOFT-WARNING! Masuk {total_inbound_slots} slot, menembus batas maksimal rak ({max_slot_actual}). Pallet kelebihan ditandai sebagai [BAYANGAN].")
                        else:
                            st.success(f"✅ Berhasil menyuntikkan data! Memakan {total_inbound_slots} Slot Fisik di rak {k_pilih}-{b_pilih}.")
                            
                        st.session_state['in_lokasi_dicari'] = False
                        time.sleep(2.5)
                        st.rerun()
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cek Status Rak'.")

# TAB 2: OUTBOUND KOREKSI DENGAN RE-INDEXING
with tab2:
    st.header("Penyesuaian Sisa Stok (Berdasarkan No. Lot)")
    st.caption("Pilih No. Lot yang dibongkar, lalu masukkan SISA AKHIR fisik rak.")
    
    if 'out_lokasi_dicari' not in st.session_state:
        st.session_state.update({'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1})

    col_out1, col_out2 = st.columns([1, 2])
    with col_out1:
        st.markdown("**1. Pilih Lokasi Rak**")
        out_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="out_kolom")
        out_baris = st.number_input("Nomor Baris (Outbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cari Data Rak", use_container_width=True):
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            
    with col_out2:
        if st.session_state['out_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
            doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_out = doc_ref_out.get()
            all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
            baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_out_data:
                df_out = pd.DataFrame(baris_out_data)
                df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
                df_out = df_out[df_out['Part Number'].astype(str).str.strip() != ""]
                
                if not df_out.empty and 'Lot Number' in df_out.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    for _, row in df_out.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                        lot_asli = str(row['Lot Number'])
                        label_dropdown = f"{str(row['Nama Produk'])} (Lot: {lot_asli})"
                        tersedia_lot.append(label_dropdown)
                        mapping_lot[label_dropdown] = lot_asli
                        
                    st.markdown(f"**2. Eksekusi Pembongkaran di Rak {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk & No. Lot yang dikeluarkan:", tersedia_lot)
                    pilih_lot = mapping_lot[pilihan_label]
                    df_lot = df_out[df_out['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=False)
                    current_qty_lot = len(df_lot)
                    
                    jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                    pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                    
                    pecahan_awal, eceran_awal = 0, 0
                    if not pecahan_rows.empty:
                        ket_str = str(pecahan_rows['Keterangan'].iloc[0]).lower()
                        match_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
                        if match_pec: pecahan_awal = int(match_pec.group(1))
                        else:
                            numbers = re.findall(r'\d+', ket_str)
                            if numbers and 'pecahan' in ket_str: pecahan_awal = int(numbers[0])
                        match_ec = re.search(r'eceran:\s*(\d+)', ket_str)
                        if match_ec: eceran_awal = int(match_ec.group(1))
                    
                    st.markdown(f"**📦 Total Lot `{pilih_lot}` di Rak ini: `{current_qty_lot}` Slot Fisik**")
                    st.caption(f"*(Fisik asli: {jml_utuh_awal} Pallet Utuh, {pecahan_awal} Dus/Sak, {eceran_awal} Pcs Eceran)*")
                    
                    kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan', 'Catatan'] if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia].head(5), hide_index=True)
                    
                    col_sisa1, col_sisa2, col_sisa3 = st.columns(3)
                    with col_sisa1: out_sisa_akhir = st.number_input(f"Sisa Pallet UTUH Fisik", min_value=0, max_value=jml_utuh_awal, step=1, value=jml_utuh_awal)
                    with col_sisa2: out_sisa_dus = st.number_input("Sisa Pecahan Fisik (Dus/Sak)", min_value=0, step=1, value=pecahan_awal)
                    with col_sisa3: out_sisa_pcs = st.number_input("Sisa Eceran Fisik (Pcs)", min_value=0, step=1, value=eceran_awal)

                    out_catatan = st.text_input("📝 Catatan Penyesuaian Fisik (Opsional)", placeholder="Kosongkan jika stok fisik di lapangan sesuai dengan sistem...")
                
                    if st.button("🚜 Sesuaikan Stok Lot Ini (OUTBOUND)", type="primary", use_container_width=True):
                        slot_tambahan = 1 if (out_sisa_dus > 0 or out_sisa_pcs > 0) else 0
                        total_slot_dibutuhkan = out_sisa_akhir + slot_tambahan
                        
                        if total_slot_dibutuhkan > current_qty_lot:
                            st.error(f"❌ Tidak logis! Anda mengklaim sisa fisik {total_slot_dibutuhkan} Slot, padahal di sistem hanya ada {current_qty_lot} Pallet.")
                        elif out_sisa_akhir == jml_utuh_awal and out_sisa_dus == pecahan_awal and out_sisa_pcs == eceran_awal and out_catatan == "":
                            st.info("Sisa stok persis sama dengan di sistem dan tidak ada catatan. Tidak ada pergerakan Outbound yang dicatat.")
                        else:
                            keluar_utuh = jml_utuh_awal - out_sisa_akhir
                            keluar_pecahan = pecahan_awal - out_sisa_dus
                            keluar_eceran = eceran_awal - out_sisa_pcs
                            data_sebelum_out = copy.deepcopy(all_out_data)
                            
                            out_qty = current_qty_lot - total_slot_dibutuhkan
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            new_all_out_data = []
                            for d in all_out_data:
                                b_val = float(d.get('Baris', 0) or 0)
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                l_str = str(d.get('Lot Number', ''))
                                
                                if b_val == float(b_pilih) and p_val in pallets_to_remove: continue 
                                
                                if b_val == float(b_pilih) and l_str == pilih_lot:
                                    d['Catatan'] = out_catatan
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        if out_sisa_dus > 0 or out_sisa_pcs > 0:
                                            ket_parts = []
                                            if out_sisa_dus > 0: ket_parts.append(f"Pecahan: {out_sisa_dus} qty")
                                            if out_sisa_pcs > 0: ket_parts.append(f"Eceran: {out_sisa_pcs} pcs")
                                            d['Keterangan'] = " | ".join(ket_parts)
                                        else:
                                            if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): d['Keterangan'] = ""
                                    else:
                                        if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): d['Keterangan'] = ""
                                        
                                new_all_out_data.append(d)
                            
                            # RE-INDEXING PALLET
                            data_rak_ini = [d for d in new_all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                            data_rak_lain = [d for d in new_all_out_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
                            
                            data_rak_ini = sorted(data_rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            
                            valid_items = [x for x in data_rak_ini if str(x.get('Part Number', '')).strip() != '']
                            is_all_box_out = False
                            if valid_items:
                                is_all_box_out = all(str(x.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for x in valid_items)
                                
                            b_val_float = float(b_pilih)
                            max_slot_out = int(32 if b_val_float <= 16 else 46) if is_all_box_out else int(31 if b_val_float <= 16 else 44)
                            
                            for urutan_baru, d in enumerate(data_rak_ini, start=1):
                                d['Pallet Ke'] = float(urutan_baru)
                                catatan_lama = str(d.get('Catatan', ''))
                                catatan_lama = re.sub(r'\[BOOKED:.*?\]', '', catatan_lama, flags=re.IGNORECASE).strip()
                                
                                if urutan_baru <= max_slot_out and 'BAYANGAN' in catatan_lama.upper():
                                    d['Catatan'] = catatan_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                                elif urutan_baru > max_slot_out and 'BAYANGAN' not in catatan_lama.upper():
                                    d['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + catatan_lama).strip()
                                else:
                                    d['Catatan'] = catatan_lama
                                    
                            new_all_out_data = data_rak_lain + data_rak_ini
                            
                            st.session_state.update({'undo_data': data_sebelum_out, 'redo_data': copy.deepcopy(new_all_out_data), 'action_kolom': k_pilih, 'action_msg': f"Penyesuaian Outbound Lot '{pilih_lot}' di Rak {k_pilih}-{b_pilih}", 'action_status': 'DONE'})
                            
                            doc_ref_out.set({'data': new_all_out_data})
                            catat_log_mutasi("OUTBOUND (PPIC)", k_pilih, b_pilih, str(df_lot['Nama Produk'].iloc[0]), str(df_lot['Part Number'].iloc[0]), pilih_lot, keluar_utuh, keluar_pecahan, keluar_eceran, out_catatan)
                            
                            st.success(f"✅ Selesai! Sisa di rak menjadi {total_slot_dibutuhkan} Slot Fisik dan barisan pallet telah dirapikan (Re-indexed).")
                            st.session_state['out_lokasi_dicari'] = False
                            time.sleep(2.5)
                            st.rerun()
                else:
                    st.info(f"📍 Rak {k_pilih}-{b_pilih} masih kosong.")
            else:
                st.info(f"📍 Rak {k_pilih}-{b_pilih} belum ada data.")
        else:
            st.info("👈 Silakan tentukan Lokasi Kolom dan Baris di sebelah kiri, lalu klik 'Cari Data Rak'.")

# TAB 3: AUDIT & RE-INDEXING GLOBAL
with tab3:
    st.header("⚙️ Audit Stok & Maintenance Gudang")
    
    st.subheader("🔧 Re-Indexing Global Gudang")
    st.write("Jalankan jika nomor urut pallet berantakan. Sistem akan merapikan index seluruh rak gudang dari nomor 1 secara konsisten.")
    
    if st.button("⚙️ Jalankan Re-Indexing Semua Rak Sekarang"):
        with st.spinner("Merapikan seluruh index rak gudang..."):
            try:
                docs = db.collection(NAMA_DATABASE).stream()
                batch = db.batch()
                total_koreksi = 0
                
                for doc in docs:
                    kolom_id = doc.id
                    doc_data = doc.to_dict().get('data', [])
                    if not doc_data: continue
                    
                    df_col = pd.DataFrame(doc_data)
                    if 'Baris' not in df_col.columns or 'Pallet Ke' not in df_col.columns: continue
                    
                    df_col['Baris'] = pd.to_numeric(df_col['Baris'], errors='coerce')
                    df_col['Pallet Ke'] = pd.to_numeric(df_col['Pallet Ke'], errors='coerce')
                    
                    new_doc_data = []
                    for baris, group in df_col.groupby('Baris'):
                        group = group.sort_values('Pallet Ke', ascending=True)
                        valid_group = group[~group['Part Number'].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])]
                        is_all_box = False
                        if not valid_group.empty:
                            is_all_box = bool(valid_group['Part Number'].astype(str).str.upper().str.endswith(('D', 'G')).all())
                            
                        max_slot_actual = int(32 if baris <= 16 else 46) if is_all_box else int(31 if baris <= 16 else 44)
                        
                        for i, (idx, row) in enumerate(group.iterrows(), start=1):
                            row_dict = row.to_dict()
                            row_dict['Pallet Ke'] = float(i)
                            catatan_lama = str(row_dict.get('Catatan', ''))
                            
                            if i <= max_slot_actual and 'BAYANGAN' in catatan_lama.upper():
                                row_dict['Catatan'] = catatan_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                            elif i > max_slot_actual and 'BAYANGAN' not in catatan_lama.upper():
                                row_dict['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + catatan_lama).strip()
                                
                            new_doc_data.append(row_dict)
                    
                    doc_ref = db.collection(NAMA_DATABASE).document(kolom_id)
                    batch.set(doc_ref, {'data': new_doc_data})
                    total_koreksi += 1
                
                batch.commit()
                st.success(f"✅ Re-Indexing Selesai! {total_koreksi} Kolom rak berhasil diurutkan ulang.")
                time.sleep(2)
                st.rerun()
            except Exception as e:
                st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Ekspor Laporan Audit (CSV Super)")
    
    if st.button("Racik Laporan Audit Stok", type="primary"):
        with st.spinner("Membaca database..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data: semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                df_master = load_master_produk()
                
                qty_utuh_list, qty_dus_list, qty_pcs_list, grand_total_list = [], [], [], []
                for idx, row in df_export.iterrows():
                    if pd.isna(row.get('Part Number')) or str(row.get('Part Number')).strip() == '':
                        qty_utuh_list.append(0); qty_dus_list.append(0); qty_pcs_list.append(0); grand_total_list.append(0)
                        continue
                        
                    ket = str(row.get('Keterangan', '')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    q_utuh = 0 if is_pecahan else 1
                    q_dus, q_pcs = 0, 0
                    
                    if is_pecahan:
                        match_pec = re.search(r'pecahan:\s*(\d+)', ket)
                        if match_pec: q_dus = int(match_pec.group(1))
                        else:
                            nums = re.findall(r'\d+', ket)
                            if nums and 'pecahan' in ket: q_dus = int(nums[0])
                        match_ec = re.search(r'eceran:\s*(\d+)', ket)
                        if match_ec: q_pcs = int(match_ec.group(1))
                        
                    qty_utuh_list.append(q_utuh)
                    qty_dus_list.append(q_dus)
                    qty_pcs_list.append(q_pcs)
                    
                    part_num_clean = str(row.get('Part Number', '')).strip().upper()
                    isi_pallet, pcs_per_satuan = 0, 0
                    
                    if not df_master.empty and part_num_clean in df_master['Part Number Clean'].values:
                        m_row = df_master[df_master['Part Number Clean'] == part_num_clean].iloc[0]
                        isi_pallet = pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                        isi_pallet = 0 if pd.isna(isi_pallet) else isi_pallet
                        val_satuan = pd.to_numeric(m_row.get('Box / Pallet' if part_num_clean.endswith('D') else 'Sac / Pallet', 0), errors='coerce')
                        pcs_per_satuan = 0 if pd.isna(val_satuan) else val_satuan
                        
                    grand_total_list.append((q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs)
                    
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list
                
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Status', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']
                for c in kolom_harapan:
                    if c not in df_export.columns: df_export[c] = ""
                        
                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']: df_export[col] = df_export[col].replace(0, "")
                        
                df_export = df_export[kolom_harapan]
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                
                st.success("✅ CSV Super berhasil diracik!")
                st.download_button(label="⬇️ Unduh CSV Super (Pivot Excel)", data=csv_buffer.getvalue(), file_name=f"Audit_Stok_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else: st.warning("Database kosong.")

# TAB 4: LAPORAN MUTASI HARIAN
with tab4:
    st.header("📊 Laporan Mutasi Harian (24 Jam Terakhir)")
    st.write("Menarik riwayat pergerakan Inbound/Outbound dalam 24 jam terakhir.")
    
    if st.button("Racik Laporan Mutasi", type="primary"):
        with st.spinner("Menarik log mutasi..."):
            try:
                waktu_24_jam_lalu_utc = datetime.utcnow() - timedelta(hours=24)
                log_docs = db.collection(DB_LOG_MUTASI).where("Timestamp_System", ">=", waktu_24_jam_lalu_utc).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).stream()
                
                log_list = [d.to_dict() for d in log_docs]
                if log_list:
                    df_log = pd.DataFrame(log_list)
                    avail_cols = [c for c in ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Mutasi Eceran (Pcs)", "Part Number", "Keterangan"] if c in df_log.columns]
                    df_log = df_log[avail_cols]
                    
                    csv_log_buffer = io.StringIO()
                    df_log.to_csv(csv_log_buffer, index=False, sep=";")
                    
                    st.success("✅ Laporan Mutasi siap!")
                    st.download_button(label="⬇️ Unduh CSV Mutasi", data=csv_log_buffer.getvalue(), file_name=f"Mutasi_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
                else: st.info("Belum ada mutasi 24 jam terakhir.")
            except Exception as e: st.error(f"Gagal memuat log mutasi: {e}")

# TAB 5: SINKRONISASI / OVERRIDE DATABASE
with tab5:
    st.header("🚀 Mode Override: Timpa Database via CSV")
    st.warning("⚠️ **PERINGATAN:** Ini akan MENGHAPUS SELURUH DATA GUDANG LAMA dan menimpanya dengan isi CSV yang diunggah!")
    
    uploaded_file = st.file_uploader("Pilih file CSV Super", type=['csv'])
    if uploaded_file is not None:
        try:
            try: df_sync = pd.read_csv(uploaded_file, sep=";")
            except: df_sync = pd.read_csv(uploaded_file, sep=",")
            
            for c in df_sync.columns:
                if c.strip().lower() in ['palet ke', 'pallet ke']: df_sync.rename(columns={c: 'Pallet Ke'}, inplace=True)
            df_sync = df_sync.fillna("")
            
            if 'Kolom' in df_sync.columns:
                st.dataframe(df_sync.head(5))
                if st.button("⚡ EKSEKUSI OVERRIDE DATABASE PRODUKSI", type="primary"):
                    if len(df_sync) < 50:
                        st.error("Ditolak! CSV terlalu sedikit (<50 baris).")
                    else:
                        with st.spinner("Menimpa database produksi..."):
                            docs = db.collection(NAMA_DATABASE).stream()
                            for doc in docs: doc.reference.delete()
                            
                            batch = db.batch()
                            for k, group in df_sync.groupby('Kolom'):
                                if str(k).strip() != "":
                                    doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k}")
                                    batch.set(doc_ref, {'data': group.to_dict(orient='records')})
                            batch.commit()
                            catat_log_mutasi("OVERRIDE DATABASE", "ALL", "ALL", "RESET SYSTEM", "BY CSV", "-", 0, 0, 0, "Override data via CSV Upload.")
                            st.success("✅ Override berhasil!")
                            st.balloons()
        except Exception as e: st.error(f"Error reading file: {e}")

# TAB 6: PENJADWALAN KIRIM / BOOKING
with tab6:
    st.header("📅 Penjadwalan Pengiriman (Booking Pallet)")
    st.caption("Kunci pallet di rak tertentu agar ditandai persiapan muat (Soft Allocation).")
    
    if 'book_lokasi_dicari' not in st.session_state:
        st.session_state.update({'book_lokasi_dicari': False, 'book_k_pilih': "A", 'book_b_pilih': 1})

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        st.markdown("**1. Pilih Lokasi Rak**")
        book_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="book_kolom")
        book_baris = st.number_input("Nomor Baris (Booking)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cari Data Rak (Booking)", use_container_width=True):
            st.session_state.update({'book_lokasi_dicari': True, 'book_k_pilih': book_kolom, 'book_b_pilih': book_baris})
            
    with col_b2:
        if st.session_state['book_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['book_k_pilih'], st.session_state['book_b_pilih']
            doc_ref_book = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_book = doc_ref_book.get()
            all_book_data = doc_book.to_dict().get('data', []) if doc_book.exists else []
            baris_book_data = [d for d in all_book_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_book_data:
                df_book = pd.DataFrame(baris_book_data)
                df_book['Pallet Ke'] = pd.to_numeric(df_book['Pallet Ke'], errors='coerce')
                df_book = df_book[df_book['Part Number'].astype(str).str.strip() != ""]
                
                if not df_book.empty and 'Lot Number' in df_book.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    for _, row in df_book.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                        lot_asli = str(row['Lot Number'])
                        label_dropdown = f"{str(row['Nama Produk'])} (Lot: {lot_asli})"
                        tersedia_lot.append(label_dropdown)
                        mapping_lot[label_dropdown] = lot_asli
                        
                    st.markdown(f"**2. Pilih Produk & Lot untuk di-Booking di {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk:", tersedia_lot, key="book_lot")
                    pilih_lot = mapping_lot[pilihan_label]
                    
                    df_lot = df_book[df_book['Lot Number'].astype(str) == pilih_lot].sort_values('Pallet Ke', ascending=True)
                    df_belum_book = df_lot[~df_lot['Catatan'].astype(str).str.contains('BOOKED', case=False, na=False)]
                    qty_tersedia = len(df_belum_book)
                    
                    st.markdown(f"**📦 Tersedia untuk di-Booking: `{qty_tersedia}` Slot Fisik**")
                    kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Lot Number', 'Catatan'] if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia], hide_index=True, height=150)
                    
                    if qty_tersedia > 0:
                        book_qty = st.number_input("Jumlah Pallet UTUH yang akan di-Booking", min_value=1, max_value=qty_tersedia, step=1, value=qty_tersedia)
                        book_do = st.text_input("📝 Terjadwal Untuk (Cth: DO-123 / Tgl 28)", placeholder="Ketik keterangan pengiriman...")
                        
                        if st.button("🔒 Kunci / Booking Pallet Sekarang", type="primary", use_container_width=True):
                            if book_do.strip() == "":
                                st.error("❌ Keterangan jadwal wajib diisi!")
                            else:
                                with st.spinner("Menyematkan label booking..."):
                                    pallets_to_book = df_belum_book['Pallet Ke'].head(book_qty).tolist()
                                    data_sebelum_book = copy.deepcopy(all_book_data)
                                    new_all_book_data = []
                                    
                                    for d in all_book_data:
                                        b_val = float(d.get('Baris', 0) or 0)
                                        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                        if b_val == float(b_pilih) and p_val in pallets_to_book:
                                            catatan_lama = str(d.get('Catatan', ''))
                                            d['Catatan'] = (f"[BOOKED: {book_do.strip()}] " + catatan_lama).strip()
                                        new_all_book_data.append(d)
                                        
                                    doc_ref_book.set({'data': new_all_book_data})
                                    st.session_state.update({'undo_data': data_sebelum_book, 'redo_data': copy.deepcopy(new_all_book_data), 'action_kolom': k_pilih, 'action_msg': f"Booking {book_qty} Pallet Lot '{pilih_lot}' untuk '{book_do}' di Rak {k_pilih}-{b_pilih}", 'action_status': 'DONE'})
                                    st.success(f"✅ Berhasil! {book_qty} pallet telah dikunci untuk jadwal: {book_do}")
                                    st.session_state['book_lokasi_dicari'] = False
                                    time.sleep(2)
                                    st.rerun()
                    else: st.warning("⚠️ Semua pallet pada Lot ini sudah ter-booking.")
            else: st.info(f"📍 Rak {k_pilih}-{b_pilih} kosong.")
