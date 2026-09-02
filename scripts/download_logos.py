"""
Script to download official brand logos for addons and templates.
"""
import os
import sys

# Delegate to download_all_logos.py
sys.path.insert(0, os.path.dirname(__file__))
from download_all_logos import main as run_download
from update_matrix_docs import update_asset_sources

def main():
    print("Running comprehensive logo updater...")
    run_download()
    print("Updating documentation and certification matrix...")
    update_asset_sources()
    print("Done! All addon and template assets are verified.")

if __name__ == "__main__":
    main()
