"""
History-related command handlers for Stats Tracker Bot
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

    entries = [entry for entry in entries if not entry.get('is_deleted', False)]


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

















# Add to history_handlers.py or in app.py

async def handle_r(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /r command for entry deletion"""
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    # Show usage help if no arguments
    if not context.args:
            help_text = (
                "🗑️ Entry Deletion Commands:\n\n"
                "/r <category> - Show entries with indices\n"
                "/r <category> <indices> -s - Soft delete (recoverable)\n"
                "/r <category> <indices> -h - Hard delete (permanent)\n\n"
                "Supported formats:\n"
                "/r weight 1,2 -s - Delete entries 1 & 2\n"
                "/r weight 1, 2 -s - Delete entries 1 & 2\n"
                "/r weight 1-3 -h - Delete entries 1,2,3\n"
                "/r weight 1-3,5 -s - Delete entries 1,2,3,5\n\n"
                "Use /history_f <category> to view and recover deleted entries."
            )
            await update.message.reply_text(help_text)  # Remove parse_mode='Markdown'
            return

    category = context.args[0].lower()

    # Get user data
    user_data = await db.get_user(user_id)

    # Validate category exists
    if category not in user_data.get('stats', {}):
        await update.message.reply_text(f"❌ Category '{category}' not found.")
        return

    entries = user_data['stats'][category]['entries']

    # Show entries with indices if no deletion flags
    if len(context.args) == 1 or (len(context.args) == 2 and context.args[1] not in ['-s', '-h']):
        await _show_entries_with_indices(update, category, entries)
        return

    # Parse deletion command
    await _handle_deletion_command(update, context, user_data, category, entries, user_id, db)

async def _show_entries_with_indices(update: Update, category: str, entries: List[Dict]) -> None:
    """Show entries with their current indices"""
    if not entries:
        await update.message.reply_text(f"ℹ️ No entries found for '{category}'.")
        return

    # Filter out deleted entries for display
    active_entries = [entry for entry in entries if not entry.get('is_deleted', False)]

    if not active_entries:
        await update.message.reply_text(
            f"ℹ️ No active entries found for '{category}'.\n"
            f"Use `/history_f {category}` to view deleted entries."
        )
        return

    # Reverse to show newest first
    active_entries.reverse()

    message = f"📋 Current entries for '{category}' (newest first):\n\n"

    for i, entry in enumerate(active_entries, 1):
        formatted_time = format_timestamp(entry['timestamp'], entry.get('timezone', 'UTC'))
        message += f"{i}. {entry['value']} - {formatted_time}"
        if entry.get('note'):
            message += f" - {entry['note']}"
        message += "\n"

    message += "\nEnter: `/r <category> <indices> -s`\n"
    message += "*Examples:*\n"
    message += "`/r {category} 2 -s` - Soft delete entry #2\n"
    message += "`/r {category} 1,3 -s` - Soft delete entries 1 & 3\n"
    message += "`/r {category} 1-3 -s` - Soft delete entries 1,2,3\n"
    message += "`/r {category} 2 -h` - Hard delete entry #2"

    await update.message.reply_text(message, parse_mode='Markdown')


















async def _handle_deletion_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 user_data: Dict, category: str, entries: List[Dict],
                                 user_id: str, db) -> None:
    """Handle the actual deletion command with indices and flags"""
    try:
        # Find the deletion flag position
        delete_flag = None
        flag_position = -1

        for i, arg in enumerate(context.args[1:], 1):
            if arg in ['-s', '-h']:
                delete_flag = arg
                flag_position = i
                break

        if not delete_flag:
            await update.message.reply_text(
                "❌ Missing deletion flag. Use `-s` for soft delete or `-h` for hard delete.\n"
                "Example: `/r test 1,2 -s` or `/r test 1, 2 -h`"
            )
            return

        # Join all arguments between category and flag as indices string
        if flag_position > 1:  # There are indices between category and flag
            indices_parts = context.args[1:flag_position]
            indices_str = ' '.join(indices_parts)
        else:
            indices_str = ""

        # Parse indices (support for single, multiple, and ranges)
        target_indices = await _parse_indices(indices_str, entries)

        if not target_indices:
            await update.message.reply_text(
                "❌ Invalid indices format. Use:\n"
                "• Single: `2`\n"
                "• Multiple: `1,3,5` or `1, 3, 5`\n"
                "• Range: `1-5`\n"
                "• Mixed: `1,3-5,7` or `1, 3-5, 7`"
            )
            return

        # Validate indices are within range
        active_entries = [entry for entry in entries if not entry.get('is_deleted', False)]
        active_entries.reverse()  # Newest first for index matching

        max_index = len(active_entries)
        invalid_indices = [idx for idx in target_indices if idx < 1 or idx > max_index]

        if invalid_indices:
            await update.message.reply_text(
                f"❌ Invalid indices: {invalid_indices}\n"
                f"Valid range: 1-{max_index}\n"
                f"Use `/r {category}` to see current indices."
            )
            return

        # Get the actual entries to delete
        entries_to_delete = []
        for idx in target_indices:
            # Convert display index (1-based, newest first) to storage index
            display_index = idx - 1  # Convert to 0-based
            target_entry = active_entries[display_index]
            # Find the original index in the main entries list
            storage_index = next(
                i for i, entry in enumerate(entries)
                if entry.get('timestamp') == target_entry.get('timestamp')
                and entry.get('value') == target_entry.get('value')
                )

            entries_to_delete.append({
                'index': storage_index,
                'entry': target_entry
            })

        # Show confirmation
        await _show_deletion_confirmation(update, category, entries_to_delete, delete_flag)

    except Exception as e:
        logger.error(f"Error parsing deletion command: {e}")


        await update.message.reply_text(
            "Error parsing command. Use:\n"
            "`/r <category> <indices> -s` for soft delete\n"
            "`/r <category> <indices> -h` for hard delete\n\n"
            "Supported formats:\n"
            "• `/r test 1,2 -s`\n"
            "• `/r test 1, 2 -s`\n"
            "• `/r test 1-5 -h`\n"
            "• `/r test 1-3,5 -s`",
            parse_mode=None
        )






















async def _parse_indices(indices_str: str, entries: List[Dict]) -> List[int]:
    """Parse indices string into list of integers"""
    if not indices_str or not indices_str.strip():
        return []

    try:
        indices = set()
        # Remove spaces for cleaner parsing, then split by commas
        cleaned_str = indices_str.replace(' ', '')
        parts = cleaned_str.split(',')

        for part in parts:
            part = part.strip()
            if not part:  # Skip empty parts from trailing commas
                continue
            if '-' in part:
                # Range like 1-5
                start, end = map(int, part.split('-'))
                indices.update(range(start, end + 1))
            else:
                # Single number
                indices.add(int(part))

        return sorted(list(indices))
    except (ValueError, AttributeError):
        return []


















async def _show_deletion_confirmation(update: Update, category: str,
                                    entries_to_delete: List[Dict], delete_flag: str) -> None:
    """Show confirmation message with inline keyboard"""
    delete_type = "soft" if delete_flag == '-s' else "hard"
    emoji = "🗑️" if delete_flag == '-s' else "💥"

    message = f"{emoji} {delete_type.capitalize()} delete these {len(entries_to_delete)} entries from '{category}'?\n\n"

    for item in entries_to_delete:
        entry = item['entry']
        formatted_time = format_timestamp(entry['timestamp'], entry.get('timezone', 'UTC'))
        message += f"• {entry['value']} - {formatted_time}"
        if entry.get('note'):
            message += f" - {entry['note']}"
        message += "\n"

    if delete_flag == '-s':
        message += "\nSoft deleted entries can be recovered with /history_f"
    else:
        message += "\n⚠️ Hard deletion is PERMANENT and cannot be undone"

    # Create confirmation keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                f"✅ Confirm {delete_type.capitalize()} Delete",
                callback_data=f"confirm_delete_{category}_{delete_flag}_{','.join(str(item['index']) for item in entries_to_delete)}"
            )
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)


















# Add this to the callback handler section
async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deletion confirmation callbacks"""
    query = update.callback_query
    await query.answer()

    data = query.data
    logger.info(f"DELETE CALLBACK RECEIVED: {data}")
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    if data == "cancel_delete":
        await query.edit_message_text("❌ Deletion cancelled.")
        return

    if data.startswith("confirm_delete_"):
        try:
            # Parse callback data: confirm_delete_category_flag_indices
            parts = data.split('_')
            category = parts[2]
            delete_flag = parts[3]  # -s or -h
            indices_str = parts[4] if len(parts) > 4 else ""

            storage_indices = [int(idx) for idx in indices_str.split(',')] if indices_str else []

            # Get current user data
            user_data = await db.get_user(user_id)
            entries = user_data['stats'][category]['entries']

            deleted_count = 0
            entries_modified = False

            for storage_idx in storage_indices:
                if 0 <= storage_idx < len(entries):
                    if delete_flag == '-s':
                        entries[storage_idx]['is_deleted'] = True
                        entries_modified = True
                        deleted_count += 1
                    else:  # -h
                        # Hard delete - create new list excluding the indices to delete
                        entries_to_keep = []
                        for i, entry in enumerate(entries):
                            if i not in storage_indices:
                                entries_to_keep.append(entry)
                            else:
                                entries_modified = True
                                deleted_count += 1
                        # Replace the entries list with the filtered list
                        user_data['stats'][category]['entries'] = entries_to_keep


                if entries_modified:
                    await db.set_user(user_id, user_data)

                    if delete_flag == '-s':
                        await query.edit_message_text(
                            f"✅ {deleted_count} entries soft deleted from '{category}'.\n"
                            f"Use `/history_f {category}` to view or recover deleted entries."
                        )
                    else:
                        await query.edit_message_text(
                            f"💥 {deleted_count} entries permanently deleted from '{category}'."
                        )
                else:
                    await query.edit_message_text("❌ No entries were deleted. They may have been modified since confirmation.")


        except Exception as e:
            logger.error(f"Error processing deletion callback: {e}")
            logger.error(f"Callback data: {data}")
            logger.error(f"Parsed parts: category={category}, flag={delete_flag}, indices={storage_indices}")
            await query.edit_message_text("❌ Error processing deletion. Please try again.")












async def handle_history_f(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history_f command to show full history with recovery options"""
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: `/history_f <category>`\n"
            "Shows all entries including deleted ones with recovery options.",
            parse_mode='Markdown'
        )
        return

    category = context.args[0].lower()
    user_id = str(update.effective_user.id)
    db = context.bot_data['db']

    user_data = await db.get_user(user_id)

    if category not in user_data.get('stats', {}):
        await update.effective_message.reply_text(f"❌ Category '{category}' not found.")
        return

    entries = user_data['stats'][category]['entries']

    if not entries:
        await update.effective_message.reply_text(f"ℹ️ No entries found for '{category}'.")
        return

    # Group entries by local date (including deleted ones)
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

    # Build message with recovery buttons for deleted entries
    messages = []
    current_message = f"📊 *Full History for {category}:*\n\n"

    for i, (local_date, group_entries) in enumerate(date_groups):
        group_text = ""
        if i > 0:
            group_text += '─────────────────\n'

        for entry in group_entries:
            is_deleted = entry.get('is_deleted', False)
            status_emoji = "❌" if is_deleted else "✅"

            tag = f"[{entry['category']}] " if 'category' in entry else ''
            formatted_time = format_timestamp(entry['timestamp'], entry.get('timezone', 'UTC'))

            group_text += f"• {status_emoji} {tag}{entry['value']} - {formatted_time}"

            if entry.get('note'):
                group_text += f" - {entry['note']}"

            # Add recovery button for deleted entries
            if is_deleted:
                # Find the entry index for recovery
                entry_index = entries.index(entry)
                group_text += f" [🔄 Recover]"

            group_text += "\n"

        if len(current_message + group_text) > 3500 and current_message.strip():
            messages.append(current_message)
            current_message = ""

        current_message += group_text

    if current_message.strip():
        messages.append(current_message)

    # Send all messages (for now without actual buttons - we'll add callback later)
    for message in messages:
        await update.effective_message.reply_text(message, parse_mode='Markdown')







# Add recovery callback handler
async def handle_recover_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle recovery of deleted entries"""
    query = update.callback_query
    await query.answer()

    # This would be implemented similarly to the deletion callbacks
    # For now, we'll add a placeholder
    await query.edit_message_text("🔄 Recovery functionality will be implemented in the next phase.")

