"""
Telegram Stats Tracker Bot - Production Ready for Railway
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any

from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# Initialize Flask app
app = Flask(__name__)

# Configure logging for production
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Firebase initialization
def initialize_firebase():
    """Initialize Firebase - simplified version"""
    try:
        if not firebase_admin._apps:
            project_id = os.getenv('FIREBASE_PROJECT_ID')
            private_key = os.getenv('FIREBASE_PRIVATE_KEY')
            client_email = os.getenv('FIREBASE_CLIENT_EMAIL')
            
            if not all([project_id, private_key, client_email]):
                raise ValueError("Missing Firebase environment variables")
            
            private_key = private_key.replace('\\n', '\n')
            
            service_account_info = {
                "type": "service_account",
                "project_id": project_id,
                "private_key": private_key,
                "client_email": client_email,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase initialized successfully")
            
    except Exception as e:
        logger.error(f"Firebase initialization failed: {e}")
        raise

class FirestoreDB:
    def __init__(self):
        self.db = firestore.client()
    
    async def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get user data from Firestore"""
        try:
            doc_ref = self.db.collection('users').document(str(user_id))
            doc = doc_ref.get()
            
            if not doc.exists:
                return {'stats': {}, 'groups': {}, 'timezone': 'UTC'}
            
            data = doc.to_dict()
            return {
                'stats': data.get('stats', {}),
                'groups': data.get('groups', {}),
                'timezone': data.get('timezone', 'UTC')
            }
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return {'stats': {}, 'groups': {}, 'timezone': 'UTC'}
    
    async def set_user(self, user_id: str, data: Dict[str, Any]) -> None:
        """Set user data in Firestore"""
        try:
            doc_ref = self.db.collection('users').document(str(user_id))
            doc_ref.set(data, merge=True)
        except Exception as e:
            logger.error(f"Set user error: {e}")

# Helper functions
def format_timestamp(iso_string: str, timezone: str = 'UTC') -> str:
    """Format ISO timestamp to readable string"""
    try:
        if iso_string.endswith('Z'):
            iso_string = iso_string[:-1]
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime('%b %d, %Y at %I:%M %p')
    except Exception as e:
        logger.error(f"Error formatting timestamp: {e}")
        return iso_string

# Command handlers (same as before, but I'll include key ones for completeness)
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_text = """🎯 *Welcome to Stats Tracker Bot!*

Track any metric across all your devices:
• Weight, workout reps, study hours
• Daily habits, mood, water intake
• Custom stats of your choice

*Commands:*
/new - Create a new stat category
/add - Add an entry to a stat
/view - View your stats
/history - See stat history
/group - Add category groups
/delete - Delete a category
/timezone - Set your timezone
/help - Show this message"""

    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /view command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)
    all_stats = user_data['stats']
    all_groups = user_data['groups']

    grouped_cats = set()
    for group_categories in all_groups.values():
        if isinstance(group_categories, list):
            grouped_cats.update(group_categories)

    ungrouped = [cat for cat in all_stats.keys() if cat not in grouped_cats]
    buttons = []

    for cat in ungrouped:
        buttons.append([InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")])

    for group_name in all_groups.keys():
        buttons.append([InlineKeyboardButton(f"🗂️ {group_name}", callback_data=f"viewgroup_{group_name}")])

    if not buttons:
        await update.message.reply_text(
            "You don't have any categories or groups yet!\n"
            "Create one with: /new <name> or /group <group> <cat1> [cat2] ..."
        )
        return

    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "📊 *Your Stats and Groups:*\nSelect one to view details.",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    if data.startswith('view_'):
        category = data.replace('view_', '')
        user_data = await db.get_user(user_id)

        if category not in user_data['stats']:
            await query.edit_message_text(f"No entries for {category} yet!")
            return

        entries = user_data['stats'][category]['entries']
        if not entries:
            await query.edit_message_text(f"No entries for {category} yet!")
            return

        timezone = user_data['timezone']
        latest = entries[-1]

        response = f"📊 *{category.upper()}*\n\n"
        response += f"Latest: *{latest['value']}*\n"
        response += f"Logged: {format_timestamp(latest['timestamp'], timezone)}\n"
        if latest.get('note'):
            response += f"Note: _{latest['note']}_\n"
        response += f"\nTotal entries: {len(entries)}\n"
        response += f"\nUse /history {category} for full history"

        await query.edit_message_text(response, parse_mode='Markdown')

    elif data.startswith('viewgroup_'):
        group_name = data.replace('viewgroup_', '')
        user_data = await db.get_user(user_id)
    
        if group_name not in user_data['groups']:
            await query.edit_message_text(f"Group '{group_name}' not found.")
            return
    
        categories = user_data['groups'][group_name]
        
        buttons = []
        for cat in categories:
            buttons.append([InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")])
        
        buttons.append([InlineKeyboardButton('« Back to All Categories', callback_data='view_main')])
        
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            f"📂 *Group: {groupName}*\n\nSelect a category to view its latest entry:",
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    elif data == 'view_main':
        await handle_view(update, context)

# Add other command handlers (new, add, history, delete, timezone, group) here...
# They remain exactly the same as in our previous version

def create_application():
    """Create and configure the Telegram Bot Application"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
    
    # Initialize Firebase
    initialize_firebase()
    
    # Create application
    application = Application.builder().token(token).build()
    
    # Add database to bot_data
    application.bot_data['db'] = FirestoreDB()
    
    # Add handlers
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("help", handle_start))
    application.add_handler(CommandHandler("new", handle_new))
    application.add_handler(CommandHandler("add", handle_add))
    application.add_handler(CommandHandler("view", handle_view))
    application.add_handler(CommandHandler("history", handle_history))
    application.add_handler(CommandHandler("delete", handle_delete))
    application.add_handler(CommandHandler("timezone", handle_timezone))
    application.add_handler(CommandHandler("group", handle_group))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    return application

# Create application instance
application = create_application()

# Webhook routes
@app.route('/webhook', methods=['POST'])
async def webhook():
    """Handle Telegram webhook updates"""
    try:
        data = request.get_json()
        logger.info(f"Received update")
        
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/')
def home():
    """Root endpoint"""
    return jsonify({
        'status': 'Telegram Stats Bot is running!',
        'timestamp': datetime.utcnow().isoformat()
    })

# Initialize on import
initialize_firebase()

# Note: No app.run() here - Railway handles the process
