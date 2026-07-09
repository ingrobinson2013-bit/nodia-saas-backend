import asyncio
from supabase import create_client
from config import settings

async def main():
    supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    res = supabase.table("tenant_config").select("*").execute()
    print("Columns in tenant_config:", res.data[0].keys() if res.data else "No data")

if __name__ == "__main__":
    asyncio.run(main())
