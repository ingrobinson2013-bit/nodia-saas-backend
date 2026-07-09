import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    
    print(f"Fetching tenant_config for {tenant_id}...")
    try:
        res = db.table("tenant_config").select("*").eq("tenant_id", tenant_id).execute()
        if res.data:
            c = res.data[0]
            for k, v in c.items():
                print(f"{k}: {v}")
        else:
            print("No tenant_config found!")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
