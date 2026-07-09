import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    
    print("Listing all tenants in database...")
    try:
        res = db.table("tenants").select("*").execute()
        if res.data:
            for t in res.data:
                print(f"ID: {t.get('tenant_id')}")
                print(f"  Nombre: {t.get('nombre')}")
                print(f"  wa_phone_id: {t.get('wa_phone_id')}")
                print(f"  waba_id: {t.get('waba_id')}")
                print(f"  odoo_db: {t.get('odoo_db')}")
                print(f"  odoo_url: {t.get('odoo_url')}")
                print(f"  activo: {t.get('activo')}")
                print("-" * 30)
        else:
            print("No tenants found!")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
