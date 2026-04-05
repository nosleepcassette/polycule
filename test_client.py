#!/usr/bin/env python3
"""
Test client for Polycule Hub - simulates an agent connecting

Usage: python3 test_client.py [room_name]
"""
import asyncio
import json
import sys

async def run_client(room_name=None):
    """Connect to polycule hub"""
    print("🐙 Polycule Test Client")
    print("="*50)
    
    reader, writer = await asyncio.open_connection('localhost', 7777)
    print("✓ Connected to polycule hub")
    
    # Send handshake
    handshake = {
        'type': 'handshake',
        'name': 'TestAgent',
        'agent_type': 'test_agent',
        'room_name': room_name
    }
    writer.write((json.dumps(handshake) + '\n').encode())
    await writer.drain()
    print(f"✓ Sent handshake: {handshake['name']}")
    
    # Skip awaiting_room response
    await reader.readline()
    
    # Create/join room
    create_cmd = {
        'type': 'command',
        'command': 'create_room',
        'room_name': room_name or 'TestRoom'
    }
    writer.write((json.dumps(create_cmd) + '\n').encode())
    await writer.drain()
    print(f"✓ Created room: {create_cmd['room_name']}")
    
    # Read room response
    data = await reader.readline()
    response = json.loads(data.decode().strip())
    room_id = response['room']['room_id']
    print(f"✓ Room ID: {room_id}")
    
    # Send test messages
    print(f"\\n💬 Sending messages...")
    messages = [
        "TestAgent: Hello from tmux pane 2!",
        "TestAgent: This is a real-time chat demo.",
        "TestAgent: Messages route through the hub backend."
    ]
    
    for msg_text in messages:
        msg = {
            'type': 'message',
            'room_id': room_id,
            'content': msg_text
        }
        writer.write((json.dumps(msg) + '\n').encode())
        await writer.drain()
        print(f"→ Sent: {msg_text[:50]}...")
        await asyncio.sleep(0.5)
    
    print("\\n✅ Test complete!")
    writer.close()
    await writer.wait_closed()

if __name__ == "__main__":
    room = sys.argv[1] if len(sys.argv) > 1 else "DemoRoom"
    asyncio.run(run_client(room))