"""
Telegram Stats Tracker Bot - FastAPI Version
Using Firestore REST API with async HTTP requests
"""

import os
import logging
import json
import httpx
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import pytz
from history_handlers import handle_history, format_timestamp




logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)















#timestamp Helper function 
def format_timestamp(iso_string: str, timezone: str = 'UTC') -> str:
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        try:
            tz = pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning(f"Invalid timezone '{timezone}', falling back to UTC")
            tz = pytz.UTC
        dt = dt.astimezone(tz)
        return dt.strftime('%b %d, %Y at %I:%M %p %Z')
    except Exception as e:
        logger.error(f"Error formatting timestamp: {e}")
        return iso_string

















class FirestoreDB:
    def __init__(self):
        self.project_id = os.getenv('FIREBASE_PROJECT_ID')
        if not self.project_id:
            logger.error("FIREBASE_PROJECT_ID environment variable is required")
            raise ValueError("FIREBASE_PROJECT_ID environment variable is required")
        
        self.base_url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents"
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
    
    async def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get user data from Firestore using REST API"""
        try:
            response = await self.client.get(f"{self.base_url}/users/{user_id}")
            if response.status_code == 404:
                return {'stats': {}, 'groups': {}, 'timezone': 'UTC'}
            
            data = response.json()
            return self.parse_document(data)
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return {'stats': {}, 'groups': {}, 'timezone': 'UTC'}
    
    async def set_user(self, user_id: str, data: Dict[str, Any]) -> None:
        """Set user data in Firestore using REST API"""
        try:
            firestore_doc = self.to_firestore_document(data)
            response = await self.client.patch(
                f"{self.base_url}/users/{user_id}",
                headers={'Content-Type': 'application/json'},
                content=json.dumps({'fields': firestore_doc})
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Set user error: {e}")
    
    def parse_document(self, doc):
        """Parse Firestore document"""
        if 'fields' not in doc:
            return {}
        
        result = {}
        for key, value in doc['fields'].items():
            result[key] = self.parse_value(value)
        return result
    
    def parse_value(self, value):
        """Parse Firestore value types"""
        if 'stringValue' in value:
            return value['stringValue']
        elif 'integerValue' in value:
            return int(value['integerValue'])
        elif 'doubleValue' in value:
            return float(value['doubleValue'])
        elif 'booleanValue' in value:
            return value['booleanValue']
        elif 'mapValue' in value:
            return self.parse_document(value['mapValue'])
        elif 'arrayValue' in value:
            return [self.parse_value(v) for v in value['arrayValue'].get('values', [])]
        return None
    
    def to_firestore_document(self, obj):
        """Convert Python object to Firestore document"""
        result = {}
        for key, value in obj.items():
            result[key] = self.to_firestore_value(value)
        return result
    
    def to_firestore_value(self, value):
        """Convert Python value to Firestore value"""
        if isinstance(value, str):
            return {'stringValue': value}
        elif isinstance(value, int):
            return {'integerValue': value}
        elif isinstance(value, float):
            return {'doubleValue': value}
        elif isinstance(value, bool):
            return {'booleanValue': value}
        elif isinstance(value, list):
            return {'arrayValue': {'values': [self.to_firestore_value(v) for v in value]}}
        elif isinstance(value, dict):
            return {'mapValue': {'fields': self.to_firestore_document(value)}}
        return {'nullValue': None}












# Command handlers
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command"""
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


















async def handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new command"""
    if not context.args:
        await update.message.reply_text(
            "Usage: /new <category_name>\n"
            "Example: /new weight\n"
            "Example: /new study_hours"
        )
        return

    category = '_'.join(context.args).lower()
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']
    
    user_data = await db.get_user(user_id)
    
    if category in user_data['stats']:
        await update.message.reply_text(f"Category '{category}' already exists!")
        return
    
    user_data['stats'][category] = {
        'entries': [],
        'created_at': datetime.utcnow().isoformat() + 'Z'
    }

    await db.set_user(user_id, user_data)
    await update.message.reply_text(
        f"✅ Created new category: *{category}*\n"
        f"Use /add {category} <value> to log entries!",
        parse_mode='Markdown'
    )













async def handle_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add command"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /add <category> <value> [note]\n"
            "Example: /add weight 75.5\n"
            "Example: /add workout 50 push-ups today"
        )
        return

    category = context.args[0].lower()
    try:
        value = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Value must be a number!")
        return

    note = ' '.join(context.args[2:]) if len(context.args) > 2 else ''
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)

    # TIMEZONE ENFORCEMENT
    if 'timezone' not in user_data or not user_data['timezone']:
        await update.message.reply_text(
            "❌ Please set your timezone first using /timezone\n"
            "Example: /timezone Australia/Sydney\n"
            "Example: /timezone Asia/Ho_Chi_Minh"
        )
        return

    if category not in user_data['stats']:
        await update.message.reply_text(
            f"❌ Category '{category}' doesn't exist.\n"
            f"Create it first with: /new {category}"
        )
        return

    # Create timestamp in local timezone (NEW)
    user_tz = pytz.timezone(user_data['timezone'])
    local_time = datetime.now(pytz.UTC).astimezone(user_tz)  # Convert current UTC to local time

    entry = {
        'value': value,
        'note': note,
        'timestamp': local_time.isoformat(),  # Local time with offset
        'timezone': user_data['timezone']     # Store timezone context
    }

    user_data['stats'][category]['entries'].append(entry)
    await db.set_user(user_id, user_data)

    response = f"✅ Added to *{category}*: {value}"
    if note:
        response += f"\n📝 Note: {note}"

    # Show the local time it was recorded
    formatted_time = format_timestamp(local_time.isoformat(), user_data['timezone'])
    response += f"\n🕒 Recorded at: {formatted_time}"

    await update.message.reply_text(response, parse_mode='Markdown')

















async def handle_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /view command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)
    all_stats = user_data['stats']
    all_groups = user_data['groups']

    # Collect all categories that belong to groups
    grouped_cats = set()
    for group_categories in all_groups.values():
        if isinstance(group_categories, list):
            grouped_cats.update(group_categories)

    # Ungrouped = categories not in any group
    ungrouped = [cat for cat in all_stats.keys() if cat not in grouped_cats]

    # Construct buttons
    buttons = []

    # Add ungrouped categories
    for cat in ungrouped:
        buttons.append([InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")])

    # Add groups
    for group_name in all_groups.keys():
        buttons.append([InlineKeyboardButton(f"🗂️ {group_name}", callback_data=f"viewgroup_{group_name}")])

    # Handle empty case
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




















async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /delete command"""
    if not context.args:
        await update.message.reply_text(
            "Usage: /delete <category_or_group>\n"
            "Example: /delete weight\n"
            "Example: /delete workout_group"
        )
        return

    target = context.args[0].lower()
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)

    # Check if it's a category
    if target in user_data['stats']:
        del user_data['stats'][target]
        await db.set_user(user_id, user_data)
        await update.message.reply_text(f"✅ Deleted category: {target}")
    
    # Check if it's a group
    elif 'groups' in user_data and target in user_data['groups']:
        del user_data['groups'][target]
        await db.set_user(user_id, user_data)
        await update.message.reply_text(f"✅ Deleted group: {target}")
    
    else:
        await update.message.reply_text(f"❌ Category or group '{target}' not found")

























async def handle_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /timezone command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    if not context.args:
        user_data = await db.get_user(user_id)
        current_tz = user_data.get('timezone', 'Not set')

        await update.message.reply_text(
            f"⏰ *Current timezone:* {current_tz}\n\n"
            f"Future entries will use this timezone.\n\n"
            f"To change, use: /timezone <timezone>\n\n"
            f"*Examples:*\n"
            f"/timezone Australia/Sydney\n"
            f"/timezone Asia/Ho_Chi_Minh\n"
            f"/timezone America/New_York\n\n"
            f"Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
            parse_mode='Markdown'
        )
        return

    timezone = '_'.join(context.args)
    
    # Basic validation
    if '/' not in timezone:
        await update.message.reply_text(
            f"❌ Invalid timezone format: {timezone}\n\n"
            f"Use format like: America/New_York or Asia/Tokyo\n"
            f"Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        )
        return

    user_data = await db.get_user(user_id)
    user_data['timezone'] = timezone
    await db.set_user(user_id, user_data)
    
    await update.message.reply_text(f"✅ Timezone set to: *{timezone}*", parse_mode='Markdown')

























async def handle_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /group command"""
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /group <group_name> <category1> [category2] ...\n"
            "Example: /group workout squats pushups lunges\n\n"
            "To view a group: /history workout"
        )
        return

    group_name = context.args[0].lower()
    categories = [cat.lower() for cat in context.args[1:]]
    
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    # Verify all categories exist
    user_data = await db.get_user(user_id)
    missing_categories = [cat for cat in categories if cat not in user_data['stats']]
    
    if missing_categories:
        await update.message.reply_text(
            f"❌ These categories don't exist: {', '.join(missing_categories)}\n"
            "Create them first with /new"
        )
        return

    # Save the group
    if 'groups' not in user_data:
        user_data['groups'] = {}
    user_data['groups'][group_name] = categories
    
    await db.set_user(user_id, user_data)
    await update.message.reply_text(
        f"✅ Created group *{group_name}*\n"
        f"Contains: {', '.join(categories)}",
        parse_mode='Markdown'
    )

































async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']


    if data == 'view_main':
        # Return to main view
        user_data = await db.get_user(user_id)
        all_stats = user_data.get('stats', {})
        all_groups = user_data.get('groups', {})

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
            await query.edit_message_text(
                "You don't have any categories or groups yet!\n"
                "Create one with: /new <name> or /group <group> <cat1> [cat2] ..."
            )
            return

        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            "📊 *Your Stats and Groups:*\nSelect one to view details.",
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    elif data.startswith('view_'):
        category = data.replace('view_', '')
        # Set context.args so handle_history knows which category to show
        context.args = [category]     
        # Delete the message with the buttons
        await query.delete_message()    
        # Call the history handler, which will send a new message
        await handle_history(update, context)

    elif data.startswith('viewgroup_'):
        group_name = data.replace('viewgroup_', '')
        user_data = await db.get_user(user_id)
    
        if group_name not in user_data['groups']:
            await query.edit_message_text(f"Group '{group_name}' not found.")
            return
    
        categories = user_data['groups'][group_name]
        
        # Create buttons for each category in the group
        buttons = []
        for cat in categories:
            buttons.append([InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")])
        
        # Add back button
        buttons.append([InlineKeyboardButton('« Back to All Categories', callback_data='view_main')])
        
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            f"📂 *Group: {group_name}*\n\nSelect a category to view its latest entry:",
            parse_mode='Markdown',
            reply_markup=keyboard
        )




































def create_application():
    """Create and configure the Telegram Bot Application"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
    
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



# Add this function to your main file or a separate migration file
async def migrate_all_users_timezones(db):
    """One-time migration for all users"""
    logger.info("Starting timezone migration...")

    # This would need to iterate through all users in your database
    # Since Firestore REST doesn't have easy "get all users", we'll handle this per-user
    # You might need to run this manually for each user or find another way

    logger.info("Timezone migration completed")

# Add this to your handle_start or create a /migrate command
async def handle_migrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual migration command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)
    migrated_count = 0

    # Migration cutoff: Nov 1, 2024 12:00 UTC
    cutoff_time = datetime(2025, 11, 1, 12, 0, 0)

    for category_name, category_data in user_data['stats'].items():
        for entry in category_data['entries']:
            # Convert UTC timestamp to datetime
            entry_time = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))

            # If entry is before cutoff and has no timezone, set to HCMC
            if entry_time < cutoff_time and 'timezone' not in entry:
                entry['timezone'] = 'Asia/Ho_Chi_Minh'
                migrated_count += 1

                # Convert timestamp to HCMC timezone for storage
                hcmc_tz = pytz.timezone('Asia/Ho_Chi_Minh')
                entry_local_time = entry_time.astimezone(hcmc_tz)
                entry['timestamp'] = entry_local_time.isoformat()

    if migrated_count > 0:
        await db.set_user(user_id, user_data)
        await update.message.reply_text(f"✅ Migrated {migrated_count} entries to HCMC timezone")
    else:
        await update.message.reply_text("ℹ️ No entries needed migration")




























# Global application instance
telegram_app: Optional[Application] = None





# Lifespan context manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    global telegram_app
    
    # Startup
    logger.info("Starting up...")
    telegram_app = create_application()
    await telegram_app.initialize()
    await telegram_app.start()
    logger.info("Telegram application initialized and started")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    if telegram_app:
        await telegram_app.stop()
        await telegram_app.shutdown()
        # Close database HTTP client
        if 'db' in telegram_app.bot_data:
            await telegram_app.bot_data['db'].close()
    logger.info("Telegram application stopped")







# Initialize FastAPI app
app = FastAPI(title="Telegram Stats Tracker Bot", lifespan=lifespan)





# Webhook endpoint
@app.post("/webhook")
async def webhook(request: Request):
    """Handle Telegram webhook updates"""
    try:
        data = await request.json()
        logger.info("Received webhook update")
        
        if telegram_app is None:
            raise HTTPException(status_code=503, detail="Bot not initialized")
        
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        
        return JSONResponse(content={'status': 'ok'})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))









# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse(content={
        'status': 'healthy',
        'bot_initialized': telegram_app is not None,
        'timestamp': datetime.utcnow().isoformat()
    })





# Root endpoint
@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse(content={
        'status': 'Telegram Stats Bot is running!',
        'timestamp': datetime.utcnow().isoformat()
    })








# Optional: Endpoint to set webhook (call this once after deployment)
@app.post("/set-webhook")
async def set_webhook(webhook_url: str):
    """Set the Telegram webhook URL"""
    try:
        if telegram_app is None:
            raise HTTPException(status_code=503, detail="Bot not initialized")
        
        await telegram_app.bot.set_webhook(url=webhook_url)
        return JSONResponse(content={
            'status': 'success',
            'webhook_url': webhook_url
        })
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))




# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
