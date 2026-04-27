# Real Linecount : 584 as of 2025-11-03 23:23:35

"""
Telegram Stats Tracker Bot - FastAPI Version
Using Firestore REST API with async HTTP requests
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
import pytz
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from history_handlers import *

logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)


async def handle_unrecognized_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle unrecognized commands"""
    command = update.message.text.split()[0]  # Get the command including "/"
    await update.message.reply_text(f"Unrecognised command {command}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a message to the user"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    if isinstance(update, Update) and update.effective_message:
        # Avoid replying if it's already a failed callback query that might have been answered
        try:
            if isinstance(context.error, httpx.HTTPStatusError):
                status = context.error.response.status_code
                detail = context.error.response.text
                error_msg = f"❌ Database Error (HTTP {status}): {detail}"
            elif isinstance(context.error, TelegramError):
                error_msg = f"❌ Telegram API Error: {str(context.error)}"
            elif isinstance(context.error, ValueError):
                error_msg = f"❌ Validation Error: {str(context.error)}"
            else:
                error_msg = f"❌ An unexpected error occurred: {type(context.error).__name__}"
            
            # Truncate if too long for Telegram message limit
            if len(error_msg) > 4000:
                error_msg = error_msg[:4000] + "..."
                
            await update.effective_message.reply_text(error_msg)
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")


# timestamp Helper function
def format_timestamp(iso_string: str, timezone: str = "UTC") -> str:
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        try:
            tz = pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.warning(f"Invalid timezone '{timezone}', falling back to UTC")
            tz = pytz.UTC
        dt = dt.astimezone(tz)
        return dt.strftime("%b %d, %Y at %I:%M %p %Z")
    except Exception as e:
        logger.error(f"Error formatting timestamp: {e}")
        return iso_string


class FirestoreDB:
    def __init__(self):
        self.project_id = os.getenv("FIREBASE_PROJECT_ID")
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
                return {"stats": {}, "groups": {}, "timezone": "UTC"}

            data = response.json()
            return self.parse_document(data)
        except Exception as e:
            logger.error(f"Get user error: {e}")
            return {"stats": {}, "groups": {}, "timezone": "UTC"}

    async def set_user(self, user_id: str, data: Dict[str, Any]) -> None:
        """Set user data in Firestore using REST API (FULL OVERWRITE - Use sparingly)"""
        try:
            firestore_doc = self.to_firestore_document(data)
            response = await self.client.patch(
                f"{self.base_url}/users/{user_id}",
                headers={"Content-Type": "application/json"},
                content=json.dumps({"fields": firestore_doc}),
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Set user error: {e}")

    async def update_user_fields(self, user_id: str, data: Dict[str, Any], field_paths: List[str]) -> None:
        """Update specific fields in a user document using updateMask."""
        try:
            firestore_doc = self.to_firestore_document(data)
            params = {"updateMask.fieldPaths": field_paths}
            response = await self.client.patch(
                f"{self.base_url}/users/{user_id}",
                params=params,
                headers={"Content-Type": "application/json"},
                content=json.dumps({"fields": firestore_doc}),
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Update user fields error: {e}")
            raise

    async def add_entry(self, user_id: str, category: str, entry: Dict[str, Any]) -> None:
        """Add an entry to a category. Uses Read-Modify-Write but restricted to the specific category."""
        # Note: REST API array transforms are complex; using targeted updateMask as a major safety improvement
        user_data = await self.get_user(user_id)
        if category not in user_data["stats"]:
            raise ValueError(f"Category {category} does not exist")
        
        entries = user_data["stats"][category].get("entries", [])
        entries.append(entry)
        
        # We only update the specific category's entries array
        update_data = {
            "stats": {
                category: {
                    "entries": entries
                }
            }
        }
        await self.update_user_fields(user_id, update_data, [f"stats.{category}.entries"])

    async def create_category(self, user_id: str, category: str) -> None:
        """Create a new category container."""
        update_data = {
            "stats": {
                category: {
                    "entries": [],
                    "created_at": datetime.utcnow().isoformat() + "Z"
                }
            }
        }
        # updateMask ensures we don't touch existing categories
        await self.update_user_fields(user_id, update_data, [f"stats.{category}"])

    async def delete_category(self, user_id: str, category: str) -> None:
        """Delete a category by removing it from the stats map."""
        # In REST API, deleting a nested map key requires sending the whole parent or using updateMask
        # However, simple way is to use the transform 'REMOVE' if supported, but here we use updateMask
        # Actually, to delete a field in REST, we omit it from updateMask or use a specific syntax
        # For now, we'll stick to the safer pattern of reading stats and writing back just stats
        user_data = await self.get_user(user_id)
        if "stats" in user_data and category in user_data["stats"]:
            del user_data["stats"][category]
            await self.update_user_fields(user_id, {"stats": user_data["stats"]}, ["stats"])

    async def update_timezone(self, user_id: str, timezone: str) -> None:
        await self.update_user_fields(user_id, {"timezone": timezone}, ["timezone"])

    async def update_groups(self, user_id: str, groups: Dict[str, List[str]]) -> None:
        await self.update_user_fields(user_id, {"groups": groups}, ["groups"])


    def parse_document(self, doc):
        """Parse Firestore document"""
        if "fields" not in doc:
            return {}

        result = {}
        for key, value in doc["fields"].items():
            result[key] = self.parse_value(value)
        return result

    def parse_value(self, value):
        """Parse Firestore value types"""
        if "stringValue" in value:
            return value["stringValue"]
        elif "integerValue" in value:
            return int(value["integerValue"])
        elif "doubleValue" in value:
            return float(value["doubleValue"])
        elif "booleanValue" in value:
            return value["booleanValue"]
        elif "mapValue" in value:
            return self.parse_document(value["mapValue"])
        elif "arrayValue" in value:
            return [self.parse_value(v) for v in value["arrayValue"].get("values", [])]
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
            return {"stringValue": value}
        elif isinstance(value, int):
            return {"integerValue": value}
        elif isinstance(value, float):
            return {"doubleValue": value}
        elif isinstance(value, bool):
            return {"booleanValue": value}
        elif isinstance(value, list):
            return {
                "arrayValue": {"values": [self.to_firestore_value(v) for v in value]}
            }
        elif isinstance(value, dict):
            return {"mapValue": {"fields": self.to_firestore_document(value)}}
        return {"nullValue": None}


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
/history_f - See soft deletes
/group - Add category groups
/delete - Delete a category
/r - Delete entries
/timezone - Set your timezone
/help - Show this message"""

    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /new command"""
    if not context.args:
        await update.message.reply_text(
            "Usage: /new <category_name>\n"
            "Example: /new weight\n"
            "Example: /new study_hours"
        )
        return

    category = "_".join(context.args).lower()
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    user_data = await db.get_user(user_id)

    if category in user_data["stats"]:
        await update.message.reply_text(f"Category '{category}' already exists!")
        return

    await db.create_category(user_id, category)
    await update.message.reply_text(
        f"✅ Created new category: <b>{category}</b>\n"
        f"Use /add {category} &lt;value&gt; to log entries!",
        parse_mode="HTML",
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

    note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    user_data = await db.get_user(user_id)

    # TIMEZONE ENFORCEMENT
    if "timezone" not in user_data or not user_data["timezone"]:
        await update.message.reply_text(
            "❌ Please set your timezone first using /timezone\n"
            "Example: /timezone Australia/Sydney\n"
            "Example: /timezone Asia/Ho_Chi_Minh"
        )
        return

    if category not in user_data["stats"]:
        await update.message.reply_text(
            f"❌ Category '{category}' doesn't exist.\n"
            f"Create it first with: /new {category}"
        )
        return

    # Create timestamp in local timezone (NEW)
    user_tz = pytz.timezone(user_data["timezone"])
    local_time = datetime.now(pytz.UTC).astimezone(
        user_tz
    )  # Convert current UTC to local time

    entry = {
        "value": value,
        "note": note,
        "timestamp": local_time.isoformat(),  # Local time with offset
        "timezone": user_data["timezone"],  # Store timezone context
    }

    await db.add_entry(user_id, category, entry)


    response = f"✅ Added to <b>{category}</b>: {value}"
    if note:
        response += f"\n📝 Note: {note}"

    # Show the local time it was recorded
    formatted_time = format_timestamp(local_time.isoformat(), user_data["timezone"])
    response += f"\n🕒 Recorded at: {formatted_time}"

    await update.message.reply_text(response, parse_mode="HTML")


async def handle_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /view command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    user_data = await db.get_user(user_id)
    all_stats = user_data["stats"]
    all_groups = user_data["groups"]

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
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🗂️ {group_name}", callback_data=f"viewgroup_{group_name}"
                )
            ]
        )

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
        parse_mode="Markdown",
        reply_markup=keyboard,
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
    db = context.bot_data["db"]

    user_data = await db.get_user(user_id)

    # Check if it's a category
    if target in user_data["stats"]:
        await db.delete_category(user_id, target)
        await update.message.reply_text(f"✅ Deleted category: {target}")

    # Check if it's a group
    elif "groups" in user_data and target in user_data["groups"]:
        del user_data["groups"][target]
        await db.update_groups(user_id, user_data["groups"])
        await update.message.reply_text(f"✅ Deleted group: {target}")


    else:
        await update.message.reply_text(f"❌ Category or group '{target}' not found")


async def handle_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /timezone command"""
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    if not context.args:
        user_data = await db.get_user(user_id)
        current_tz = user_data.get("timezone", "Not set")

        await update.message.reply_text(
            f"⏰ *Current timezone:* {current_tz}\n\n"
            f"Future entries will use this timezone.\n\n"
            f"To change, use: /timezone <timezone>\n\n"
            f"*Examples:*\n"
            f"/timezone Australia/Sydney\n"
            f"/timezone Asia/Ho_Chi_Minh\n"
            f"/timezone America/New_York\n\n"
            f"Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
            parse_mode="Markdown",
        )
        return

    timezone = "_".join(context.args)

    # Basic validation
    if "/" not in timezone:
        await update.message.reply_text(
            f"❌ Invalid timezone format: {timezone}\n\n"
            f"Use format like: America/New_York or Asia/Tokyo\n"
            f"Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        )
        return

    user_data = await db.get_user(user_id)
    await db.update_timezone(user_id, timezone)


    await update.message.reply_text(
        f"✅ Timezone set to: *{timezone}*", parse_mode="Markdown"
    )


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
    db = context.bot_data["db"]

    # Verify all categories exist
    user_data = await db.get_user(user_id)
    missing_categories = [cat for cat in categories if cat not in user_data["stats"]]

    if missing_categories:
        await update.message.reply_text(
            f"❌ These categories don't exist: {', '.join(missing_categories)}\n"
            "Create them first with /new"
        )
        return

    # Save the group
    if "groups" not in user_data:
        user_data["groups"] = {}
    user_data["groups"][group_name] = categories

    await db.update_groups(user_id, user_data["groups"])

    await update.message.reply_text(
        f"✅ Created group *{group_name}*\nContains: {', '.join(categories)}",
        parse_mode="Markdown",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboards"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"GENERAL CALLBACK RECEIVED: {data}")  # ADD THIS LINE
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    if data == "view_main":
        # Return to main view
        user_data = await db.get_user(user_id)
        all_stats = user_data.get("stats", {})
        all_groups = user_data.get("groups", {})

        grouped_cats = set()
        for group_categories in all_groups.values():
            if isinstance(group_categories, list):
                grouped_cats.update(group_categories)

        ungrouped = [cat for cat in all_stats.keys() if cat not in grouped_cats]

        buttons = []
        for cat in ungrouped:
            buttons.append(
                [InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")]
            )

        for group_name in all_groups.keys():
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗂️ {group_name}", callback_data=f"viewgroup_{group_name}"
                    )
                ]
            )

        if not buttons:
            await query.edit_message_text(
                "You don't have any categories or groups yet!\n"
                "Create one with: /new <name> or /group <group> <cat1> [cat2] ..."
            )
            return

        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            "📊 *Your Stats and Groups:*\nSelect one to view details.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    elif data.startswith("view_"):
        category = data.replace("view_", "")
        # Set context.args so handle_history knows which category to show
        context.args = [category]
        # Delete the message with the buttons
        await query.delete_message()
        # Call the history handler, which will send a new message
        await handle_history(update, context)

    elif data.startswith("viewgroup_"):
        group_name = data.replace("viewgroup_", "")
        user_data = await db.get_user(user_id)

        if group_name not in user_data["groups"]:
            await query.edit_message_text(f"Group '{group_name}' not found.")
            return

        categories = user_data["groups"][group_name]

        # Create buttons for each category in the group
        buttons = []
        for cat in categories:
            buttons.append(
                [InlineKeyboardButton(f"📈 {cat}", callback_data=f"view_{cat}")]
            )

        # Add back button
        buttons.append(
            [
                InlineKeyboardButton(
                    "« Back to All Categories", callback_data="view_main"
                )
            ]
        )

        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            f"📂 *Group: {group_name}*\n\nSelect a category to view its latest entry:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


async def handle_migrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual migration command for entries before November 1, 2025 with timezone-aware comparison"""
    user_id = str(update.effective_user.id)
    db = context.bot_data["db"]

    # Check for dry-run mode
    dry_run = len(context.args) > 0 and context.args[0].lower() == "dry-run"

    if dry_run:
        await update.message.reply_text(
            "🔍 Running migration in DRY-RUN mode (no changes will be saved)"
        )
    else:
        await update.message.reply_text(
            "🔄 Starting timezone migration for entries before Nov 1, 2025..."
        )

    try:
        user_data = await db.get_user(user_id)
        if not user_data.get("stats"):
            await update.message.reply_text("ℹ️ No stats found to migrate")
            return

        migrated_count = 0
        error_count = 0
        category_report = []
        detailed_errors = []

        # FIX: Make cutoff time timezone-aware (UTC)
        cutoff_time = datetime(2025, 11, 1, 12, 0, 0, tzinfo=pytz.UTC)
        logger.info(f"Migration cutoff time (UTC): {cutoff_time}")

        for category_name, category_data in user_data["stats"].items():
            category_migrated = 0
            category_errors = 0

            for i, entry in enumerate(category_data["entries"]):
                try:
                    timestamp_str = entry["timestamp"]
                    logger.info(
                        f"Processing entry {i} in {category_name}: {timestamp_str}"
                    )

                    # Parse timestamp (already UTC-aware from the Z suffix)
                    if timestamp_str.endswith("Z"):
                        entry_time = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        ).replace(tzinfo=pytz.UTC)
                    else:
                        entry_time = datetime.fromisoformat(timestamp_str)
                        # Ensure it's timezone-aware
                        if entry_time.tzinfo is None:
                            entry_time = entry_time.replace(tzinfo=pytz.UTC)

                    logger.info(f"Parsed timestamp (UTC): {entry_time}")

                    # FIX: Now both times are timezone-aware, comparison should work
                    is_before_cutoff = entry_time < cutoff_time
                    logger.info(f"Before cutoff {cutoff_time}? {is_before_cutoff}")

                    if is_before_cutoff:
                        category_migrated += 1
                        logger.info(f"Entry qualifies for migration")

                        if not dry_run:
                            entry["timezone"] = "Asia/Ho_Chi_Minh"
                            hcmc_tz = pytz.timezone("Asia/Ho_Chi_Minh")
                            entry_local_time = entry_time.astimezone(hcmc_tz)
                            entry["timestamp"] = entry_local_time.isoformat()
                            logger.info(f"Migrated to: {entry['timestamp']}")

                except Exception as e:
                    error_msg = f"Category '{category_name}', entry {i}, timestamp '{entry.get('timestamp', 'MISSING')}': {str(e)}"
                    logger.error(error_msg)
                    detailed_errors.append(error_msg)
                    category_errors += 1
                    error_count += 1

            if category_migrated > 0 or category_errors > 0:
                category_report.append(
                    f"• {category_name}: {category_migrated} migrated, {category_errors} errors"
                )

            migrated_count += category_migrated

        if not dry_run and migrated_count > 0:
            await db.update_user_fields(user_id, {"stats": user_data["stats"]}, ["stats"])
            logger.info(f"Saved {migrated_count} migrated entries for user {user_id}")


        # Build report message
        report_lines = []

        if dry_run:
            report_lines.append("📊 *DRY-RUN RESULTS* (Entries before Nov 1, 2025)")
        else:
            report_lines.append("📊 *MIGRATION RESULTS* (Entries before Nov 1, 2025)")

        report_lines.append(f"Total entries migrated: {migrated_count}")
        report_lines.append(f"Total errors: {error_count}")

        if category_report:
            report_lines.append("\n*Category Breakdown:*")
            report_lines.extend(category_report)

        if detailed_errors and len(detailed_errors) > 0:
            report_lines.append(
                f"\n*Sample errors ({min(3, len(detailed_errors))} of {len(detailed_errors)}):*"
            )
            for error in detailed_errors[:3]:
                report_lines.append(f"• {error}")

        if migrated_count == 0 and error_count == 0:
            report_lines.append("\n✅ No entries found before November 1, 2025")
        elif dry_run and migrated_count > 0:
            report_lines.append(
                f"\n💡 Dry-run: {migrated_count} entries would be migrated"
            )
            report_lines.append("Run without 'dry-run' to apply changes")

        await update.message.reply_text("\n".join(report_lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Migration failed for user {user_id}: {e}")
        await update.message.reply_text(
            f"❌ Migration failed: {str(e)}\nCheck logs for details."
        )


def create_application():
    """Create and configure the Telegram Bot Application"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

    # Create application
    application = Application.builder().token(token).build()

    # Add database to bot_data
    application.bot_data["db"] = FirestoreDB()

    # Add handlers
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("help", handle_start))
    application.add_handler(CommandHandler("new", handle_new))
    application.add_handler(CommandHandler("add", handle_add))
    application.add_handler(CommandHandler("view", handle_view))
    application.add_handler(CommandHandler("history", handle_history))
    application.add_handler(CommandHandler("history_f", handle_history_f))

    application.add_handler(CommandHandler("r", handle_r))
    application.add_handler(CommandHandler("delete", handle_delete))
    application.add_handler(CommandHandler("timezone", handle_timezone))
    application.add_handler(CommandHandler("group", handle_group))
    application.add_handler(CommandHandler("migrate", handle_migrate))

    application.add_handler(
        MessageHandler(filters.COMMAND, handle_unrecognized_command)
    )
    # REORDER AND UPDATE PATTERNS:
    application.add_handler(
        CallbackQueryHandler(
            handle_delete_callback, pattern="^(confirm_delete_|cancel_delete)"
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_recover_callback, pattern="^recover_")
    )
    application.add_handler(
        CallbackQueryHandler(handle_callback)
    )  # Catch-all handler LAST

    # Add error handler
    application.add_error_handler(error_handler)

    return application


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
        if "db" in telegram_app.bot_data:
            await telegram_app.bot_data["db"].close()
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

        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse(
        content={
            "status": "healthy",
            "bot_initialized": telegram_app is not None,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse(
        content={
            "status": "Telegram Stats Bot is running!",
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


# Optional: Endpoint to set webhook (call this once after deployment)
@app.post("/set-webhook")
async def set_webhook(webhook_url: str):
    """Set the Telegram webhook URL"""
    try:
        if telegram_app is None:
            raise HTTPException(status_code=503, detail="Bot not initialized")

        await telegram_app.bot.set_webhook(url=webhook_url)
        return JSONResponse(content={"status": "success", "webhook_url": webhook_url})
    except Exception as e:
        logger.error(f"Set webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Run with: uvicorn main:app --host 0.0.0.0 --port 8000
