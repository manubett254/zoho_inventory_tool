import requests

TOKEN_FILE = "token.txt"

url = "https://accounts.zoho.com/oauth/v2/token"
data = {
    "refresh_token": "1000.7ef7d22e9eb986a7b3df24e422c544e3.5fb7ee8ea5a732bc8a7e4fa6f6227a5d",
    "client_id": "1000.9HAW7XM2ZUL7MT9YGWKK1H37P5H82F",
    "client_secret": "a3c46f1a679f274cf7cc956ee90a4fb5938498180f",
    "grant_type": "refresh_token"
}

res = requests.post(url, data=data)
if res.status_code == 200:
    token_data = res.json()
    access_token = token_data.get("access_token")
    if access_token:
        with open(TOKEN_FILE, "w") as f:
            f.write(access_token)
        print(f"✅ New token saved to {TOKEN_FILE}")
        print(f"   Token: {access_token[:12]}...")
    else:
        print("❌ No access_token in response:")
        print(res.json())
else:
    print(f"❌ Refresh failed ({res.status_code}):")
    print(res.text)