import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/ws/live"
    try:
        async with websockets.connect(uri) as websocket:
            print(f"Connected to {uri}")
            # Wait for a potential message (like a GPS update we'll trigger)
            # In a separate process or task we'll send a GPS ping
            print("Waiting for messages...")
            while True:
                message = await websocket.recv()
                print(f"Received: {message}")
                data = json.loads(message)
                if data["event"] == "gps_update":
                    print("SUCCESS: Received GPS update!")
                    break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
