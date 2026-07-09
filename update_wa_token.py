import asyncio
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    new_token = "EAAMZCVlpO3yABRj3HxxtriyA4t6hLSG8BOJAdSWFBztNe5K44ebfK3m9WJD0UIiEgWNS8DJeQSWmUN1XKAsudSjLIk5qtMGIXsFYPAu4LUPs2kJs5lujgJWrm9EsXlBp4cYrZCXm7A4VRB0KK5QufV8onaN17oZBVupknqyp91Cf4WJ8tS03MWTXZBRAwQZDZD"
    
    print(f"Updating wa_access_token for tenant {tenant_id}...")
    try:
        res = db.table("tenants").update({
            "wa_access_token": new_token
        }).eq("tenant_id", tenant_id).execute()
        
        if res.data:
            print("Successfully updated token in Supabase!")
            print(f"Verified Nombre: {res.data[0].get('nombre')}")
            print(f"Verified wa_phone_id: {res.data[0].get('wa_phone_id')}")
        else:
            print("Failed to update: Tenant row not found or database error.")
    except Exception as e:
        print("Error during update:", e)

if __name__ == "__main__":
    asyncio.run(main())
