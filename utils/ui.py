import streamlit as st
import base64
import os

def setup_header():
    current_script_path = os.path.abspath(__file__)
    
    # Dapatkan folder tempat skrip ini berada (.../data23/utils)
    utils_folder = os.path.dirname(current_script_path)
    
    # Dapatkan folder induknya (ini adalah folder dataset Anda, misal .../data23)
    base_dataset_folder = os.path.dirname(utils_folder)
    
    # Gabungkan path folder induk dengan nama file logo Anda
    logo_path = os.path.join(base_dataset_folder, "Logo_BMKG.png")
    
    # --- SELESAI BLOK TAMBAHAN ---
    with open(logo_path, "rb") as f:
        logo_base64 = base64.b64encode(f.read()).decode()

    st.markdown(f"""
    <style>
        .header-container {{
            display: flex;
            align-items: center;
            background-color: #e9f2fb;
            padding: 4px 14px;
            border-radius: 8px;
            position: sticky;
            top: 0;
            z-index: 999;
        }}
        .header-container img {{
            width: 55px;
            margin-right: 15px;
        }}
        .header-text h4 {{
            margin: 0;
            color: #1f4e79;
            line-height: 1; 
        }}
        .header-text h6 {{
            margin: 0;
            color: gray;
            font-weight: normal;
            line-height: 0.5;
        }}
    </style>

    <div class="header-container">
        <img src="data:image/png;base64,{logo_base64}" alt="BMKG Logo">
        <div class="header-text">
            <h4>Dashboard Monitoring Sandi CLIMAT</h4>
            <h6>Tim Kerja Manajemen Observasi Meteorologi Permukaan</h6>
        </div>
    </div>
    <hr>
    """, unsafe_allow_html=True)
