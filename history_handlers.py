"""
History-related command handlers for Stats Tracker Bot
"""
import pytz
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history command"""
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /history <category_or_group> [-days_back:days_forward]\n"
            "Example: /history weight\n"
            "Example: /history weight -7:0 (last 7 days, excluding today)\n"
            "Example: /history weight -7:1 (last 7 days, including today)\n"
            "Example: /history workout_group -30:-7 (from 30 days ago to 7 days ago)"
        )
        return

    category = context.args[0].lower()
    days_back = None
    days_forward = None

    # Optional date range parsing
    if len(context.args) > 1 and ':' in context.args[1]:
        try:
            back, forward = context.args[1].split(':')
            days_back = int(back)
            days_forward = int(forward)
        except ValueError:
            pass

    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)
    if not user_data['stats'] and not user_data['groups']:
        await update.effective_message.reply_text("You don't have any stats or groups yet!")
        return

    entries = []
    is_group = category in user_data['groups']

    if is_group:
        # Get entries from all categories in the group
        group_categories = user_data['groups'][category]
        for cat in group_categories:
            if cat in user_data['stats']:
                cat_entries = user_data['stats'][cat]['entries']
                for entry in cat_entries:
                    entry_with_category = entry.copy()
                    entry_with_category['category'] = cat
                    entries.append(entry_with_category)
        # Sort by timestamp
        entries.sort(key=lambda x: x['timestamp'])
    elif category in user_data['stats']:
        entries = user_data['stats'][category]['entries']
    else:
        await update.effective_message.reply_text(f"❌ No category or group named '{category}'")
        return

    if not entries:
        await update.effective_message.reply_text(f"ℹ️ No entries recorded for '{category}' yet.")
        return

    timezone = user_data['timezone']

    # CORRECTED Date filtering
    if days_back is not None and days_forward is not None:
        now = datetime.utcnow()
        today_midnight = datetime(now.year, now.month, now.day)

        # FIXED: Use absolute values and proper direction
        start_date = today_midnight - timedelta(days=abs(days_back))
        end_date = today_midnight - timedelta(days=days_forward)

        # Adjust end date to be inclusive of the target day
        if days_forward <= 0:  # -1:0 or -1:1
            end_date += timedelta(days=1)

        logger.info(f"Date range: {start_date} to {end_date}")

        filtered_entries = []
        for entry in entries:
            entry_date = datetime.fromisoformat(entry['timestamp'].replace('Z', ''))
            if start_date <= entry_date < end_date:
                filtered_entries.append(entry)

        entries = filtered_entries

    # If no entries after filtering, show message
    if not entries and days_back is not None and days_forward is not None:
        await update.effective_message.reply_text(
            f"ℹ️ No entries found for '{category}' in the specified date range.\n"
            f"Try without date filters to see all entries."
        )
        return

    # If no entries at all (even without filtering)
    if not entries:
        await update.effective_message.reply_text(f"ℹ️ No entries recorded for '{category}' yet.")
        return

    # Reverse to show newest first
    entries.reverse()

    # Group entries by date for smart chunking
    date_groups = []
    current_date = None
    current_group = []

    for entry in entries:
        entry_date = datetime.fromisoformat(entry['timestamp'].replace('Z', '')).date()
        if entry_date != current_date:
            if current_group:
                date_groups.append((current_date, current_group))
            current_date = entry_date
            current_group = []
        current_group.append(entry)

    if current_group:
        date_groups.append((current_date, current_group))

    # Build messages with smart chunking
    messages = []
    current_message = f"📊 *History for {category}:*\n\n"

    for i, (date, group_entries) in enumerate(date_groups):
        group_text = ""
        if i > 0:
            group_text += '─────────────────\n'

        for entry in group_entries:
            tag = f"[{entry['category']}] " if 'category' in entry else ''
            formatted_time = format_timestamp(entry['timestamp'], timezone)
            group_text += f"• {tag}{entry['value']} - {formatted_time}\n"
            if entry.get('note'):
                group_text += f"  _{entry['note']}_\n"

        if len(current_message + group_text) > 3500 and current_message.strip():
            messages.append(current_message)
            current_message = ""

        current_message += group_text

    if current_message.strip():
        messages.append(current_message)

    # Send all messages
    for message in messages:
        await update.effective_message.reply_text(message, parse_mode='Markdown')


# Helper function for timestamp formatting (copied from main file)
def format_timestamp(iso_string: str, timezone: str = 'UTC') -> str:
    """Format ISO timestamp to readable string"""
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
