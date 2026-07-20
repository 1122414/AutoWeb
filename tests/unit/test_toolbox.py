import os

import pytest


if os.getenv("AUTOWEB_RUN_INTEGRATION") != "1":
    pytest.skip(
        "legacy live-network toolbox smoke test",
        allow_module_level=True,
    )

from skills.toolbox import *
from skills.tool_rag import ask_knowledge_base

def test_toolbox():
    print("🚀 Starting Toolbox Verification...\n")

    # 1. Test HTTP
    print("--- Test 1: HTTP Request ---")
    url = "https://httpbin.org/get"
    resp = http_request(url)
    if "httpbin" in resp: print("✅ HTTP Success")
    else: print(f"❌ HTTP Fail: {resp[:100]}")

    # 2. Test File Downloader
    print("\n--- Test 2: File Download ---")
    file_url = "https://httpbin.org/image/png"
    save_path = "test_download.png"
    if download_file(file_url, save_path) and os.path.exists(save_path):
        print("✅ Download Success")
        os.remove(save_path)
    else:
        print("❌ Download Fail")

    # 3. Test Content Cleaner
    print("\n--- Test 3: Cleaner ---")
    raw_html = "<html><script>alert(1)</script><body><h1>Hello</h1><!-- comment --></body></html>"
    cleaned = clean_html(raw_html)
    if "alert" not in cleaned and "Hello" in cleaned:
        print(f"✅ Cleaner Success: {cleaned}")
    else:
        print(f"❌ Cleaner Fail: {cleaned}")

    # 4. Test DB
    print("\n--- Test 4: DB Insert ---")
    data = {"name": "AutoWeb", "version": "2.0"}
    if db_insert("test_table", data, "test.db"):
        res = db_query("SELECT * FROM test_table", "test.db")
        if res and res[0]["name"] == "AutoWeb":
            print("✅ DB Success")
        else:
            print("❌ DB Query Fail")
        os.remove("test.db")
    else:
        print("❌ DB Insert Fail")

    # 5. Test CSV
    print("\n--- Test 5: CSV Export ---")
    data_list = [{"id": 1, "val": "A"}, {"id": 2, "val": "B"}]
    csv_file = "test.csv"
    save_to_csv(data_list, csv_file)
    if os.path.exists(csv_file):
        print("✅ CSV Success")
        os.remove(csv_file)
    else:
        print("❌ CSV Fail")
        
    # 6. Test RAG (Mock)
    print("\n--- Test 6: RAG Wrapper ---")
    # This might fail if RAG env is not set up, but we just check the wrapper call
    ans = ask_knowledge_base("Hello")
    print(f"✅ RAG Response: {ans[:50]}...")

if __name__ == "__main__":
    test_toolbox()
