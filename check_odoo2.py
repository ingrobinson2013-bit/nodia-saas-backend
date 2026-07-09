import asyncio
from supabase import create_client
from domain.odoo_service import OdooService
import os

async def main():
    with open(".env", "r") as f:
        env = {}
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
                
    supabase = create_client(env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"])
    res = supabase.table("tenants").select("*").eq("odoo_db", "beautysync_showcase").execute()
    if res.data:
        tenant = res.data[0]
        odoo = OdooService(
            url=tenant["odoo_url"],
            db=tenant["odoo_db"],
            user=tenant["odoo_user"],
            api_key=tenant["odoo_api_key"]
        )
        fields = odoo._execute("calendar.event", "fields_get", [])
        for name, info in fields.items():
            if any(x in name for x in ['prof', 'emp', 'barb', 'staff', 'user', 'res_partner']) or 'Profesional' in info.get('string', ''):
                print(f"{name}: {info.get('type')} - {info.get('string')}")
                if 'selection' in info:
                    print(f"  Selections: {info['selection']}")
                if 'relation' in info:
                    print(f"  Relation: {info['relation']}")

if __name__ == "__main__":
    asyncio.run(main())
