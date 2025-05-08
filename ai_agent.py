import os
import asyncio
import logging
import sys
import time
import json
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

# Make sure PyJWT is installed for token generation
try:
    import jwt
except ImportError:
    print("Installing PyJWT...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "PyJWT"], check=True)
    import jwt

# Import the LiveKit client SDK
try:
    from livekit import rtc
except ImportError:
    print("Installing livekit package...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "livekit"], check=True)
    from livekit import rtc

# Make sure Firebase Admin SDK is installed
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
except ImportError:
    print("Installing Firebase Admin SDK...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "firebase-admin"], check=True)
    import firebase_admin
    from firebase_admin import credentials, firestore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LIVEKIT_URL = "wss://frontdesk-nn9xoxnq.livekit.cloud"
API_KEY = "APIbNaEa2tGC2bd"
API_SECRET = "6TyFLdtDff4TBekvz3VUnI9q0s1zDFBhWgIk2Ie7g7cA"
ROOM_NAME = "salon"
IDENTITY = "salon-agent"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("livekit_agent")

# ---------------------------------------------------------------------------
# JWT Token Implementation
# ---------------------------------------------------------------------------

def create_access_token(identity: str = IDENTITY) -> str:
    """Return a signed JWT granting access to the room."""
    now = int(time.time())
    payload = {
        "exp": now + 86400,  # 24 hours
        "iss": API_KEY,
        "nbf": now,
        "sub": identity,
        "video": {
            "room": ROOM_NAME,
            "roomJoin": True
        }
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")

# ---------------------------------------------------------------------------
# HelpRequest Implementation
# ---------------------------------------------------------------------------

class HelpRequest:
    def __init__(self, question: str, caller_id: str):
        self.id = str(uuid.uuid4())
        self.question = question
        self.caller_id = caller_id
        self.status = "pending"
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.timeout_at = self.created_at + timedelta(minutes=30)  # 30-minute timeout

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "caller_id": self.caller_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

# ---------------------------------------------------------------------------
# Simple Agent Implementation
# ---------------------------------------------------------------------------

class SimpleAgent:
    def __init__(self):
        self.room = rtc.Room()
        self.is_connected = False
        
        # Initialize Firebase
        try:
            # Check if Firebase is already initialized
            firebase_admin.get_app()
        except ValueError:
            # Initialize Firebase with service account
            cred = credentials.Certificate('service.json')
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
        self.help_requests_ref = self.db.collection('help_requests')
        self.knowledge_base_ref = self.db.collection('knowledge_base')
        
        # Load knowledge base from Firebase
        self.knowledge_base = self.load_knowledge_base()
        
        # Create a queue for pending responses
        self.response_queue = asyncio.Queue()
        
        # Set up listener for resolved requests
        self.setup_resolved_requests_listener()

    def load_knowledge_base(self) -> dict:
        """Load knowledge base from Firebase."""
        knowledge_base = {}
        docs = self.knowledge_base_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            knowledge_base[doc.id] = data['answer']
        return knowledge_base

    def save_knowledge_base_entry(self, key: str, answer: str):
        """Save a knowledge base entry to Firebase."""
        self.knowledge_base_ref.document(key).set({
            'answer': answer,
            'updated_at': datetime.utcnow().isoformat()
        })

    def setup_resolved_requests_listener(self):
        """Set up a listener for resolved help requests to update knowledge base."""
        def on_snapshot(doc_snapshot, changes, read_time):
            for change in changes:
                if change.type.name == 'MODIFIED':
                    doc = change.document
                    data = doc.to_dict()
                    if data.get('status') == 'resolved' and 'answer' in data:
                        self.update_knowledge_base(data['question'], data['answer'])
                        # Add response to queue instead of creating task directly
                        self.response_queue.put_nowait({
                            "text": data['answer'],
                            "caller_id": data['caller_id']
                        })

        # Watch the collection for changes
        self.help_requests_ref.on_snapshot(on_snapshot)

    def update_knowledge_base(self, question: str, answer: str):
        """Update the knowledge base with new information."""
        # Convert question to lowercase for matching
        question_lower = question.lower()
        
        # Extract meaningful key phrases from the question
        words = question_lower.split()
        
        # Remove common question words
        question_words = {'what', 'when', 'where', 'who', 'why', 'how', 'do', 'does', 'is', 'are', 'can', 'could', 'would', 'should'}
        meaningful_words = [w for w in words if w not in question_words]
        
        if len(meaningful_words) >= 2:
            # Use the first two meaningful words as the key
            key = ' '.join(meaningful_words[:2])
        elif meaningful_words:
            # If only one meaningful word, use it with the first question word
            key = f"{words[0]} {meaningful_words[0]}"
        else:
            # Fallback to first two words if no meaningful words found
            key = ' '.join(words[:2])
        
        # Update or create knowledge base entry
        self.knowledge_base[key] = answer
        self.save_knowledge_base_entry(key, answer)
        logger.info(f"Updated knowledge base entry for '{key}'")

    async def connect(self):
        """Connect to LiveKit room."""
        try:
            auth_token = create_access_token()
            logger.info("Connecting to LiveKit room...")
            
            options = rtc.RoomOptions()
            options.auto_subscribe = True
            
            await self.room.connect(LIVEKIT_URL, auth_token, options)
            self.is_connected = True
            logger.info("Successfully connected to LiveKit room")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from the room."""
        if self.is_connected:
            await self.room.disconnect()
            self.is_connected = False
            logger.info("Disconnected from room")

    async def create_help_request(self, question: str, caller_id: str) -> HelpRequest:
        """Create a new help request in Firebase and notify supervisor."""
        help_request = HelpRequest(question, caller_id)
        
        # Store in Firebase
        try:
            self.help_requests_ref.document(help_request.id).set(help_request.to_dict())
            logger.info(f"Help request stored in Firebase with ID: {help_request.id}")
        except Exception as e:
            logger.error(f"Failed to store help request in Firebase: {e}")
            raise
        
        # Simulate texting supervisor
        logger.info(f"SUPERVISOR NOTIFICATION: Hey, I need help answering: '{question}'")
        
        return help_request

    def setup_handlers(self):
        """Set up event handlers for the room."""
        @self.room.on("participant_joined")
        def _on_participant_joined(participant):
            logger.info(f"Call received from: {participant.identity}")
            # Send welcome message
            asyncio.create_task(
                self.room.local_participant.publish_data(
                    json.dumps({"text": "Hello! I'm the salon assistant. How can I help you today?"}).encode(),
                    reliable=True
                )
            )
            logger.info("Welcome message sent")

        @self.room.on("participant_left")
        def _on_participant_left(participant):
            logger.info(f"Participant left: {participant.identity}")

        @self.room.on("data_received")
        def _on_data(data):
            try:
                message_bytes = data.data
                message = message_bytes.decode('utf-8')
                logger.info(f"Received message: {message}")
                
                # Process the message asynchronously
                async def process_and_respond():
                    try:
                        response = await self._process_message(message)
                        logger.info(f"Generated response: {response}")
                        
                        # Send response
                        await self.room.local_participant.publish_data(
                            json.dumps({"text": response}).encode(),
                            reliable=True
                        )
                        logger.info("Response sent")
                    except Exception as e:
                        logger.error(f"Error in async processing: {e}")

                # Create task for async processing
                asyncio.create_task(process_and_respond())
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")

        @self.room.on("disconnected")
        def _on_disconnected():
            logger.info("Disconnected from room")

    async def _process_message(self, message: str) -> str:
        """Process incoming message and return appropriate response."""
        try:
            data = json.loads(message)
            query = data.get("text", "").lower()
            caller_id = data.get("caller_id", "unknown")
            logger.info(f"Processing query: {query}")
            
            # Check if we know the answer
            best_match = None
            best_match_score = 0
            
            for key, answer in self.knowledge_base.items():
                # Calculate match score based on key presence and position
                if key in query:
                    # Give higher score if key is at the start of the query
                    score = 1.0 if query.startswith(key) else 0.5
                    # Give higher score for longer matching keys
                    score *= len(key) / len(query)
                    if score > best_match_score:
                        best_match_score = score
                        best_match = answer
            
            # Only return a match if it's a strong match (score > 0.3)
            if best_match and best_match_score > 0.3:
                logger.info(f"Found matching answer with score: {best_match_score}")
                return best_match
            
            # If we don't know the answer, create help request
            try:
                help_request = await self.create_help_request(query, caller_id)
                logger.info(f"Created help request: {help_request.to_dict()}")
                return "Let me check with my supervisor and get back to you."
            except Exception as e:
                logger.error(f"Failed to create help request: {e}")
                return "I apologize, but I'm having trouble processing your request right now. Please try again in a moment."
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON message received")
            return "I couldn't understand that message. Please try again."
        except Exception as e:
            logger.error(f"Error in message processing: {e}")
            return "I encountered an error processing your message. Please try again."

    async def process_response_queue(self):
        """Process responses from the queue."""
        while self.is_connected:
            try:
                response = await self.response_queue.get()
                await self.room.local_participant.publish_data(
                    json.dumps(response).encode(),
                    reliable=True
                )
                logger.info(f"Sent response to customer: {response['text']}")
            except Exception as e:
                logger.error(f"Error processing response queue: {e}")
            await asyncio.sleep(0.1)  # Small delay to prevent CPU spinning

    async def run(self):
        """Main run loop for the agent."""
        try:
            if await self.connect():
                self.setup_handlers()
                logger.info("Agent is live – press Ctrl+C to exit.")
                
                # Start the response queue processor
                response_processor = asyncio.create_task(self.process_response_queue())
                
                while self.is_connected:
                    await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down agent...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            await self.disconnect()

    async def check_timeouts(self):
        """Check for timed out requests."""
        while self.is_connected:
            try:
                # Get all pending requests
                pending_requests = self.help_requests_ref.where('status', '==', 'pending').stream()
                now = datetime.utcnow()
                
                for doc in pending_requests:
                    data = doc.to_dict()
                    timeout_at = datetime.fromisoformat(data['timeout_at'])
                    
                    if now > timeout_at:
                        # Mark as unresolved
                        doc.reference.update({
                            'status': 'unresolved',
                            'updated_at': now.isoformat()
                        })
                        # Notify customer
                        await self.room.local_participant.publish_data(
                            json.dumps({
                                "text": "I apologize, but I haven't received a response from my supervisor yet. Please try asking your question again or contact us directly.",
                                "caller_id": data['caller_id']
                            }).encode(),
                            reliable=True
                        )
                
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error checking timeouts: {e}")

# ---------------------------------------------------------------------------
# Interactive Client Implementation
# ---------------------------------------------------------------------------

class InteractiveClient:
    def __init__(self):
        self.room = rtc.Room()
        self.is_connected = False

    async def connect(self):
        """Connect to LiveKit room."""
        try:
            token = create_access_token("interactive-client")
            logger.info("Connecting to LiveKit room...")
            
            options = rtc.RoomOptions()
            options.auto_subscribe = True
            
            await self.room.connect(LIVEKIT_URL, token, options)
            self.is_connected = True
            logger.info("Successfully connected to LiveKit room")
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def setup_handlers(self):
        """Set up event handlers for the room."""
        @self.room.on("participant_joined")
        def _on_participant_joined(participant):
            logger.info(f"Participant joined: {participant.identity}")

        @self.room.on("participant_left")
        def _on_participant_left(participant):
            logger.info(f"Participant left: {participant.identity}")

        @self.room.on("data_received")
        def _on_data(data):
            try:
                message_bytes = data.data
                message = message_bytes.decode('utf-8')
                logger.info(f"Agent response: {message}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")

        @self.room.on("disconnected")
        def _on_disconnected():
            logger.info("Disconnected from room")

    async def send_message(self, text: str):
        """Send a message to the room."""
        if not self.is_connected:
            logger.error("Not connected to room")
            return

        message = json.dumps({"text": text})
        try:
            await self.room.local_participant.publish_data(message.encode(), reliable=True)
            logger.info(f"Sent message: {text}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    async def run(self):
        """Main run loop for the interactive client."""
        try:
            if await self.connect():
                self.setup_handlers()
                
                # Wait a moment for room to stabilize
                await asyncio.sleep(1)
                
                print("\nWelcome to the Salon Chat Client!")
                print("Type your questions about the salon (type 'exit' to quit)")
                print("Example questions:")
                print("- What are your hours?")
                print("- Where are you located?")
                print("- How much is a basic haircut?")
                print("- What's the price for coloring?")
                print("- Do you offer massages?")
                print("\n")

                while True:
                    # Get user input
                    question = input("Your question: ").strip()
                    
                    if question.lower() == 'exit':
                        break
                        
                    if question:
                        await self.send_message(question)
                        # Wait a moment for response
                        await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Shutting down client...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            if self.is_connected:
                await self.room.disconnect()
                logger.info("Disconnected from room")

class SalonChat:
    def __init__(self):
        self.agent = SimpleAgent()
        self.client = InteractiveClient()
        self.is_running = False

    async def start(self):
        """Start both agent and client."""
        try:
            # Start the agent first
            if await self.agent.connect():
                self.agent.setup_handlers()
                logger.info("Agent is live")
                
                # Then start the client
                if await self.client.connect():
                    self.client.setup_handlers()
                    self.is_running = True
                    
                    # Wait a moment for room to stabilize
                    await asyncio.sleep(1)
                    
                    print("\nWelcome to the Salon Chat!")
                    print("Type your questions about the salon (type 'exit' to quit)")
                    print("Example questions:")
                    print("- What are your hours?")
                    print("- Where are you located?")
                    print("- How much is a basic haircut?")
                    print("- What's the price for coloring?")
                    print("- Do you offer massages?")
                    print("\n")

                    while self.is_running:
                        # Get user input
                        question = input("Your question: ").strip()
                        
                        if question.lower() == 'exit':
                            break
                            
                        if question:
                            await self.client.send_message(question)
                            # Wait a moment for response
                            await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            await self.cleanup()

    async def cleanup(self):
        """Clean up connections."""
        if self.client.is_connected:
            await self.client.room.disconnect()
        if self.agent.is_connected:
            await self.agent.disconnect()
        logger.info("Disconnected from room")

async def main():
    chat = SalonChat()
    await chat.start()

if __name__ == "__main__":
    asyncio.run(main())