# LiveKit AI Agent Simulator

This is a basic implementation of a simulated AI agent using LiveKit. The agent can join a room and respond to basic commands.

## Setup

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file with your LiveKit credentials:
```
LIVEKIT_API_KEY=your_api_key
LIVEKIT_API_SECRET=your_api_secret
LIVEKIT_URL=your_livekit_url
```

3. Run the agent:
```bash
python ai_agent.py
```

## Features

- Connects to a LiveKit room
- Responds to basic text commands
- Simulates AI processing with delays
- Handles room events and participant interactions

## Usage

Once the agent is running, it will join the specified room and respond to messages. You can interact with it through the LiveKit room interface. 