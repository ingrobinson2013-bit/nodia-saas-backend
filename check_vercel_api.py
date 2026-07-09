import httpx

async def main():
    url = "https://nodia-saas-panel.vercel.app/api/admin/tenants"
    headers = {
        "x-admin-secret": "nodia_admin_2024"
    }
    print(f"Testing GET {url}...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.get(url, headers=headers)
            print(f"Status Code: {res.status_code}")
            if res.status_code == 200:
                data = res.json()
                print(f"Number of tenants returned: {len(data)}")
                for i, t in enumerate(data):
                    print(f"Tenant {i+1}: id={t.get('tenant_id')} name={t.get('nombre')}")
            else:
                print("Error Response:", res.text)
        except Exception as e:
            print("Error:", e)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
