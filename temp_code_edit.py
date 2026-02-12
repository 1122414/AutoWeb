results = []
page_num = 1

while True:
    print(f"-> Processing page {page_num}")
    
    # Find all news items on current page
    news_items = tab.eles("x://div[@id='ctl00_ctl41_g_7de359f1_b708_4e31_8bbd_2cd87ec3690c']/div[2]/div[1]/div")
    
    print(f"-> Found {len(news_items)} items on page {page_num}")
    
    for item in news_items:
        try:
            title = item.ele("x:.//h4[@class='titlenews']/a").text
        except:
            title = ""
        
        try:
            date = item.ele("x:.//h4[@class='titlenews']/i[@class='datetime']").text
        except:
            date = ""
        
        try:
            summary = item.ele("x:.//p").text
        except:
            summary = ""
        
        try:
            link = item.ele("x:.//h4[@class='titlenews']/a").link
        except:
            link = ""
        
        results.append({
            "title": title,
            "date": date,
            "summary": summary,
            "link": link
        })
    
    # Try to find next page button
    try:
        next_page_elements = tab.eles("x://div[@id='ctl00_ctl41_g_7de359f1_b708_4e31_8bbd_2cd87ec3690c']/div[3]//ul[@class='pagination']/li/a")
        next_page_clicked = False
        
        for next_btn in next_page_elements:
            # Check if this is a next page link (not current page)
            if "active" not in next_btn.parent().attr("class", ""):
                btn_text = next_btn.text.strip()
                if btn_text.isdigit() and int(btn_text) > page_num:
                    print(f"-> Clicking next page: {btn_text}")
                    next_btn.click(by_js=True)
                    tab.wait(2)
                    page_num += 1
                    next_page_clicked = True
                    break
        
        if not next_page_clicked:
            print("-> No more pages to navigate")
            break
            
    except Exception as e:
        print(f"-> Pagination ended: {e}")
        break

print(f"-> Total collected: {len(results)} items")
toolbox.save_data(results, "output/search_results.csv")