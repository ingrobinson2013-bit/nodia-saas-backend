import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    
    print(f"Fetching tenant {tenant_id}...")
    try:
        res = db.table("tenants").select("*").eq("tenant_id", tenant_id).execute()
        if res.data:
            t = res.data[0]
            token = t.get("wa_access_token")
            if token:
                print(f"Token Length: {len(token)}")
                print(f"Token Starts with: {token[:20]}")
                print(f"Token Ends with: {token[-20:]}")
                print(f"Token contains whitespace? {' ' in token or '\n' in token or '\r' in token}")
            else:
                print("Token is None/empty!")
        else:
            print("Tenant not found!")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
