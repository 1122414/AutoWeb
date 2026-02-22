results = []
categories = tab.eles('x://div[@class="container content"]//h5[@class="tit"]')
print(f"-> Found {len(categories)} categories")

for category in categories:
    try:
        category_name = category.ele('x://b').text
    except:
        try:
            category_name = category.text
        except:
            category_name = ""
    
    print(f"-> Processing category: {category_name}")
    
    try:
        tools_container = category.ele('x://following-sibling::div[@class="cardlk"][1]')
        tool_items = tools_container.eles('x://div[@class="col"]/a[not(@class="gl") and not(@class="gl tip")]')
        
        for tool_item in tool_items:
            row = {}
            
            try:
                row["title"] = tool_item.text
            except:
                row["title"] = ""
            
            try:
                row["link"] = tool_item.attr('href')
            except:
                row["link"] = ""
            
            try:
                row["description"] = tool_item.ele('x://p').text
            except:
                row["description"] = ""
            
            try:
                row["category"] = category_name
            except:
                row["category"] = ""
            
            if any(row.values()):
                results.append(row)
                
    except Exception as e:
        print(f"-> Error processing category {category_name}: {e}")
        continue

print(f"-> Total collected: {len(results)} tools")
toolbox.save_data(results, "output/tools.json")