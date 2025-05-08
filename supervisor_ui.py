from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import json
import logging

# Initialize Firebase
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate('service.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()
help_requests_ref = db.collection('help_requests')
knowledge_base_ref = db.collection('knowledge_base')

app = Flask(__name__)

@app.route('/')
def index():
    # Get all help requests
    requests = help_requests_ref.order_by('created_at', direction=firestore.Query.DESCENDING).stream()
    requests_list = [doc.to_dict() for doc in requests]
    
    # Separate requests by status
    pending = [r for r in requests_list if r['status'] == 'pending']
    resolved = [r for r in requests_list if r['status'] == 'resolved']
    
    # Get knowledge base entries
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
def submit_answer():
    request_id = request.form.get('request_id')
    answer = request.form.get('answer')
    
    if not request_id or not answer:
        return jsonify({'error': 'Missing request_id or answer'}), 400
    
    # Update the help request
    help_request = help_requests_ref.document(request_id)
    help_request.update({
        'status': 'resolved',
        'answer': answer,
        'resolved_at': datetime.utcnow().isoformat(),
        'updated_at': datetime.utcnow().isoformat()
    })
    
    # Get the original request details
    request_data = help_request.get().to_dict()
    
    # Simulate texting back the original caller
    logging.info(f"TEXTING CALLER {request_data['caller_id']}: {answer}")
    
    # Update knowledge base (simulated)
    logging.info(f"UPDATING KNOWLEDGE BASE with new information: {answer}")
    
    return redirect(url_for('index'))

@app.route('/edit_knowledge', methods=['POST'])
def edit_knowledge():
    key = request.form.get('key')
    answer = request.form.get('answer')
    
    if not key or not answer:
        return jsonify({'error': 'Missing key or answer'}), 400
    
    # Update knowledge base entry
    knowledge_base_ref.document(key).set({
        'answer': answer,
        'updated_at': datetime.utcnow().isoformat()
    })
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True) 