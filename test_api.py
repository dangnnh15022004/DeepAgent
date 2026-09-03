import asyncio
import httpx

async def test_api():
    url = "https://dev-api-deeptrace.deepprotech.com/api/v1/product/search"
    
    # Token bạn vừa cung cấp
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkZWVwdHJhY2VfdXNlcklkIjoiZGE4MDJkNDktYjE0Ni00MzExLTk2YjQtMGE5NDIwYmFhNzkwIiwiZGVlcHRyYWNlX2VtYWlsIjoibXlwaGFtYXZhdGFydmlldG5hbUBnbWFpbC5jb20iLCJkZWVwdHJhY2Vfcm9sZSI6Ik1hbnVmYWN0dXJlciIsImV4cCI6MTc4Nzk5MjU4NSwiaXNzIjoiRGVlcFRyYWNlRGV2ZWxvcG1lbnQiLCJhdWQiOiJEZWVwVHJhY2VBdWRpZW5jZSJ9.zTW3q_69XokRC6ZR5T3lHyg46QrvVSUepu2f3QY4oko"

    # Bộ header ngụy trang y hệt Swagger UI
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Origin": "https://dev-api-deeptrace.deepprotech.com",
        "Referer": "https://dev-api-deeptrace.deepprotech.com/swagger/index.html",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive"
    }

    # Payload tìm kiếm sản phẩm "Body"
    payload = {
        "productName": "Body",
        "productId": "",
        "gtin": "",
        "gln": "",
        "sortOrder": ""
    }

    print(f"Đang gửi POST request tới: {url}")
    
    try:
        # Bỏ qua SSL (verify=False) như cấu hình hiện tại
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            
        print(f"\n--- KẾT QUẢ ---")
        print(f"HTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            print("Thành công! Dữ liệu trả về:")
            print(response.json())
        else:
            print("Lỗi! Nội dung trả về:")
            # In ra 500 ký tự đầu tiên nếu là trang HTML dài
            print(response.text[:500] + "..." if len(response.text) > 500 else response.text)
            
    except Exception as e:
        print(f"Lỗi Exception khi gọi API: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())