import asyncio
from supabase import create_client
from config import settings
from domain.odoo_service import OdooService

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    
    res = db.table("tenants").select("*").eq("tenant_id", tenant_id).execute()
    if res.data:
        tenant = res.data[0]
        odoo = OdooService(
            url=tenant["odoo_url"],
            db=tenant["odoo_db"],
            user=tenant["odoo_user"],
            api_key=tenant["odoo_api_key"]
        )
        try:
            # Query active products
            products = odoo._execute(
                "product.product", "search_read",
                [[["sale_ok", "=", True], ["active", "=", True]]],
                {"fields": ["id", "name", "lst_price"]}
            )
            print("\n--- Odoo Products/Services ---")
            for p in products:
                print(f"- {p.get('name')}: ${p.get('lst_price')} (id={p.get('id')})")
        except Exception as e:
            print("Error fetching products:", e)
            
        try:
            # Query professionals
            employees = odoo._execute(
                "hr.employee", "search_read",
                [[["active", "=", True]]],
                {"fields": ["id", "name"]}
            )
            print("\n--- Odoo Employees/Professionals ---")
            for e_row in employees:
                print(f"- {e_row.get('name')} (id={e_row.get('id')})")
        except Exception as e:
            print("Error fetching employees:", e)

if __name__ == "__main__":
    asyncio.run(main())
