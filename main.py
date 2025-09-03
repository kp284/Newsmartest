# main.py
"""
The main entry point for the Telegram Promotion Bot.

This script initializes the bot, sets up all the handlers from the 'handlers' module,
schedules the recurring jobs from the 'jobs' module, and starts the polling process.
"""

import logging
from datetime import time
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
# The CronTrigger is needed for the custom weekly job
from apscheduler.triggers.cron import CronTrigger

import config
import handlers
import jobs
import database as db

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# PTB's logs can be verbose, so we can set its logger to a higher level
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram.ext').setLevel(logging.INFO)

logger = logging.getLogger(__name__)

async def post_init(application: Application) -> None:
    """
    A function to run after the bot is initialized but before polling starts.
    Used for setting up the database.
    """
    logger.info("Initializing database...")
    await db.initialize_database()
    logger.info("Database initialized.")


def main() -> None:
    """Start the bot."""
    # --- Application Setup ---
    application = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    
    # === Conversation Handlers ===

    # 1. User: Set Normal Promotion Link
    normal_link_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.promote_normal_link_start, pattern='^set_normal_link$')],
        states={
            handlers.LINK_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_link_text)],
            handlers.LINK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_link_url)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation), CallbackQueryHandler(handlers.start)],
        per_message=False,
    )
    
    # 2. User: Set Force-Join Channel
    force_channel_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.set_force_channel_start, pattern='^set_force_channel$')],
        states={
            handlers.CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_channel_id)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)],
        per_message=False,
    )
    
    # 3. User: Create a Promotion (select type & budget)
    create_promotion_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.create_promotion_start, pattern='^create_promotion$')],
        states={
            handlers.AWAIT_PROMO_TYPE_FOR_CREATION: [CallbackQueryHandler(handlers.get_promotion_type_for_creation, pattern='^create_promo_')],
            handlers.AWAIT_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_promotion_budget)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation), CallbackQueryHandler(handlers.start, pattern='^back_to_main$')],
        per_message=False,
    )
    
    # 4. Premium User: Image with Caption Broadcast
    premium_broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.premium_broadcast_start, pattern='^premium_broadcast$')],
        states={
            handlers.AWAIT_IMAGE_FOR_BROADCAST: [MessageHandler(filters.PHOTO, handlers.get_image_for_broadcast)],
            handlers.AWAIT_BROADCAST_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_broadcast_count)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)],
        per_message=False,
    )
    
    # --- Admin Conversation Handlers ---
    
    # 5. Admin: Broadcast Message
    broadcast_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.admin_broadcast_start, pattern='^admin_broadcast$')],
        states={
            handlers.BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, handlers.get_broadcast_message)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)],
        per_message=False,
    )
    
    # 6. Admin: Add Premium
    add_premium_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handlers.admin_add_premium_start, pattern='^admin_add_premium$')],
        states={
            handlers.AWAIT_USER_ID_FOR_PREMIUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_user_id_for_premium)],
            handlers.AWAIT_PREMIUM_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_premium_days)]
        },
        fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)],
        per_message=False,
    )
    
    # 7. Other Admin Handlers (single step)
    remove_premium_handler = ConversationHandler(entry_points=[CallbackQueryHandler(handlers.admin_remove_premium_start, pattern='^admin_remove_premium$')], states={handlers.AWAIT_USER_ID_FOR_REMOVE_PREMIUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_user_id_for_remove_premium)]}, fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)], per_message=False)
    ban_handler = ConversationHandler(entry_points=[CallbackQueryHandler(handlers.admin_ban_user_start, pattern='^admin_ban_user$')], states={handlers.AWAIT_USER_ID_FOR_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_user_id_for_ban)]}, fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)], per_message=False)
    unban_handler = ConversationHandler(entry_points=[CallbackQueryHandler(handlers.admin_unban_user_start, pattern='^admin_unban_user$')], states={handlers.AWAIT_USER_ID_FOR_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_user_id_for_unban)]}, fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)], per_message=False)
    stats_handler = ConversationHandler(entry_points=[CallbackQueryHandler(handlers.admin_get_stats_start, pattern='^admin_stats$')], states={handlers.AWAIT_USER_ID_FOR_STATS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.get_user_id_for_stats)]}, fallbacks=[CommandHandler('cancel', handlers.cancel_conversation)], per_message=False)

    # --- Handler Registration ---
    application.add_handler(CommandHandler('start', handlers.start))
    application.add_handler(CommandHandler('group', handlers.group_command))
    
    # Add all conversation handlers
    application.add_handler(normal_link_handler)
    application.add_handler(force_channel_handler)
    application.add_handler(create_promotion_handler)
    application.add_handler(premium_broadcast_handler)
    application.add_handler(broadcast_handler)
    application.add_handler(add_premium_handler)
    application.add_handler(remove_premium_handler)
    application.add_handler(ban_handler)
    application.add_handler(unban_handler)
    application.add_handler(stats_handler)
    
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handlers.new_group_member))
    application.add_handler(MessageHandler(filters.FORWARDED, handlers.handle_report_forward))
    application.add_handler(CallbackQueryHandler(handlers.button_handler)) # General button handler

    # --- Job Queue Setup ---
    job_queue = application.job_queue
    job_queue.run_daily(jobs.daily_credit_reset, time=time(0, 0), name="daily_reset")
    job_queue.run_daily(jobs.reset_image_broadcasts, time=time(0, 0), name="daily_image_broadcast_reset")
    
    # Corrected method for scheduling weekly job for PTB v20+
    job_queue.run_custom(
        jobs.weekly_leaderboard_reset,
        job_kwargs={"trigger": CronTrigger(day_of_week="sun", hour=0, minute=0)},
        name="weekly_reset"
    )

    # --- Start Bot ---
    logger.info("Starting bot polling...")
    application.run_polling()


if __name__ == '__main__':
    main()

