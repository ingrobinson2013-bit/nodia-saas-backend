import asyncio
import httpx
from supabase import create_client
from config import settings

async def main():
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    tenant_id = "d56faa0f-2909-439b-bed0-9d70aeee4bad"
    
    res = db.table("tenants").select("*").eq("tenant_id", tenant_id).execute()
    if not res.data:
        print("Tenant not found")
        return
        
    t = res.data[0]
    token = t.get("wa_access_token")
    phone_id = t.get("wa_phone_id")
    
    print(f"Testing access token for phone_id: {phone_id}")
    
    # 1. Test /me
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://graph.facebook.com/v19.0/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        print("--- GET /v19.0/me response ---")
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}")
        
    # 2. Test sending a message to a test number (e.g. Robinson's number 573235813942)
    async with httpx.AsyncClient() as client:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "573235813942",
            "type": "text",
            "text": {"body": "Test message from backend verification script"},
        }
        r = await client.post(
            f"https://graph.facebook.com/v19.0/{phone_id}/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )
        print("\n--- POST /messages response ---")
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}")

if __name__ == "__main__":
    asyncio.run(main())
