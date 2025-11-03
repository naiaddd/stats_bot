


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
    cutoff_time = datetime(2024, 11, 1, 12, 0, 0)

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
