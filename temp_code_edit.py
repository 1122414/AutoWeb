print("-> Starting movie data extraction for vector database embedding")
print(f"-> Target URL: {tab.url}")

results = []

for i in range(1, 11):
    try:
        movie_card = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]")
        
        try:
            title = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[2]/a[1]/h2[1]").text
        except Exception as e:
            print(f"Warning: Title extraction failed for item {i} - {e}")
            title = ""
        
        try:
            categories_eles = tab.eles(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[2]/div[1]//span")
            categories = [cat.text for cat in categories_eles if cat.text]
        except Exception as e:
            print(f"Warning: Categories extraction failed for item {i} - {e}")
            categories = []
        
        try:
            region_duration = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[2]/div[2]").text
        except Exception as e:
            print(f"Warning: Region/Duration extraction failed for item {i} - {e}")
            region_duration = ""
        
        try:
            release_date = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[2]/div[3]/span[1]").text
        except Exception as e:
            print(f"Warning: Release date extraction failed for item {i} - {e}")
            release_date = ""
        
        try:
            score = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[3]/p[1]").text
        except Exception as e:
            print(f"Warning: Score extraction failed for item {i} - {e}")
            score = ""
        
        try:
            detail_link = tab.ele(f"x://*[@id='index']/div[1]/div[1]/div[{i}]/div[1]/div[1]/div[2]/a[1]").link
        except Exception as e:
            print(f"Warning: Detail link extraction failed for item {i} - {e}")
            detail_link = ""
        
        movie_data = {
            "title": title,
            "categories": categories,
            "region_duration": region_duration,
            "release_date": release_date,
            "score": score,
            "detail_link": detail_link
        }
        results.append(movie_data)
        print(f"-> Extracted movie {i}: {title}")
        
    except Exception as e:
        print(f"Warning: Failed to extract movie card {i} - {e}")

print(f"-> Total movies collected: {len(results)}")

toolbox.save_data(results, "output/movies.json")

print("-> Movie data saved. Ready for vector embedding and database storage.")