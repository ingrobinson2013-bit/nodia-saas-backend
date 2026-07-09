import asyncio
import httpx

async def main():
    token = "EAAMZCVlpO3yABRj3HxxtriyA4t6hLSG8BOJAdSWFBztNe5K44ebfK3m9WJD0UIiEgWNS8DJeQSWmUN1XKAsudSjLIk5qtMGIXsFYPAu4LUPs2kJs5lujgJWrm9EsXlBp4cYrZCXm7A4VRB0KK5QufV8onaN17oZBVupknqyp91Cf4WJ8tS03MWTXZBRAwQZDZD"
    phone_id = "332957319891158"
    
    print(f"Testing access token for NEW phone_id: {phone_id}")
    
    # Send a test message to Robinson
    async with httpx.AsyncClient() as client:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "573235813942",
            "type": "text",
            "text": {"body": "¡Hola, Robinson! Prueba de integración para el bot de ventas de BeautySync Pro. 🚀"},
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
