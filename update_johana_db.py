import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    
    print(f"Verifying tenant {tenant_id}...")
    try:
        res = db.table("tenants").select("*").eq("tenant_id", tenant_id).execute()
        if res.data:
            t = res.data[0]
            print("Row details in Supabase:")
            print(f"Name: {t.get('nombre')}")
            print(f"wa_phone_id: {t.get('wa_phone_id')}")
            print(f"waba_id: {t.get('waba_id')}")
        else:
            print("Tenant not found!")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
