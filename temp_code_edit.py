results = []
current_page = 1
max_pages = 2

print(f"-> Starting batch extraction, target: {max_pages} pages")

while current_page <= max_pages:
    print(f"-> Processing page {current_page}")
    
    # Wait for page to load
    tab.wait(2)
    
    # Get all movie items on current page
    try:
        movie_items = tab.eles("x://div[@class='module-items ']//a[@class='module-poster-item module-item']")
        total_items = len(movie_items)
        print(f"-> Found {total_items} movies on page {current_page}")
    except Exception as e:
        print(f"Warning: Failed to get movie items: {e}")
        break
    
    # Loop through each movie using index (stale element protection)
    for i in range(total_items):
        print(f"-> Processing movie {i+1}/{total_items} on page {current_page}")
        
        try:
            # Re-get the item list and target specific index
            movie_items = tab.eles("x://div[@class='module-items ']//a[@class='module-poster-item module-item']")
            if i >= len(movie_items):
                print(f"Warning: Index {i} out of range, skipping")
                continue
            
            item = movie_items[i]
            
            # Extract basic info from list page
            title = item.attr('title') or ""
            detail_url = item.attr('href') or ""
            poster_url = ""
            status = ""
            
            try:
                poster_img = item.ele("x://div[contains(@class,'module-item-pic')]/img")
                poster_url = poster_img.attr('src') or poster_img.attr('data-src') or ""
            except Exception as e:
                print(f"Warning: Failed to get poster: {e}")
            
            try:
                status_ele = item.ele("x://div[contains(@class,'module-item-note')]")
                status = status_ele.text if status_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get status: {e}")
            
            print(f"-> Clicking into detail page: {title}")
            
            # Click to enter detail page
            item.click(by_js=True)
            tab.wait(2)
            
            # Extract detail page information
            movie_data = {
                "movie_name": "",
                "poster_image": poster_url,
                "year": "",
                "area": "",
                "type": "",
                "alias": "",
                "update_info": "",
                "note": "",
                "douban_link": "",
                "play_link": "",
                "detail_url": detail_url
            }
            
            try:
                title_ele = tab.ele("x://div[@class='module-info-heading']/h1/span")
                movie_data["movie_name"] = title_ele.text if title_ele else title
            except Exception as e:
                print(f"Warning: Failed to get movie name: {e}")
                movie_data["movie_name"] = title
            
            try:
                poster_ele = tab.ele("x://div[@class='module-info-poster']//img")
                if poster_ele:
                    movie_data["poster_image"] = poster_ele.attr('src') or poster_ele.attr('data-src') or poster_url
            except Exception as e:
                print(f"Warning: Failed to get detail poster: {e}")
            
            try:
                year_ele = tab.ele("x://div[@class='module-info-tag']/div[1]//a")
                movie_data["year"] = year_ele.text if year_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get year: {e}")
            
            try:
                area_ele = tab.ele("x://div[@class='module-info-tag']/div[2]//a")
                movie_data["area"] = area_ele.text if area_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get area: {e}")
            
            try:
                genre_ele = tab.ele("x://div[@class='module-info-tag']/div[3]//a")
                movie_data["type"] = genre_ele.text if genre_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get genre: {e}")
            
            try:
                alias_ele = tab.ele("x://div[@class='module-info-items']/div[contains(@class,'module-info-item')][span[text()='别名Alias：']]/div[@class='module-info-item-content']")
                movie_data["alias"] = alias_ele.text if alias_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get alias: {e}")
            
            try:
                update_ele = tab.ele("x://div[@class='module-info-items']/div[contains(@class,'module-info-item')][span[text()='更新：']]/div[@class='module-info-item-content']")
                movie_data["update_info"] = update_ele.text if update_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get update info: {e}")
            
            try:
                note_ele = tab.ele("x://div[@class='module-info-items']/div[contains(@class,'module-info-item')][span[text()='备注：']]/div[@class='module-info-item-content']")
                movie_data["note"] = note_ele.text if note_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get note: {e}")
            
            try:
                douban_ele = tab.ele("x://div[@class='module-info-items']/div[contains(@class,'module-info-item')][span[text()='豆瓣：']]/a")
                movie_data["douban_link"] = douban_ele.attr('href') if douban_ele else ""
            except Exception as e:
                print(f"Warning: Failed to get douban link: {e}")
            
            try:
                play_eles = tab.eles("x://div[@class='module-play-list']//a[@class='module-play-list-link']")
                play_links = [ele.attr('href') for ele in play_eles if ele.attr('href')]
                movie_data["play_link"] = "|".join(play_links) if play_links else ""
            except Exception as e:
                print(f"Warning: Failed to get play links: {e}")
            
            results.append(movie_data)
            print(f"-> Collected: {movie_data['movie_name']}")
            
            # Go back to list page
            print(f"-> Going back to list page")
            tab.back()
            tab.wait(2)
            
        except Exception as e:
            print(f"Warning: Error processing movie {i+1}: {e}")
            # Try to go back to list page
            try:
                tab.back()
                tab.wait(2)
            except:
                pass
            continue
    
    # Check if we need to go to next page
    if current_page < max_pages:
        try:
            print(f"-> Clicking next page")
            next_page_btn = tab.ele("x://*[@id='page']/a[@class='page-link page-next']")
            if next_page_btn and next_page_btn.states.is_displayed:
                next_page_btn.click(by_js=True)
                tab.wait(2)
                current_page += 1
            else:
                print(f"-> No next page button found, ending")
                break
        except Exception as e:
            print(f"Warning: Failed to click next page: {e}")
            break
    else:
        print(f"-> Reached max pages ({max_pages}), ending")
        break

print(f"-> Total collected: {len(results)} movies")
toolbox.save_data(results, "output/movies.csv")