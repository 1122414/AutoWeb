print("-> goto : https://www.baidu.com")
tab.get("https://www.baidu.com")
tab.wait(3)
print(f"-> Page title is now: {tab.title}")