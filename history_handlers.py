"""
History-related command handlers for Stats Tracker Bot
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
import pytz
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

def format_timestamp(iso_string: str, timezone: str = 'UTC') -> str:
    """Format ISO timestamp to readable string using the provided timezone"""
    try:
        dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
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

    # CORRECTED Date filtering
    if days_back is not None and days_forward is not None:
        # Get user timezone
        try:
            user_tz = pytz.timezone(user_data['timezone'])
        except pytz.exceptions.UnknownTimeZoneError:
            await update.effective_message.reply_text(
                f"❌ Invalid timezone setting: '{user_data['timezone']}'\n"
                f"Please set a valid timezone using /settimezone"
            )
            return

        # Get current time in user's timezone
        now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)
        now_user = now_utc.astimezone(user_tz)

        # Calculate date range in user's timezone
        user_midnight = user_tz.localize(datetime(now_user.year, now_user.month, now_user.day))

        start_date = user_midnight - timedelta(days=abs(days_back))
        end_date = user_midnight - timedelta(days=days_forward)

        # Adjust end date to be inclusive of the target day
        if days_forward <= 0:
            end_date += timedelta(days=1)

        logger.info(f"Date range in user timezone: {start_date} to {end_date}")

        filtered_entries = []
        for entry in entries:
            try:
                # Parse timestamp with backward compatibility for both formats
                timestamp_str = entry['timestamp']

                # Handle both timezone-aware and naive timestamps
                if timestamp_str.endswith('Z'):
                    # UTC timestamp - make it timezone-aware
                    entry_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=pytz.UTC)
                elif '+' in timestamp_str:
                    # Already timezone-aware format
                    entry_dt = datetime.fromisoformat(timestamp_str)
                else:
                    # Naive timestamp (old format) - assume UTC and make timezone-aware
                    entry_dt = datetime.fromisoformat(timestamp_str).replace(tzinfo=pytz.UTC)

                # Convert to user timezone for date comparison
                entry_user_tz = entry_dt.astimezone(user_tz)

                if start_date <= entry_user_tz < end_date:
                    filtered_entries.append(entry)

            except ValueError as e:
                logger.warning(f"Invalid timestamp format for entry: {entry.get('timestamp')} - {e}")
                continue

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

    # Group entries by LOCAL date (not UTC)
    date_groups = []
    current_local_date = None
    current_group = []

    for entry in entries:
        # Get the entry's timezone and convert to local date
        entry_tz = pytz.timezone(entry.get('timezone', 'UTC'))
        entry_local_time = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00')).astimezone(entry_tz)
        entry_local_date = entry_local_time.date()

        if entry_local_date != current_local_date:
            if current_group:
                date_groups.append((current_local_date, current_group))
            current_local_date = entry_local_date
            current_group = []
        current_group.append(entry)

    if current_group:
        date_groups.append((current_local_date, current_group))

    # Build messages with smart chunking
    messages = []
    current_message = f"📊 *History for {category}:*\n\n"

    for i, (local_date, group_entries) in enumerate(date_groups):
        group_text = ""
        if i > 0:
            group_text += '─────────────────\n'

        for entry in group_entries:
            tag = f"[{entry['category']}] " if 'category' in entry else ''
            # Use entry's own timezone for display
            entry_timezone = entry.get('timezone', 'UTC')
            formatted_time = format_timestamp(entry['timestamp'], entry_timezone)
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
