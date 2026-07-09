import asyncio
import httpx

async def main():
    token = "EAAMZCVlpO3yABRj3HxxtriyA4t6hLSG8BOJAdSWFBztNe5K44ebfK3m9WJD0UIiEgWNS8DJeQSWmUN1XKAsudSjLIk5qtMGIXsFYPAu4LUPs2kJs5lujgJWrm9EsXlBp4cYrZCXm7A4VRB0KK5QufV8onaN17oZBVupknqyp91Cf4WJ8tS03MWTXZBRAwQZDZD"
    
    async with httpx.AsyncClient() as client:
        # Query businesses associated with the token/system user
        print("Querying /me/businesses...")
        r = await client.get(
            "https://graph.facebook.com/v19.0/me/businesses",
            headers={"Authorization": f"Bearer {token}"}
        )
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}\n")
        
        # Also query /me/accounts
        print("Querying /me/accounts...")
        r = await client.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            headers={"Authorization": f"Bearer {token}"}
        )
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}\n")

if __name__ == "__main__":
    asyncio.run(main())
