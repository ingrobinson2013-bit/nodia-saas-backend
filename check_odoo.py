import asyncio
from supabase import create_client
from config import settings
from domain.odoo_service import OdooService

async def main():
    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
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
            if any(x in name for x in ['state', 'status', 'show', 'active', 'allday']):
                print(f"{name}: {info.get('type')} - {info.get('string')}")
                if 'selection' in info:
                    print(f"  Selections: {info['selection']}")

if __name__ == "__main__":
    asyncio.run(main())
