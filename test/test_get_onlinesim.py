import requests

url = "https://onlinesim.io/api/getTariffs.php"
response = requests.get(url, timeout=10)

print(response.status_code, response.text)
