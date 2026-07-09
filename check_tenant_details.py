import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    print("Fetching tenant Johana Calle details...")
    try:
        res = db.table("tenants").select("*").eq("nombre", "Johana Calle Beauty Salon").execute()
        if res.data:
            t = res.data[0]
            print("--- Tenant Data ---")
            print(f"ID: {t.get('tenant_id')}")
            print(f"Nombre: {t.get('nombre')}")
            print(f"wa_phone_id: {t.get('wa_phone_id')}")
            print(f"wa_access_token: {t.get('wa_access_token')[:20] if t.get('wa_access_token') else 'None'}...")
            print(f"odoo_url: {t.get('odoo_url')}")
            print(f"odoo_db: {t.get('odoo_db')}")
            print(f"odoo_user: {t.get('odoo_user')}")
            print(f"odoo_api_key: {t.get('odoo_api_key')[:5] if t.get('odoo_api_key') else 'None'}...")
            print(f"waba_id: {t.get('waba_id')}")
            
            # Check config too
            res_config = db.table("tenant_config").select("*").eq("tenant_id", t.get('tenant_id')).execute()
            if res_config.data:
                c = res_config.data[0]
                print("\n--- Config Data ---")
                print(f"Dirección: {c.get('direccion')}")
                print(f"Horario: {c.get('horario')}")
                print(f"Prompt Extra: {c.get('system_prompt_extra')}")
            else:
                print("\n⚠️ No tenant_config found for this tenant!")
        else:
            print("❌ Tenant 'Johana Calle Beauty Salon' not found in database!")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
