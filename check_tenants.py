import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    print("Checking tenants table...")
    try:
        res = db.table("tenants").select("*").execute()
        print(f"Total tenants: {len(res.data)}")
        for t in res.data:
            print(f"Tenant: id={t.get('tenant_id')} name={t.get('nombre')} plan={t.get('plan')}")
    except Exception as e:
        print("Error reading tenants:", e)

if __name__ == "__main__":
    asyncio.run(main())
