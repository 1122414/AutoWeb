results = []
items = tab.eles('x://div[@id=\'wrap\']/div[2]/div[3]/div[1]/div[1]/div[1]/ul[1]/div/div[1]/li[@class=\'job-card-box\']')
print(f"-> Found {len(items)} job cards")
for item in items:
    row = {}
    try:
        row["title"] = item.ele('x:.//div[@class=\'job-title clearfix\']/a[@class=\'job-name\']').text
    except:
        row["title"] = ""
    try:
        row["salary"] = item.ele('x:.//div[@class=\'job-title clearfix\']/span[@class=\'job-salary\']').text
    except:
        row["salary"] = ""
    try:
        tags = []
        tag_elements = item.eles('x:.//ul[@class=\'tag-list\']/li')
        for tag_el in tag_elements:
            tags.append(tag_el.text)
        row["tags"] = tags
    except:
        row["tags"] = []
    try:
        row["company"] = item.ele('x:.//div[@class=\'job-card-footer\']/a[@class=\'boss-info\']/span[@class=\'boss-name\']').text
    except:
        row["company"] = ""
    try:
        row["location"] = item.ele('x:.//div[@class=\'job-card-footer\']/span[@class=\'company-location\']').text
    except:
        row["location"] = ""
    try:
        row["link"] = item.ele('x:.//div[@class=\'job-title clearfix\']/a[@class=\'job-name\']').link
    except:
        row["link"] = ""
    if any(row.values()):
        results.append(row)
print(f"-> Total collected: {len(results)}")
toolbox.save_data(results, "output/job_data.json")