from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore
import json
import logging
import os
import asyncio
import sys
import time
import uuid
from typing import Optional, Dict, Any, List
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import lru_cache
from celery import Celery
from tenacity import retry, stop_after_attempt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("supervisor_app")

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate('service.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()
help_requests_ref = db.collection('help_requests')
knowledge_base_ref = db.collection('knowledge_base')

class SimpleAgent:
    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_event_loop()
        self.help_requests_ref = help_requests_ref
        self.knowledge_base_ref = knowledge_base_ref
        self.knowledge_base = {}
        self.pending_questions = {}  # Track questions that are already pending
        self.setup_resolved_requests_listener()

    def setup_resolved_requests_listener(self):
        """Set up a listener for resolved help requests."""
        def on_snapshot(doc_snapshot, changes, read_time):
            for change in changes:
                if change.type.name == 'MODIFIED':
                    doc = change.document
                    data = doc.to_dict()
                    
                    if data.get('status') == 'resolved' and 'answer' in data:
                        self.update_knowledge_base(data['question'], data['answer'])
                        
                        # Use the loop that was passed in during initialization
                        asyncio.run_coroutine_threadsafe(
                            self.send_response_to_caller(
                                data['answer'], 
                                data['caller_id']
                            ),
                            self.loop
                        )
        
        # Actually set up the listener
        self.help_requests_ref.where('status', '==', 'pending').on_snapshot(on_snapshot)

    async def _process_message(self, message: str) -> str:
        """Process incoming message and return appropriate response."""
        try:
            await self.refresh_knowledge_base()
            
            data = json.loads(message)
            query = data.get("text", "").lower()
            caller_id = data.get("caller_id", "unknown")
            logger.info(f"Processing query: {query}")
            
            # First check if we already have an answer in the knowledge base
            if 'hours' in query and 'hours' in self.knowledge_base:
                return self.knowledge_base['hours']
            elif ('location' in query or 'where' in query) and 'location' in self.knowledge_base:
                return self.knowledge_base['location']
            elif ('price' in query or 'cost' in query) and 'pricing' in self.knowledge_base:
                return self.knowledge_base['pricing']
            elif ('service' in query or 'offer' in query) and 'services' in self.knowledge_base:
                return self.knowledge_base['services']
            
            # Check if this is a repeated question that's already pending
            question_key = f"{query}:{caller_id}"
            if question_key in self.pending_questions:
                pending_since = self.pending_questions[question_key]
                # If the question was asked within the last 5 minutes, don't create a new request
                if datetime.utcnow() - pending_since < timedelta(minutes=5):
                    return "I've already asked my supervisor about this. I'll let you know as soon as I receive a response."
            
            # Create help request if no answer found
            try:
                help_request = await self.create_help_request(query, caller_id)
                # Add to pending questions cache
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

    async def refresh_knowledge_base(self):
        """Refresh the knowledge base from Firestore."""
        try:
            kb_docs = self.knowledge_base_ref.stream()
            self.knowledge_base = {
                doc.id: doc.to_dict()['answer']
                for doc in kb_docs
            }
        except Exception as e:
            logger.error(f"Error refreshing knowledge base: {e}")

    def update_knowledge_base(self, question: str, answer: str):
        """Update the knowledge base with a new question-answer pair."""
        try:
            key = self._generate_kb_key(question)
            self.knowledge_base_ref.document(key).set({
                'answer': answer,
                'updated_at': datetime.utcnow().isoformat()
            })
            self.knowledge_base[key] = answer
        except Exception as e:
            logger.error(f"Error updating knowledge base: {e}")

    def _generate_kb_key(self, question: str) -> str:
        """Generate a key for the knowledge base from a question."""
        question = question.lower()
        if 'hours' in question:
            return 'hours'
        elif 'location' in question or 'where' in question:
            return 'location'
        elif 'price' in question or 'cost' in question:
            return 'pricing'
        elif 'service' in question or 'offer' in question:
            return 'services'
        else:
            words = question.split()
            question_words = {'what', 'when', 'where', 'who', 'why', 'how', 'do', 'does', 'is', 'are', 'can', 'could', 'would', 'should'}
            meaningful_words = [w for w in words if w not in question_words]
            return ' '.join(meaningful_words[:2]) if meaningful_words else ' '.join(words[:2])

    async def create_help_request(self, question: str, caller_id: str):
        """Create a new help request in Firestore."""
        doc_ref = self.help_requests_ref.document()
        data = {
            'question': question,
            'caller_id': caller_id,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        await doc_ref.set(data)
        return doc_ref

    async def send_response_to_caller(self, answer: str, caller_id: str):
        """Send response back to the caller."""
        logger.info(f"TEXTING CALLER {caller_id}: {answer}")
        # Implement actual messaging logic here

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Add a secret key for flash messages

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

celery = Celery('tasks', broker='redis://localhost:6379/0')

# Initialize the agent
agent = SimpleAgent()

@app.route('/')
def index():
    # Add error handling here
    try:
        requests = help_requests_ref.order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        requests_list = [doc.to_dict() for doc in requests]
        
        pending = [r for r in requests_list if r['status'] == 'pending']
        resolved = [r for r in requests_list if r['status'] == 'resolved']
        
        knowledge_base = {}
        kb_docs = knowledge_base_ref.stream()
        for doc in kb_docs:
            data = doc.to_dict()
            knowledge_base[doc.id] = {
                'answer': data['answer'],
                'updated_at': data.get('updated_at', 'Unknown')
            }
        
        return render_template('index.html', 
                             pending=pending, 
                             resolved=resolved,
                             knowledge_base=knowledge_base)
    except Exception as e:
        logger.error(f"Error loading index page: {str(e)}")
        return "Error loading data. Please check the logs.", 500

@app.route('/submit_answer', methods=['POST'])
@limiter.limit("10 per minute")
def submit_answer():
    try:
        request_id = request.form.get('request_id')
        answer = request.form.get('answer')
        
        if not request_id or not answer:
            flash('Missing request ID or answer', 'error')
            return redirect(url_for('index'))
        
        # Add debug logging
        logger.info(f"Processing answer submission for request {request_id}")
        
        help_request_ref = help_requests_ref.document(request_id)
        request_doc = help_request_ref.get()
        
        if not request_doc.exists:
            flash(f'Help request {request_id} not found', 'error')
            return redirect(url_for('index'))
            
        request_data = request_doc.to_dict()
        
        # Update the help request with the answer
        help_request_ref.update({
            'status': 'resolved',
            'answer': answer,
            'resolved_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat(),
            'supervisor_id': 'web-admin'  # Add supervisor ID
        })
        
        logger.info(f"Updated help request {request_id} with answer")
        
        # Update knowledge base
        question = request_data['question'].lower()
        if 'hours' in question:
            key = 'hours'
        elif 'location' in question or 'where' in question:
            key = 'location'
        elif 'price' in question or 'cost' in question:
            key = 'pricing'
        elif 'service' in question or 'offer' in question:
            key = 'services'
        else:
            words = question.split()
            question_words = {'what', 'when', 'where', 'who', 'why', 'how', 'do', 'does', 'is', 'are', 'can', 'could', 'would', 'should'}
            meaningful_words = [w for w in words if w not in question_words]
            key = ' '.join(meaningful_words[:2]) if meaningful_words else ' '.join(words[:2])
        
        knowledge_base_ref.document(key).set({
            'answer': answer,
            'updated_at': datetime.utcnow().isoformat()
        })
        
        logger.info(f"Updated knowledge base with key '{key}'")
        flash(f'Answer submitted successfully for request {request_id}', 'success')
        
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error submitting answer: {str(e)}")
        flash(f'Error submitting answer: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/edit_knowledge', methods=['POST'])
def edit_knowledge():
    try:
        key = request.form.get('key')
        answer = request.form.get('answer')
        
        if not key or not answer:
            flash('Missing key or answer', 'error')
            return redirect(url_for('index'))
        
        knowledge_base_ref.document(key).set({
            'answer': answer,
            'updated_at': datetime.utcnow().isoformat()
        })
        
        logger.info(f"Updated knowledge base entry '{key}'")
        flash(f'Knowledge base entry "{key}" updated successfully', 'success')
        
        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error updating knowledge base: {str(e)}")
        flash(f'Error updating knowledge base: {str(e)}', 'error')
        return redirect(url_for('index'))

@lru_cache(maxsize=100)
def get_cached_answer(question):
    return knowledge_base.get(question)

@celery.task
def process_help_request(request_id):
    try:
        request_doc = help_requests_ref.document(request_id).get()
        if request_doc.exists:
            request_data = request_doc.to_dict()
            logging.info(f"Processing help request {request_id}")
    except Exception as e:
        logging.error(f"Error processing help request {request_id}: {str(e)}")

@retry(stop=stop_after_attempt(3))
def update_help_request(request_id, data):
    try:
        help_requests_ref.document(request_id).update(data)
        logging.info(f"Successfully updated help request {request_id}")
    except Exception as e:
        logging.error(f"Error updating help request {request_id}: {str(e)}")
        raise

if __name__ == '__main__':
    # Start the event loop in a separate thread for the agent
    import threading
    def run_agent_loop():
        asyncio.set_event_loop(agent.loop)
        agent.loop.run_forever()
    
    agent_thread = threading.Thread(target=run_agent_loop, daemon=True)
    agent_thread.start()
    
    # Run the Flask app
    app.run(debug=True, port=5000) 