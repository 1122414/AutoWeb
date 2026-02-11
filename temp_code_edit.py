print("-> Starting search for 'sea'")
search_input = tab.ele("#txtInput")
search_input.input("sea")
print("-> Input 'sea' in search box")
search_btn = tab.ele("#btnSearch")
search_btn.click(by_js=True)
tab.wait(3)
print(f"-> Search submitted, current URL: {tab.url}")

results = []
page_num = 1

while True:
    print(f"-> Processing page {page_num}")

    try:
        items = tab.eles(
            "x://*[@id='ctl00_ctl41_g_7de359f1_b708_4e31_8bbd_2cd87ec3690c']/div[2]/div[1]/div/div")
        print(f"-> Found {len(items)} items on page {page_num}")
    except Exception as e:
        print(f"Warning: Failed to get items - {e}")
        break

    for idx in range(len(items)):
        try:
            items = tab.eles(
                "x://*[@id='ctl00_ctl41_g_7de359f1_b708_4e31_8bbd_2cd87ec3690c']/div[2]/div[1]/div/div")
            item = items[idx]

            data = {}

            try:
                title_ele = item.ele("x:.//h4[@class='titlenews']/a")
                data["title"] = title_ele.text
                data["link"] = title_ele.link
            except Exception as e:
                print(f"Warning: Title extraction failed - {e}")
                data["title"] = ""
                data["link"] = ""

            try:
                data["date"] = item.ele("x:.//i[@class='datetime']").text
            except Exception as e:
                data["date"] = ""

            try:
                data["summary"] = item.ele("x:.//p").text
            except Exception as e:
                data["summary"] = ""

            try:
                img_src = item.ele(
                    "x:.//img[@class='img-reponsive']").attr("src")
                data["image"] = img_src if img_src else ""
            except Exception as e:
                data["image"] = ""

            results.append(data)
        except Exception as e:
            print(f"Warning: Item {idx} extraction failed - {e}")

    print(f"-> Page {page_num} completed, total collected: {len(results)}")

    try:
        next_page = page_num + 1
        next_link = tab.ele(
            f"x://*[@id='ctl00_ctl41_g_7de359f1_b708_4e31_8bbd_2cd87ec3690c']/div[3]/div[1]/div[1]/ul[1]/li/a[@href='?keyword=sea&p={next_page}']")

        if next_link and next_link.states.is_displayed:
            next_link.click(by_js=True)
            tab.wait(3)
            page_num += 1
            print(f"-> Navigated to page {page_num}")
        else:
            print("-> No more pages available")
            break
    except Exception as e:
        print(f"-> Pagination ended: {e}")
        break

print(f"-> All pages processed. Total results: {len(results)}")
toolbox.save_data(results, "output/mard_sea_search_results.csv")
