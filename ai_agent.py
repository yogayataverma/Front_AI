import os
import asyncio
import logging
import sys
import time
import json
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta


try:
    import jwt
except ImportError:
    print("Installing PyJWT...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "PyJWT"], check=True)
    import jwt


try:
    from livekit import rtc
except ImportError:
    print("Installing livekit package...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "livekit"], check=True)
    from livekit import rtc


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
        "exp": now + 86400, 
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
    """
    Represents a customer question that may require supervisor assistance.
    
    Attributes:
        id (str): Unique identifier for the help request
        question (str): The original question from the customer
        caller_id (str): Identifier for the customer who asked the question
        status (str): Current status - "pending", "resolved", or "unresolved"
        category (str): Categorization of the question (e.g., "hours", "pricing", "services")
        created_at (datetime): When the request was created
        updated_at (datetime): When the request was last updated
        timeout_at (datetime): When the request will automatically time out
        resolved_at (datetime): When the request was resolved (if applicable)
        supervisor_id (str): ID of the supervisor who resolved the request (if applicable)
        answer (str): The supervisor's answer to the question (if resolved)
        attempts (int): Number of times this request has been attempted
        knowledge_base_keys (List[str]): Keys in the knowledge base that were updated by this request
    """
    
    STATUS_PENDING = "pending"
    STATUS_RESOLVED = "resolved"
    STATUS_UNRESOLVED = "unresolved"
    
    def __init__(self, question: str, caller_id: str, timeout_minutes: int = 30):
        self.id = str(uuid.uuid4())
        self.question = question
        self.caller_id = caller_id
        self.status = self.STATUS_PENDING
        self.category = self._categorize_question(question)
        self.created_at = datetime.utcnow()
        self.updated_at = self.created_at
        self.timeout_at = self.created_at + timedelta(minutes=timeout_minutes)
        self.resolved_at = None
        self.supervisor_id = None
        self.answer = None
        self.attempts = 1
        self.knowledge_base_keys = []
    
    def _categorize_question(self, question: str) -> str:
        """Automatically categorize the question based on content."""
        question_lower = question.lower()
        

        categories = {
            "hours": ["hours", "open", "close", "schedule", "when"],
            "location": ["location", "where", "address", "directions"],
            "pricing": ["price", "cost", "fee", "how much", "discount"],
            "services": ["service", "offer", "provide", "treatment", "appointment"],
            "staff": ["staff", "stylist", "employee", "who"]
        }
        

        for category, keywords in categories.items():
            if any(keyword in question_lower for keyword in keywords):
                return category
        
        return "general"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert object to dictionary for storage."""
        data = {
            "id": self.id,
            "question": self.question,
            "caller_id": self.caller_id,
            "status": self.status,
            "category": self.category,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "timeout_at": self.timeout_at.isoformat(),
            "attempts": self.attempts,
            "knowledge_base_keys": self.knowledge_base_keys
        }
        

        if self.resolved_at:
            data["resolved_at"] = self.resolved_at.isoformat()
        if self.supervisor_id:
            data["supervisor_id"] = self.supervisor_id
        if self.answer:
            data["answer"] = self.answer
            
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HelpRequest':
        """Create a HelpRequest object from dictionary data."""
        request = cls(data["question"], data["caller_id"])
        

        request.id = data["id"]
        request.status = data["status"]
        request.category = data.get("category", "general")
        request.created_at = datetime.fromisoformat(data["created_at"])
        request.updated_at = datetime.fromisoformat(data["updated_at"])
        request.timeout_at = datetime.fromisoformat(data["timeout_at"])
        request.attempts = data.get("attempts", 1)
        request.knowledge_base_keys = data.get("knowledge_base_keys", [])
        
        if "resolved_at" in data:
            request.resolved_at = datetime.fromisoformat(data["resolved_at"])
        if "supervisor_id" in data:
            request.supervisor_id = data["supervisor_id"]
        if "answer" in data:
            request.answer = data["answer"]
            
        return request
    
    def mark_resolved(self, answer: str, supervisor_id: str = None):
        """Mark this request as resolved with the given answer."""
        self.status = self.STATUS_RESOLVED
        self.answer = answer
        self.resolved_at = datetime.utcnow()
        self.updated_at = self.resolved_at
        if supervisor_id:
            self.supervisor_id = supervisor_id
    
    def mark_unresolved(self, reason: str = "timeout"):
        """Mark this request as unresolved."""
        self.status = self.STATUS_UNRESOLVED
        self.updated_at = datetime.utcnow()
    
    def update_timeout(self, timeout_minutes: int):
        """Update the timeout for this request."""
        self.timeout_at = datetime.utcnow() + timedelta(minutes=timeout_minutes)
        self.updated_at = datetime.utcnow()
    
    def add_knowledge_base_key(self, key: str):
        """Add a knowledge base key that was updated by this request."""
        if key not in self.knowledge_base_keys:
            self.knowledge_base_keys.append(key)
            self.updated_at = datetime.utcnow()
            
# ---------------------------------------------------------------------------
# Simple Agent Implementation
# ---------------------------------------------------------------------------

class SimpleAgent:
    def __init__(self, loop=None):
        self.room = rtc.Room()
        self.is_connected = False
        self.loop = loop or asyncio.get_event_loop()
        
        self.pending_questions = {}

        try:
            firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate('service.json')
            firebase_admin.initialize_app(cred)
        
        self.db = firestore.client()
        self.help_requests_ref = self.db.collection('help_requests')
        self.knowledge_base_ref = self.db.collection('knowledge_base')
        
        self.knowledge_base = self.load_knowledge_base()
        self.response_queue = asyncio.Queue()

        self.setup_resolved_requests_listener()
        
        self.last_kb_refresh = datetime.utcnow()
        self.kb_refresh_interval = timedelta(minutes=5)

    def load_knowledge_base(self) -> dict:
        """Load knowledge base from Firebase."""
        knowledge_base = {}
        docs = self.knowledge_base_ref.stream()
        for doc in docs:
            data = doc.to_dict()
            knowledge_base[doc.id] = data['answer']
        logger.info(f"Loaded {len(knowledge_base)} entries from knowledge base")
        return knowledge_base

    def save_knowledge_base_entry(self, key: str, answer: str):
        """Save a knowledge base entry to Firebase."""
        self.knowledge_base_ref.document(key).set({
            'answer': answer,
            'updated_at': datetime.utcnow().isoformat()
        })

    def setup_resolved_requests_listener(self):
        """Set up a listener for resolved help requests."""

        def on_snapshot(doc_snapshot, changes, read_time):
            logger.info(f"Received Firebase update: {len(changes)} changes")
            for change in changes:
                try:
                    if change.type.name == 'MODIFIED':
                        doc = change.document
                        data = doc.to_dict()
                        logger.info(f"Document changed: {doc.id}, status: {data.get('status')}")
                        
                        if data.get('status') == 'resolved' and 'answer' in data:
                            logger.info(f"Processing resolved request: {doc.id}")
                            self.update_knowledge_base(data['question'], data['answer'])
                            
                            
                            question_key = f"{data['question'].lower()}:{data['caller_id']}"
                            if question_key in self.pending_questions:
                                del self.pending_questions[question_key]
                            
                            
                            asyncio.run_coroutine_threadsafe(
                                self.send_response_to_caller(
                                    data['answer'],
                                    data['caller_id']
                                ),
                                self.loop
                            )
                except Exception as e:
                    logger.error(f"Error processing Firebase change: {e}")
        

        pending_query = self.help_requests_ref.where('status', '==', 'pending')
        pending_query.on_snapshot(on_snapshot)
        
        resolved_query = self.help_requests_ref.where('status', '==', 'resolved')
        resolved_query.on_snapshot(on_snapshot)
        
        logger.info("Firebase listeners set up successfully")

    async def send_response_to_caller(self, answer: str, caller_id: str):
        """Send a response directly to the caller who asked the question."""
        try:
            participants = self.room.participants
            logger.info(f"Sending response to caller {caller_id}")
            
            response_data = {
                "text": answer,
                "caller_id": caller_id
            }
            
            await self.room.local_participant.publish_data(
                json.dumps(response_data).encode(),
                reliable=True
            )
            
            logger.info(f"Sent supervisor response to caller {caller_id}: {answer[:30]}...")
        except Exception as e:
            logger.error(f"Error sending response to caller {caller_id}: {e}")
            
        
            self.response_queue.put_nowait({
                "text": answer,
                "caller_id": caller_id
            })
            
    def update_knowledge_base(self, question: str, answer: str):
        """Update the knowledge base with new information."""
        try:
            question_lower = question.lower()
            
            words = question_lower.split()
            
            question_words = {'what', 'when', 'where', 'who', 'why', 'how', 'do', 'does', 'is', 'are', 'can', 'could', 'would', 'should'}
            meaningful_words = [w for w in words if w not in question_words]
            
            if 'hours' in question_lower:
                key = 'hours'
            elif 'location' in question_lower or 'where' in question_lower:
                key = 'location'
            elif 'price' in question_lower or 'cost' in question_lower:
                key = 'pricing'
            elif 'service' in question_lower or 'offer' in question_lower:
                key = 'services'
            else:
                key = ' '.join(meaningful_words[:2]) if meaningful_words else ' '.join(words[:2])

            self.knowledge_base[key] = answer
            self.save_knowledge_base_entry(key, answer)
            logger.info(f"Updated knowledge base entry for '{key}'")
        except Exception as e:
            logger.error(f"Error updating knowledge base: {e}")

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
        
        try:
            self.help_requests_ref.document(help_request.id).set(help_request.to_dict())
            logger.info(f"Help request stored in Firebase with ID: {help_request.id}")
        except Exception as e:
            logger.error(f"Failed to store help request in Firebase: {e}")
            raise
        
        logger.info(f"SUPERVISOR NOTIFICATION: Hey, I need help answering: '{question}'")
        
        return help_request

    def setup_handlers(self):
        """Set up event handlers for the room."""
        @self.room.on("participant_joined")
        def _on_participant_joined(participant):
            logger.info(f"Call received from: {participant.identity}")
           
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
                
                async def process_and_respond():
                    try:
                        response = await self._process_message(message)
                        logger.info(f"Generated response: {response}")
                        
                        await self.room.local_participant.publish_data(
                            json.dumps({"text": response}).encode(),
                            reliable=True
                        )
                        logger.info("Response sent")
                    except Exception as e:
                        logger.error(f"Error in async processing: {e}")

                asyncio.create_task(process_and_respond())
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")

        @self.room.on("disconnected")
        def _on_disconnected():
            logger.info("Disconnected from room")

    async def refresh_knowledge_base(self):
        """Refresh knowledge base if enough time has passed."""
        now = datetime.utcnow()
        if now - self.last_kb_refresh >= self.kb_refresh_interval:
            self.knowledge_base = self.load_knowledge_base()
            self.last_kb_refresh = now
            logger.info("Knowledge base refreshed")

    async def _process_message(self, message: str) -> str:
        """Process incoming message and return appropriate response."""
        try:
            await self.refresh_knowledge_base()
            
            data = json.loads(message)
            query = data.get("text", "").lower()
            caller_id = data.get("caller_id", "unknown")
            logger.info(f"Processing query: {query}")
            
           
            question_key = f"{query}:{caller_id}"
            if question_key in self.pending_questions:
                pending_since = self.pending_questions[question_key]
              
                if datetime.utcnow() - pending_since < timedelta(minutes=5):
                    return "I've already asked my supervisor about this. I'll let you know as soon as I receive a response."
            
            if 'hours' in query and 'hours' in self.knowledge_base:
                return self.knowledge_base['hours']
            elif ('location' in query or 'where' in query) and 'location' in self.knowledge_base:
                return self.knowledge_base['location']
            elif ('price' in query or 'cost' in query) and 'pricing' in self.knowledge_base:
                return self.knowledge_base['pricing']
            elif ('service' in query or 'offer' in query) and 'services' in self.knowledge_base:
                return self.knowledge_base['services']
            
            query_words = set(query.split())
            best_match = None
            best_match_score = 0
            
            for key, answer in self.knowledge_base.items():
                key_words = set(key.split())
                common_words = query_words.intersection(key_words)
                
                if common_words:
                    score = len(common_words) / max(len(query_words), len(key_words))
                    
                    if key in query:
                        score += 0.3
                    
                    for word in key_words:
                        if query.startswith(word):
                            score += 0.2
                            break
                    
                    if score > best_match_score:
                        best_match_score = score
                        best_match = answer
            
            if best_match and best_match_score > 0.2:
                logger.info(f"Found matching answer with score: {best_match_score}")
                return best_match
            

            for key, answer in self.knowledge_base.items():
                if key in query:
                    logger.info(f"Found substring match for key: {key}")
                    return answer

            try:
                help_request = await self.create_help_request(query, caller_id)
                self.pending_questions[question_key] = datetime.utcnow()
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
                if not self.response_queue.empty():
                    response = await self.response_queue.get()
                    await self.room.local_participant.publish_data(
                        json.dumps(response).encode(),
                        reliable=True
                    )
                    logger.info(f"Sent queued response to customer: {response['text'][:30]}...")
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error processing response queue: {e}")
                await asyncio.sleep(1)

    async def run(self):
        """Main run loop for the agent."""
        try:
            if await self.connect():
                self.setup_handlers()
                logger.info("Agent is live – press Ctrl+C to exit.")
               
                response_processor = asyncio.create_task(self.process_response_queue())
                
             
                timeout_checker = asyncio.create_task(self.check_timeouts())
                
                while self.is_connected:
                    await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down agent...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
   
            if 'response_processor' in locals() and not response_processor.done():
                response_processor.cancel()
            if 'timeout_checker' in locals() and not timeout_checker.done():
                timeout_checker.cancel()
            await self.disconnect()

    async def check_timeouts(self):
        """Check for timed out requests."""
        while self.is_connected:
            try:
                pending_requests = self.help_requests_ref.where('status', '==', 'pending').stream()
                now = datetime.utcnow()
                
                for doc in pending_requests:
                    data = doc.to_dict()
                    timeout_at = datetime.fromisoformat(data['timeout_at'])
                    
                    if now > timeout_at:
                        logger.info(f"Request {doc.id} has timed out")
                        
                        doc.reference.update({
                            'status': 'unresolved',
                            'updated_at': now.isoformat()
                        })
                        
   
                        question_key = f"{data['question'].lower()}:{data['caller_id']}"
                        if question_key in self.pending_questions:
                            del self.pending_questions[question_key]
                        
                        await self.room.local_participant.publish_data(
                            json.dumps({
                                "text": "I apologize, but I haven't received a response from my supervisor yet. Please try asking your question again or contact us directly.",
                                "caller_id": data['caller_id']
                            }).encode(),
                            reliable=True
                        )
                
                await asyncio.sleep(60)  
            except Exception as e:
                logger.error(f"Error checking timeouts: {e}")
                await asyncio.sleep(60)

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

    async def disconnect(self):
        """Disconnect from the room."""
        if self.is_connected:
            await self.room.disconnect()
            self.is_connected = False

class SalonChat:
    def __init__(self):
        self.agent = SimpleAgent(asyncio.get_running_loop())
        self.client = InteractiveClient()
        self.is_running = False

    async def start(self):
        """Start both agent and client."""
        try:
            if await self.agent.connect():
                self.agent.setup_handlers()
                logger.info("Agent is live")
                
                if await self.client.connect():
                    self.client.setup_handlers()
                    self.is_running = True
                    
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
                        question = input("Your question: ").strip()
                        
                        if question.lower() == 'exit':
                            break
                            
                        if question:
                            await self.client.send_message(question)
                            
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