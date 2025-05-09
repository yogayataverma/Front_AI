from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import json
import logging
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import lru_cache
from celery import Celery
from tenacity import retry, stop_after_attempt

try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate('service.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()
help_requests_ref = db.collection('help_requests')
knowledge_base_ref = db.collection('knowledge_base')

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
)

celery = Celery('tasks', broker='redis://localhost:6379/0')

@app.route('/')
def index():
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

@app.route('/submit_answer', methods=['POST'])
@limiter.limit("10 per minute")
def submit_answer():
    request_id = request.form.get('request_id')
    answer = request.form.get('answer')
    
    if not request_id or not answer:
        return jsonify({'error': 'Missing request_id or answer'}), 400
    
    help_request = help_requests_ref.document(request_id)
    request_data = help_request.get().to_dict()
    
    help_request.update({
        'status': 'resolved',
        'answer': answer,
        'resolved_at': datetime.utcnow().isoformat(),
        'updated_at': datetime.utcnow().isoformat()
    })
    
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
    
    logging.info(f"TEXTING CALLER {request_data['caller_id']}: {answer}")
    logging.info(f"UPDATED KNOWLEDGE BASE with key '{key}': {answer}")
    
    return redirect(url_for('index'))

@app.route('/edit_knowledge', methods=['POST'])
def edit_knowledge():
    key = request.form.get('key')
    answer = request.form.get('answer')
    
    if not key or not answer:
        return jsonify({'error': 'Missing key or answer'}), 400
    
    knowledge_base_ref.document(key).set({
        'answer': answer,
        'updated_at': datetime.utcnow().isoformat()
    })
    
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
    app.run(debug=True) 