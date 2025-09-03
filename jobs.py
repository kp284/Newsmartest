# jobs.py
"""
Contains all the scheduled job functions for the bot.

These functions are run periodically by the APScheduler job queue
to perform tasks like daily credit resets and weekly leaderboard clearing.
"""
import logging
from datetime import time
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)

async def daily_credit_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Resets daily limits and adds referral credits for all users.
    This job runs once every 24 hours.
    """
    logger.info("Running daily credit reset job...")
    
    # Logic to add referral credits to main credits and reset daily runs
    await db.execute_daily_reset()
    
    logger.info("Daily credit reset job completed.")

async def weekly_leaderboard_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Resets the 'clicks_received' counter for all users.
    This job runs once every week (e.g., on Monday).
    """
    logger.info("Running weekly leaderboard reset job...")
    
    # Logic to reset the clicks_received column
    await db.execute_weekly_reset()
    
    logger.info("Weekly leaderboard reset job completed.")

async def reset_image_broadcasts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Resets the 'image_broadcasts_left' counter for all premium users.
    This job runs once every 24 hours.
    """
    logger.info("Running daily reset for premium image broadcasts...")
    
    await db.reset_all_premium_image_broadcasts()
    
    logger.info("Premium image broadcast limits reset.")

