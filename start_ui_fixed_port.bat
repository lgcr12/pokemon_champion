@echo off
cd /d E:\pk_champion
python -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0 --browser.gatherUsageStats false --server.headless true
