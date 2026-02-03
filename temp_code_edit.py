results = []
page_num = 1
while True:
    print(f"-> Processing page {page_num}")
    try:
        player_elements = tab.eles("x://div[@id='players']/div[@class='el-col el-col-18 el-col-offset-3']/div[@class='el-row']/div")
        if not player_elements:
            print("-> No player elements found, stopping pagination.")
            break
        print(f"-> Found {len(player_elements)} players on page {page_num}")
        for idx, player in enumerate(player_elements):
            try:
                player_data = {}
                # Extract name
                name_ele = player.ele("x:.//h3[@class='name']")
                if name_ele:
                    player_data["name"] = name_ele.text
                else:
                    print(f"-> Warning: Name not found for player {idx + 1} on page {page_num}")
                # Extract height
                height_ele = player.ele("x:.//p[@class='weight']/span")
                if height_ele:
                    player_data["height"] = height_ele.text
                else:
                    print(f"-> Warning: Height not found for player {idx + 1} on page {page_num}")
                # Extract weight
                weight_ele = player.ele("x:.//p[@class='weight']/span")
                if weight_ele:
                    player_data["weight"] = weight_ele.text
                else:
                    print(f"-> Warning: Weight not found for player {idx + 1} on page {page_num}")
                # Extract image
                image_ele = player.ele("x:.//img[@class='image']")
                if image_ele:
                    img_url = image_ele.link
                    if img_url:
                        player_data["image"] = img_url
                        # Download image using toolbox
                        filename = f"output/images/{player_data.get('name', 'unknown')}_{idx + 1}.jpg"
                        toolbox.download_file(img_url, filename)
                        print(f"-> Downloaded image for {player_data.get('name', 'unknown')}: {filename}")
                else:
                    print(f"-> Warning: Image not found for player {idx + 1} on page {page_num}")
                results.append(player_data)
                print(f"-> Collected data for player {idx + 1}: {player_data}")
            except Exception as e:
                print(f"-> Error extracting data for player {idx + 1} on page {page_num}: {e}")
                continue
        # Attempt to go to next page
        try:
            next_button = tab.ele("x://button[contains(@class, 'btn-next')]")
            if next_button and next_button.states.is_enabled:
                print("-> Clicking next page button")
                next_button.click(by_js=True)
                tab.wait.load_start()
                page_num += 1
            else:
                print("-> Next button disabled or not found, ending pagination")
                break
        except Exception as e:
            print(f"-> Pagination ended or failed: {e}")
            break
    except Exception as e:
        print(f"-> Critical error on page {page_num}: {e}")
        break

print(f"-> Total players collected: {len(results)}")
toolbox.save_data(results, "output/players.json")